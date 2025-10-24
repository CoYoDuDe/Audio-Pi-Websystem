import errno
import importlib
import os
import sys
from pathlib import Path

import pytest

from tests.csrf_utils import csrf_post
from tests.test_set_time import _login, app_module, client  # noqa: F401


@pytest.fixture
def sudo_app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIO_PI_DISABLE_SUDO", "0")
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
def sudo_client(sudo_app_module):
    with sudo_app_module.app.test_client() as client:
        yield client, sudo_app_module


def test_set_time_handles_missing_command(monkeypatch, client):
    client, app_module = client
    _login(client)

    set_rtc_called = False

    def fake_set_rtc(dt):
        nonlocal set_rtc_called
        set_rtc_called = True

    def fake_run(cmd, *args, **kwargs):
        assert kwargs.get("check") is True
        assert kwargs.get("capture_output") is True
        assert kwargs.get("text") is True
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), cmd[0])

    monkeypatch.setattr(app_module, "set_rtc", fake_set_rtc)
    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    monkeypatch.setattr(app_module.subprocess, "getoutput", lambda *args, **kwargs: "")

    response = csrf_post(
        client,
        "/set_time",
        data={"datetime": "2024-01-01T12:00:00"},
        follow_redirects=True,
    )

    expected_message = (
        "Kommando &#39;timedatectl&#39; wurde nicht gefunden. Systemzeit konnte nicht gesetzt werden."
    )
    assert expected_message.encode("utf-8") in response.data
    assert set_rtc_called is False


def test_set_time_handles_missing_command_via_called_process_error(
    monkeypatch, sudo_client
):
    client, app_module = sudo_client
    _login(client)

    set_rtc_called = False

    def fake_set_rtc(dt):
        nonlocal set_rtc_called
        set_rtc_called = True

    def fake_run(cmd, *args, **kwargs):
        assert kwargs.get("check") is True
        assert kwargs.get("capture_output") is True
        assert kwargs.get("text") is True
        raise app_module.subprocess.CalledProcessError(
            1,
            cmd,
            stderr="sudo: timedatectl: command not found",
            output="",
        )

    monkeypatch.setattr(app_module, "set_rtc", fake_set_rtc)
    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    monkeypatch.setattr(app_module.subprocess, "getoutput", lambda *args, **kwargs: "")

    response = csrf_post(
        client,
        "/set_time",
        data={"datetime": "2024-01-01T12:00:00"},
        follow_redirects=True,
        source_url="/set_time",
    )

    expected_message = (
        "Kommando &#39;timedatectl&#39; wurde nicht gefunden. Systemzeit konnte nicht gesetzt werden."
    )
    assert expected_message.encode("utf-8") in response.data
    assert set_rtc_called is False
