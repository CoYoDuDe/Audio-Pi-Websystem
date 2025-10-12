import importlib
import sys
from pathlib import Path

import pytest


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "testkey")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DB_FILE", str(tmp_path / "test.db"))

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
    return client.post(
        "/login",
        data={"username": "admin", "password": "password"},
        follow_redirects=True,
    )


def test_reboot_requires_login(client):
    client, _ = client
    response = client.post("/system/reboot", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers.get("Location", "")


def test_shutdown_requires_login(client):
    client, _ = client
    response = client.post("/system/shutdown", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers.get("Location", "")


def test_reboot_triggers_popen(monkeypatch, client):
    client, app_module = client
    _login(client)

    commands = []
    original_popen = app_module.subprocess.Popen

    class DummyProcess:
        pid = 0

        def communicate(self, *args, **kwargs):
            return (b"", b"")

        def wait(self, *args, **kwargs):
            return 0

        def poll(self):
            return 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_popen(cmd, *args, **kwargs):
        if cmd == ["sudo", "reboot"]:
            commands.append(cmd)
            return DummyProcess()
        return original_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(app_module.subprocess, "Popen", fake_popen)

    response = client.post("/system/reboot", follow_redirects=True)

    assert commands == [["sudo", "reboot"]]
    assert b"Systemneustart eingeleitet." in response.data


def test_shutdown_triggers_popen(monkeypatch, client):
    client, app_module = client
    _login(client)

    commands = []
    original_popen = app_module.subprocess.Popen

    class DummyProcess:
        pid = 0

        def communicate(self, *args, **kwargs):
            return (b"", b"")

        def wait(self, *args, **kwargs):
            return 0

        def poll(self):
            return 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_popen(cmd, *args, **kwargs):
        if cmd == ["sudo", "poweroff"]:
            commands.append(cmd)
            return DummyProcess()
        return original_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(app_module.subprocess, "Popen", fake_popen)

    response = client.post("/system/shutdown", follow_redirects=True)

    assert commands == [["sudo", "poweroff"]]
    assert b"Herunterfahren eingeleitet." in response.data
