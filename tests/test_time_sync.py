import importlib
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.csrf_utils import csrf_post


@contextmanager
def _app_module_context(tmp_path, monkeypatch, sudo_flag: str):
    monkeypatch.setenv("AUDIO_PI_DISABLE_SUDO", sudo_flag)
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

    try:
        yield app_module
    finally:
        if hasattr(app_module, "conn") and app_module.conn is not None:
            app_module.conn.close()
        if "app" in sys.modules:
            del sys.modules["app"]
        if repo_root_str in sys.path:
            sys.path.remove(repo_root_str)


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    with _app_module_context(tmp_path, monkeypatch, "1") as module:
        yield module


@pytest.fixture
def sudo_app_module(tmp_path, monkeypatch):
    with _app_module_context(tmp_path, monkeypatch, "0") as module:
        yield module


@pytest.fixture
def client(app_module):
    with app_module.app.test_client() as client:
        yield client, app_module


@pytest.fixture
def sudo_client(sudo_app_module):
    with sudo_app_module.app.test_client() as client:
        yield client, sudo_app_module


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


def test_sync_time_handles_timesyncd_failure(monkeypatch, client):
    client, app_module = client
    _login(client)

    commands = []
    called_set_rtc = False
    restart_failure_triggered = {"value": False}

    def fake_run(cmd, *args, **kwargs):
        assert kwargs.get("check") is True
        assert kwargs.get("capture_output") is True
        assert kwargs.get("text") is True
        commands.append(cmd)
        restart_command = app_module.privileged_command(
            "systemctl", "restart", "systemd-timesyncd"
        )
        if cmd == restart_command and not restart_failure_triggered["value"]:
            restart_failure_triggered["value"] = True
            raise app_module.subprocess.CalledProcessError(
                1,
                cmd,
                stderr="systemctl: service failed",
                output="",
            )
        return app_module.subprocess.CompletedProcess(cmd, 0, "", "")

    def fake_set_rtc(dt):
        nonlocal called_set_rtc
        called_set_rtc = True

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    monkeypatch.setattr(app_module, "set_rtc", fake_set_rtc)

    response = csrf_post(
        client,
        "/sync_time_from_internet",
        follow_redirects=True,
    )

    disable_command = app_module.privileged_command(
        "timedatectl", "set-ntp", "false"
    )
    enable_command = app_module.privileged_command(
        "timedatectl", "set-ntp", "true"
    )
    restart_command = app_module.privileged_command(
        "systemctl", "restart", "systemd-timesyncd"
    )
    assert disable_command in commands
    assert enable_command in commands
    assert commands.count(restart_command) >= 1
    assert b"Fehler bei der Synchronisation" in response.data
    assert called_set_rtc is False


def test_sync_time_rejects_get_after_login(client):
    client, _ = client
    _login(client)

    response = client.get("/sync_time_from_internet")

    assert response.status_code == 405


def test_set_time_triggers_internet_sync(monkeypatch, client):
    client, app_module = client
    _login(client)

    original_run = app_module.subprocess.run

    def fake_run(cmd, *args, **kwargs):
        expected_prefix = app_module.privileged_command("timedatectl", "set-time")
        if isinstance(cmd, list) and cmd[:2] == expected_prefix[:2]:
            return app_module.subprocess.CompletedProcess(cmd, 0)
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    monkeypatch.setattr(app_module, "set_rtc", lambda dt: None)

    sync_called = {"value": False}

    def fake_sync():
        sync_called["value"] = True
        return True, ["Zeit vom Internet synchronisiert"]

    monkeypatch.setattr(app_module, "perform_internet_time_sync", fake_sync)

    response = csrf_post(
        client,
        "/set_time",
        data={"datetime": "2024-01-01T12:00", "sync_internet": "1"},
        follow_redirects=True,
    )

    assert sync_called["value"] is True
    assert b"Zeit vom Internet synchronisiert" in response.data
    assert app_module.get_setting(app_module.TIME_SYNC_INTERNET_SETTING_KEY, "0") == "1"


