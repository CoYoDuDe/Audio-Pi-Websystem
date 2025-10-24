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


def test_reboot_requires_login(client):
    client, _ = client
    response = csrf_post(client, "/system/reboot", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers.get("Location", "")


def test_shutdown_requires_login(client):
    client, _ = client
    response = csrf_post(client, "/system/shutdown", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers.get("Location", "")


def test_reboot_triggers_run(monkeypatch, client):
    client, app_module = client
    _login(client)

    commands = []
    original_run = app_module.subprocess.run

    class DummyResult:
        def __init__(self):
            self.returncode = 0
            self.stdout = ""
            self.stderr = ""

    def fake_run(cmd, *args, **kwargs):
        if cmd == ["systemctl", "reboot"]:
            commands.append(cmd)
            return DummyResult()
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    response = csrf_post(client, "/system/reboot", follow_redirects=True)

    assert commands == [["systemctl", "reboot"]]
    assert b"Systemneustart eingeleitet." in response.data


def test_shutdown_triggers_run(monkeypatch, client):
    client, app_module = client
    _login(client)

    commands = []
    original_run = app_module.subprocess.run

    class DummyResult:
        def __init__(self):
            self.returncode = 0
            self.stdout = ""
            self.stderr = ""

    def fake_run(cmd, *args, **kwargs):
        if cmd == ["systemctl", "poweroff"]:
            commands.append(cmd)
            return DummyResult()
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    response = csrf_post(client, "/system/shutdown", follow_redirects=True)

    assert commands == [["systemctl", "poweroff"]]
    assert b"Herunterfahren eingeleitet." in response.data


def test_system_command_not_found_flash(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_SECRET_KEY", "testkey")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DB_FILE", str(tmp_path / "test.db"))
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "password")
    monkeypatch.setenv("AUDIO_PI_DISABLE_SUDO", "0")

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

    class DummyResult:
        def __init__(self):
            self.returncode = 1
            self.stdout = ""
            self.stderr = "sudo: systemctl: Befehl nicht gefunden"

    original_run = app_module.subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if cmd == ["sudo", "systemctl", "reboot"]:
            return DummyResult()
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    with app_module.app.test_client() as client:
        _login(client)

        response = csrf_post(
            client,
            "/system/reboot",
            follow_redirects=True,
        )

    if hasattr(app_module, "conn") and app_module.conn is not None:
        app_module.conn.close()
    if "app" in sys.modules:
        del sys.modules["app"]
    if repo_root_str in sys.path:
        sys.path.remove(repo_root_str)

    assert b"Neustart konnte nicht gestartet werden" in response.data
    assert b"systemctl nicht gefunden" in response.data
    assert b"Systemneustart eingeleitet." not in response.data
