import importlib
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

    app_module.pygame.mixer.music.get_busy = lambda: False

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    app_module.app.config["UPLOAD_FOLDER"] = str(upload_dir)

    yield app_module

    if hasattr(app_module, "conn") and app_module.conn is not None:
        app_module.conn.close()
    if "app" in sys.modules:
        del sys.modules["app"]
    if repo_root_str in sys.path:
        sys.path.remove(repo_root_str)


@pytest.fixture
def client(app_module):
    with app_module.app.test_client() as client:
        yield client, app_module


def _login(client):
    response = csrf_post(
        client,
        "/login",
        data={"username": "admin", "password": "password"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    change_response = csrf_post(
        client,
        "/change_password",
        data={"old_password": "password", "new_password": "password1234"},
        follow_redirects=True,
        source_url="/change_password",
    )
    assert b"Passwort ge\xc3\xa4ndert" in change_response.data
    return change_response


def test_set_time_handles_called_process_error(monkeypatch, client):
    client, app_module = client
    _login(client)

    set_rtc_called = False

    def fake_set_rtc(dt):
        nonlocal set_rtc_called
        set_rtc_called = True

    def fake_run(cmd, *args, **kwargs):
        assert kwargs.get("check") is True
        raise app_module.subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(app_module, "set_rtc", fake_set_rtc)
    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    monkeypatch.setattr(app_module.subprocess, "getoutput", lambda *args, **kwargs: "")

    response = csrf_post(
        client,
        "/set_time",
        data={"datetime": "2024-01-01T12:00:00"},
        follow_redirects=True,
    )

    assert b"Systemzeit konnte nicht gesetzt werden" in response.data
    assert b"Datum und Uhrzeit gesetzt" not in response.data
    assert set_rtc_called is False
