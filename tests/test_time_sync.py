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
    return client.post(
        "/login",
        data={"username": "admin", "password": "password"},
        follow_redirects=True,
    )


def test_sync_time_handles_ntp_failure(monkeypatch, client):
    client, app_module = client
    _login(client)

    commands = []
    called_set_rtc = False

    def fake_check_call(cmd, *args, **kwargs):
        commands.append(cmd)
        if cmd == ["sudo", "ntpdate", "pool.ntp.org"]:
            raise app_module.subprocess.CalledProcessError(1, cmd)
        return 0

    def fake_set_rtc(dt):
        nonlocal called_set_rtc
        called_set_rtc = True

    monkeypatch.setattr(app_module.subprocess, "check_call", fake_check_call)
    monkeypatch.setattr(app_module, "set_rtc", fake_set_rtc)

    response = client.get("/sync_time_from_internet", follow_redirects=True)

    assert ["sudo", "systemctl", "stop", "systemd-timesyncd"] in commands
    assert ["sudo", "ntpdate", "pool.ntp.org"] in commands
    assert ["sudo", "systemctl", "start", "systemd-timesyncd"] in commands
    assert b"Fehler bei der Synchronisation" in response.data
    assert called_set_rtc is False


def test_set_time_triggers_internet_sync(monkeypatch, client):
    client, app_module = client
    _login(client)

    original_run = app_module.subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:3] == ["sudo", "date", "-s"]:
            return app_module.subprocess.CompletedProcess(cmd, 0)
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    monkeypatch.setattr(app_module, "set_rtc", lambda dt: None)

    sync_called = {"value": False}

    def fake_sync():
        sync_called["value"] = True
        return True, ["Zeit vom Internet synchronisiert"]

    monkeypatch.setattr(app_module, "perform_internet_time_sync", fake_sync)

    response = client.post(
        "/set_time",
        data={"datetime": "2024-01-01T12:00", "sync_internet": "1"},
        follow_redirects=True,
    )

    assert sync_called["value"] is True
    assert b"Zeit vom Internet synchronisiert" in response.data
    assert app_module.get_setting(app_module.TIME_SYNC_INTERNET_SETTING_KEY, "0") == "1"
