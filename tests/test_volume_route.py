import importlib
import sys
from pathlib import Path

import pytest


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
    response = client.post(
        "/login",
        data={"username": "admin", "password": "password"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    change_response = client.post(
        "/change_password",
        data={"old_password": "password", "new_password": "password1234"},
        follow_redirects=True,
    )
    assert b"Passwort ge\xc3\xa4ndert" in change_response.data
    return change_response


def test_volume_command_failure(monkeypatch, client):
    client, app_module = client
    _login(client)

    monkeypatch.setattr(app_module, "get_current_sink", lambda: "test-sink")
    monkeypatch.setattr(app_module.pygame.mixer.music, "set_volume", lambda value: None)

    commands = []

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list):
            commands.append(cmd)
            if cmd and cmd[0] == "pactl":
                raise app_module.subprocess.CalledProcessError(
                    returncode=1,
                    cmd=cmd,
                    output="error",
                    stderr="fail",
                )
            return app_module.subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return app_module.subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    response = client.post("/volume", data={"volume": "50"}, follow_redirects=True)

    assert response.status_code == 200
    assert b"Fehler beim Setzen der Lautst\xc3\xa4rke" in response.data
    assert b"Lautst\xc3\xa4rke persistent gesetzt" not in response.data
    assert commands == [["pactl", "set-sink-volume", "test-sink", "50%"]]
