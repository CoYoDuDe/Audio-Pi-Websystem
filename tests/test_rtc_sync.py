import importlib
import logging
import sys
from datetime import datetime
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

    yield app_module

    if "app" in sys.modules:
        del sys.modules["app"]
    if repo_root_str in sys.path:
        sys.path.remove(repo_root_str)


def test_sync_rtc_logs_error_on_subprocess_failure(app_module, monkeypatch, caplog):
    fake_time = datetime(2024, 1, 2, 3, 4, 5)
    monkeypatch.setattr(app_module, "read_rtc", lambda: fake_time)

    executed_commands = []

    def fake_run(cmd, *args, **kwargs):
        executed_commands.append((cmd, kwargs))
        assert kwargs.get("check") is True
        assert kwargs.get("capture_output") is True
        assert kwargs.get("text") is True
        raise app_module.subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    caplog.clear()
    with caplog.at_level(logging.INFO):
        result = app_module.sync_rtc_to_system()

    assert executed_commands == [
        (
            app_module.privileged_command(
                "timedatectl", "set-time", "2024-01-02 03:04:05"
            ),
            {"check": True, "capture_output": True, "text": True},
        )
    ]

    error_messages = [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]
    assert any("RTC-Sync fehlgeschlagen" in message for message in error_messages)

    success_messages = [record.getMessage() for record in caplog.records if "RTC auf Systemzeit synchronisiert" in record.getMessage()]
    assert not success_messages
    assert result is False


def test_sync_rtc_failure_does_not_raise_system_exit(app_module, monkeypatch):
    fake_time = datetime(2025, 5, 4, 3, 2, 1)
    monkeypatch.setattr(app_module, "read_rtc", lambda: fake_time)

    def fake_run(cmd, *args, **kwargs):
        assert kwargs.get("check") is True
        raise app_module.subprocess.CalledProcessError(5, cmd)

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    try:
        result = app_module.sync_rtc_to_system()
    except SystemExit:  # pragma: no cover - sollte nicht passieren
        pytest.fail("sync_rtc_to_system darf keinen SystemExit auslösen")

    assert result is False
    assert app_module.RTC_SYNC_STATUS["success"] is False
    assert "Rückgabecode" in app_module.RTC_SYNC_STATUS["last_error"]
