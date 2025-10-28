import importlib
import sys
from datetime import datetime, timedelta, timezone
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


def test_refresh_updates_scheduler_and_rtc_sync(monkeypatch, app_module):
    new_tz = timezone(timedelta(hours=5), name="UTC+05")

    configure_calls = []

    def fake_configure(*, timezone):
        configure_calls.append(timezone)

    monkeypatch.setattr(app_module.scheduler, "configure", fake_configure)

    tzset_called = False

    def fake_tzset():
        nonlocal tzset_called
        tzset_called = True

    monkeypatch.setattr(app_module.time, "tzset", fake_tzset, raising=False)

    real_datetime = datetime

    class _FakeNow:
        def __init__(self, tzinfo):
            self.tzinfo = tzinfo

        def astimezone(self, tz=None):
            if tz is None:
                return self
            return real_datetime(2024, 1, 1, 12, 0, tzinfo=self.tzinfo).astimezone(tz)

    class DateTimeProxy:
        def __call__(self, *args, **kwargs):
            return real_datetime(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(real_datetime, name)

        @staticmethod
        def now(tz=None):
            if tz is not None:
                return real_datetime.now(tz)
            return _FakeNow(new_tz)

    monkeypatch.setattr(app_module, "datetime", DateTimeProxy())

    app_module.LOCAL_TZ = timezone.utc

    original_refresh = app_module.refresh_local_timezone
    refresh_calls = []

    def wrapped_refresh(*args, **kwargs):
        refresh_calls.append((args, kwargs))
        return original_refresh(*args, **kwargs)

    monkeypatch.setattr(app_module, "refresh_local_timezone", wrapped_refresh)

    result_tz = app_module.refresh_local_timezone()

    assert result_tz == new_tz
    assert app_module.LOCAL_TZ == new_tz
    assert configure_calls and configure_calls[0] == new_tz
    assert tzset_called is True

    rtc_timestamp = datetime(2024, 1, 1, 7, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(app_module, "read_rtc", lambda: rtc_timestamp)

    run_calls = []

    def fake_run(command, *args, **kwargs):
        run_calls.append(command)
        return app_module.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    assert app_module.sync_rtc_to_system() is True

    expected_local_time = rtc_timestamp.astimezone(new_tz).strftime("%Y-%m-%d %H:%M:%S")
    assert run_calls, "timedatectl sollte aufgerufen worden sein."
    set_time_command = run_calls[0]
    assert isinstance(set_time_command, list)
    assert set_time_command[:2] == ["timedatectl", "set-time"]
    assert set_time_command[2] == expected_local_time

    assert app_module.RTC_SYNC_STATUS["success"] is True
    assert len(refresh_calls) >= 2
    assert configure_calls[-1] == new_tz
