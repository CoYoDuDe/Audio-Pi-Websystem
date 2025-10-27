import importlib
import sys
from pathlib import Path

import pytest

from tests.csrf_utils import csrf_post


class _FailingBus:
    def write_i2c_block_data(self, *args, **kwargs):
        raise OSError("Simulierter I²C-Fehler")


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


def _prepare_rtc_failure(app_module):
    app_module.bus = _FailingBus()
    app_module.RTC_AVAILABLE = True
    app_module.RTC_ADDRESS = 0x51
    app_module.RTC_DETECTED_ADDRESS = 0x51
    app_module.RTC_MISSING_FLAG = False


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


def test_perform_internet_time_sync_handles_i2c_write_error(monkeypatch, app_module):
    _prepare_rtc_failure(app_module)

    commands = []

    def fake_run(cmd, *args, **kwargs):
        commands.append((cmd, kwargs))
        assert kwargs.get("check") is True
        return app_module.subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    success, messages = app_module.perform_internet_time_sync()

    expected_calls = {
        tuple(app_module.privileged_command("timedatectl", "set-ntp", "false")),
        tuple(app_module.privileged_command("timedatectl", "set-ntp", "true")),
        tuple(app_module.privileged_command("systemctl", "restart", "systemd-timesyncd")),
    }

    recorded_commands = {
        tuple(command)
        for command, kwargs in commands
        if kwargs.get("check") is True
    }

    assert expected_calls.issubset(recorded_commands)
    assert success is False
    assert "RTC konnte nicht aktualisiert werden (I²C-Schreibfehler)" in messages


def test_set_time_handles_i2c_write_error(monkeypatch, client):
    client, app_module = client
    _login(client)
    _prepare_rtc_failure(app_module)

    executed_commands = []

    def fake_run(cmd, *args, **kwargs):
        executed_commands.append((cmd, kwargs))
        if isinstance(cmd, (list, tuple)) and list(cmd)[:2] == [
            "timedatectl",
            "set-time",
        ]:
            assert kwargs.get("check") is True
            return app_module.subprocess.CompletedProcess(cmd, 0)
        return app_module.subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    monkeypatch.setattr(app_module.subprocess, "getoutput", lambda *args, **kwargs: "")

    response = csrf_post(
        client,
        "/set_time",
        data={"datetime": "2024-01-01T12:00:00"},
        follow_redirects=True,
        source_url="/set_time",
    )

    assert any(
        tuple(command)[:2] == ("timedatectl", "set-time") and kwargs.get("check") is True
        for command, kwargs in executed_commands
    )

    expected = "RTC konnte nicht gesetzt werden (I²C-Schreibfehler)"
    assert expected.encode("utf-8") in response.data
    assert response.status_code == 200
