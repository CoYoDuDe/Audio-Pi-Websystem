import os
import io
import sqlite3
import importlib
import sys
from pathlib import Path
import tempfile

import pytest

from tests.csrf_utils import csrf_post

# Hilfsfixture zum Laden der App mit Test-Einstellungen
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "testkey")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DB_FILE", str(tmp_path / "test.db"))
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "password")
    monkeypatch.setenv("AUDIO_PI_MAX_UPLOAD_MB", "1")

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

    class DummySegment:
        def __init__(self, duration_ms=1234):
            self._duration_ms = duration_ms

        def __len__(self):
            return self._duration_ms

        def normalize(self, headroom=0.1):
            return self

        def export(self, *args, **kwargs):
            pass

    def fake_from_file(*args, **kwargs):
        return DummySegment()

    monkeypatch.setattr(
        app_module.AudioSegment,
        "from_file",
        staticmethod(fake_from_file),
    )

    with app_module.app.test_client() as client:
        yield client, upload_dir, app_module

    app_module.conn.close()

def test_upload_twice_generates_new_name(client):
    client, upload_dir, app_module = client

    # Zunächst einloggen
    login_data = {"username": "admin", "password": "password"}
    response = csrf_post(client, "/login", data=login_data, follow_redirects=True)
    assert response.status_code == 200
    change_response = csrf_post(
        client,
        "/change_password",
        data={"old_password": "password", "new_password": "password1234"},
        follow_redirects=True,
        source_url="/change_password",
    )
    assert b"Passwort ge\xc3\xa4ndert" in change_response.data

    def make_data():
        return {"file": (io.BytesIO(b"data"), "song.mp3")}

    res1 = csrf_post(client, "/upload", data=make_data(), follow_redirects=True)
    assert b"hochgeladen" in res1.data
    files = sorted(upload_dir.iterdir())
    assert len(files) == 1
    first_name = files[0].name
    assert first_name == "song.mp3"

    res2 = csrf_post(client, "/upload", data=make_data(), follow_redirects=True)
    assert b"bereits vorhanden" in res2.data
    files = sorted(upload_dir.iterdir())
    assert len(files) == 2
    second_name = files[1].name
    assert second_name != first_name

    conn = sqlite3.connect(app_module.DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT filename, duration_seconds FROM audio_files")
    rows = cursor.fetchall()
    db_names = {row[0] for row in rows}
    for _, duration in rows:
        assert duration == pytest.approx(1.234, rel=1e-3)
    conn.close()
    assert {first_name, second_name} == db_names


def test_upload_same_second_does_not_overwrite(client, monkeypatch):
    client, upload_dir, app_module = client

    login_data = {"username": "admin", "password": "password"}
    response = csrf_post(client, "/login", data=login_data, follow_redirects=True)
    assert response.status_code == 200
    change_response = csrf_post(
        client,
        "/change_password",
        data={"old_password": "password", "new_password": "password1234"},
        follow_redirects=True,
        source_url="/change_password",
    )
    assert b"Passwort ge\xc3\xa4ndert" in change_response.data

    original_datetime = app_module.datetime

    fixed_now = original_datetime(2024, 1, 1, 12, 0, 0)

    class FixedDateTime(original_datetime):
        @classmethod
        def now(cls, tz=None):  # pragma: no cover - wird indirekt verwendet
            return fixed_now

    monkeypatch.setattr(app_module, "datetime", FixedDateTime)

    def make_data():
        return {"file": (io.BytesIO(b"data"), "song.mp3")}

    res1 = csrf_post(client, "/upload", data=make_data(), follow_redirects=True)
    assert b"hochgeladen" in res1.data

    timestamp_label = FixedDateTime.now().strftime("%Y%m%d_%H%M%S")
    expected_second = f"song_{timestamp_label}.mp3"
    expected_third = f"song_{timestamp_label}_2.mp3"

    res2 = csrf_post(client, "/upload", data=make_data(), follow_redirects=True)
    assert b"(Versuch 1)" in res2.data
    assert (upload_dir / expected_second).exists()

    res3 = csrf_post(client, "/upload", data=make_data(), follow_redirects=True)
    assert b"(Versuch 2)" in res3.data
    assert (upload_dir / expected_third).exists()

    files = sorted(p.name for p in upload_dir.iterdir())
    assert {"song.mp3", expected_second, expected_third} == set(files)

    conn = sqlite3.connect(app_module.DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT filename FROM audio_files")
    rows = cursor.fetchall()
    conn.close()

    db_names = {row[0] for row in rows}
    assert {"song.mp3", expected_second, expected_third} == db_names


def test_upload_rejects_too_large_file(client):
    client, upload_dir, app_module = client

    login_data = {"username": "admin", "password": "password"}
    response = csrf_post(client, "/login", data=login_data, follow_redirects=True)
    assert response.status_code == 200

    change_response = csrf_post(
        client,
        "/change_password",
        data={"old_password": "password", "new_password": "password1234"},
        follow_redirects=True,
        source_url="/change_password",
    )
    assert b"Passwort ge\xc3\xa4ndert" in change_response.data

    limit_mb = app_module.app.config.get("MAX_CONTENT_LENGTH_MB")
    limit_bytes = app_module.app.config.get("MAX_CONTENT_LENGTH")
    assert limit_mb == 1
    assert isinstance(limit_bytes, int)

    oversized_file = io.BytesIO(b"a" * (limit_bytes + 1))
    data = {"file": (oversized_file, "too_large.mp3")}

    response = csrf_post(client, "/upload", data=data, follow_redirects=False)
    assert response.status_code == 413

    body = response.get_data(as_text=True)
    assert "überschreitet das erlaubte Limit" in body
    assert "1&nbsp;MB" in body

    assert list(upload_dir.iterdir()) == []

    conn = sqlite3.connect(app_module.DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM audio_files")
    count = cursor.fetchone()[0]
    conn.close()
    assert count == 0
