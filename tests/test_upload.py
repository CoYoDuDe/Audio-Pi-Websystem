import os
import io
import sqlite3
import importlib
import sys
from pathlib import Path
import tempfile

import pytest

# Hilfsfixture zum Laden der App mit Test-Einstellungen
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "testkey")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DB_FILE", str(tmp_path / "test.db"))

    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    if "app" in sys.modules:
        del sys.modules["app"]
    import app as app_module
    importlib.reload(app_module)

    # pygame im Test deaktivieren
    app_module.pygame.mixer.music.get_busy = lambda: False

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    app_module.app.config["UPLOAD_FOLDER"] = str(upload_dir)

    with app_module.app.test_client() as client:
        yield client, upload_dir, app_module

    app_module.conn.close()

def test_upload_twice_generates_new_name(client):
    client, upload_dir, app_module = client

    # Zunächst einloggen
    login_data = {"username": "admin", "password": "password"}
    client.post("/login", data=login_data, follow_redirects=True)

    data = {"file": (io.BytesIO(b"data"), "song.mp3")}
    res1 = client.post("/upload", data=data, follow_redirects=True)
    assert b"hochgeladen" in res1.data
    files = sorted(upload_dir.iterdir())
    assert len(files) == 1
    first_name = files[0].name
    assert first_name == "song.mp3"

    data = {"file": (io.BytesIO(b"data"), "song.mp3")}
    res2 = client.post("/upload", data=data, follow_redirects=True)
    assert b"bereits vorhanden" in res2.data
    files = sorted(upload_dir.iterdir())
    assert len(files) == 2
    second_name = files[1].name
    assert second_name != first_name

    conn = sqlite3.connect(app_module.DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT filename FROM audio_files")
    db_names = {row[0] for row in cursor.fetchall()}
    conn.close()
    assert {first_name, second_name} == db_names