def test_perform_internet_time_sync_handles_missing_systemctl(monkeypatch, app_module):
    commands = []

    def fake_run(cmd, *args, **kwargs):
        assert kwargs.get("check") is True
        assert kwargs.get("capture_output") is True
        assert kwargs.get("text") is True
        commands.append(cmd)
        restart_command = app_module.privileged_command(
            "systemctl", "restart", "systemd-timesyncd"
        )
        if cmd == restart_command:
            raise FileNotFoundError(2, "No such file or directory", cmd[0])
        return app_module.subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    monkeypatch.setattr(app_module, "set_rtc", lambda dt: None)

    success, messages = app_module.perform_internet_time_sync()

    disable_command = app_module.privileged_command(
        "timedatectl", "set-ntp", "false"
    )
    enable_command = app_module.privileged_command(
        "timedatectl", "set-ntp", "true"
    )
    restart_command = app_module.privileged_command(
        "systemctl", "restart", "systemd-timesyncd"
    )
    assert disable_command in commands
    assert enable_command in commands
    assert commands.count(restart_command) >= 1
    assert success is False
    assert any(
        "kommando 'systemctl' nicht gefunden" in message.lower()
        for message in messages
    )


def test_perform_internet_time_sync_handles_missing_timedatectl(monkeypatch, app_module):
    commands = []
    rtc_called = False

    def fake_run(cmd, *args, **kwargs):
        assert kwargs.get("check") is True
        assert kwargs.get("capture_output") is True
        assert kwargs.get("text") is True
        commands.append(cmd)
        disable_cmd = app_module.privileged_command(
            "timedatectl", "set-ntp", "false"
        )
        if cmd == disable_cmd:
            raise FileNotFoundError(2, "No such file or directory", cmd[0])
        return app_module.subprocess.CompletedProcess(cmd, 0, "", "")

    def fake_set_rtc(dt):
        nonlocal rtc_called
        rtc_called = True

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    monkeypatch.setattr(app_module, "set_rtc", fake_set_rtc)

    success, messages = app_module.perform_internet_time_sync()

    assert app_module.privileged_command(
        "timedatectl", "set-ntp", "false"
    ) in commands
    assert rtc_called is False
    assert success is False
    assert any(
        "Kommando 'timedatectl' nicht gefunden, Internet-Sync abgebrochen" in message
        for message in messages
    )


def test_perform_internet_time_sync_handles_sudo_missing_timedatectl(
    monkeypatch, sudo_app_module
):
    commands = []
    rtc_called = False

    def fake_run(cmd, *args, **kwargs):
        assert kwargs.get("check") is True
        assert kwargs.get("capture_output") is True
        assert kwargs.get("text") is True
        commands.append(cmd)
        disable_cmd = sudo_app_module.privileged_command(
            "timedatectl", "set-ntp", "false"
        )
        if cmd == disable_cmd:
            raise sudo_app_module.subprocess.CalledProcessError(
                127,
                cmd,
                stderr="sudo: timedatectl: Befehl nicht gefunden",
                output="",
            )
        return sudo_app_module.subprocess.CompletedProcess(cmd, 0, "", "")

    def fake_set_rtc(dt):
        nonlocal rtc_called
        rtc_called = True

    monkeypatch.setattr(sudo_app_module.subprocess, "run", fake_run)
    monkeypatch.setattr(sudo_app_module, "set_rtc", fake_set_rtc)

    success, messages = sudo_app_module.perform_internet_time_sync()

    disable_command = sudo_app_module.privileged_command(
        "timedatectl", "set-ntp", "false"
    )
    assert disable_command in commands
    assert success is False
    assert rtc_called is False
    assert any(
        "kommando 'timedatectl' nicht gefunden" in message.lower()
        for message in messages
    )
