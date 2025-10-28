import importlib
import re
import sys
from pathlib import Path

import pytest

from tests.csrf_utils import csrf_post


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "testkey")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DB_FILE", str(tmp_path / "test.db"))
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "password")

    repo_root = Path(__file__).resolve().parents[1]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)
    if "app" in sys.modules:
        del sys.modules["app"]

    app_module = importlib.import_module("app")
    importlib.reload(app_module)

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    app_module.app.config["UPLOAD_FOLDER"] = str(upload_dir)
    if hasattr(app_module, "pygame_available"):
        app_module.pygame_available = False
    if hasattr(app_module, "pygame"):
        app_module.pygame = None

    yield app_module

    if hasattr(app_module, "conn") and app_module.conn is not None:
        app_module.conn.close()
    if "app" in sys.modules:
        del sys.modules["app"]
    if repo_root_str in sys.path:
        sys.path.remove(repo_root_str)


def test_schedule_form_contains_server_rendered_options(app_module):
    app_module.cursor.execute("DELETE FROM audio_files")
    app_module.cursor.execute("DELETE FROM playlists")
    app_module.conn.commit()

    app_module.cursor.execute(
        "INSERT INTO audio_files (filename, duration_seconds) VALUES (?, ?)",
        ("alarm.mp3", 3.5),
    )
    file_id = app_module.cursor.lastrowid
    app_module.conn.commit()

    with app_module.app.test_client() as client:
        login_response = csrf_post(
            client,
            "/login",
            data={"username": "admin", "password": "password"},
            follow_redirects=True,
            source_url="/login",
        )
        assert login_response.status_code == 200

        change_response = csrf_post(
            client,
            "/change_password",
            data={"old_password": "password", "new_password": "password1234"},
            follow_redirects=True,
            source_url="/change_password",
        )
        assert change_response.status_code == 200

        response = client.get("/")
        assert response.status_code == 200
        html = response.get_data(as_text=True)

        select_match = re.search(r'<select[^>]*id="item-select"[^>]*>(.*?)</select>', html, re.S)
        assert select_match, "Die Select-Box für Zeitpläne fehlt im gerenderten HTML"

        options_html = select_match.group(1)
        assert f'value="{file_id}"' in options_html

        file_options = re.findall(r'<option[^>]*data-item-type="file"[^>]*>', options_html)
        assert file_options, "Es muss mindestens eine Datei-Option serverseitig vorhanden sein"

        selected_match = re.search(
            r'<option[^>]*data-item-type="file"[^>]*selected[^>]*>',
            options_html,
        )
        assert selected_match, "Die Standard-Auswahl soll auf eine Datei-Option zeigen"
