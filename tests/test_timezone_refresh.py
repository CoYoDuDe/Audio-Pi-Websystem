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


def test_refresh_updates_scheduler_and_timesync(monkeypatch, app_module):
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

    def fake_current_local_time():
        return datetime(2024, 1, 1, 12, 0, 0, tzinfo=new_tz)

    monkeypatch.setattr(app_module, "_current_local_time", fake_current_local_time)

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
    assert configure_calls == [new_tz]
    assert tzset_called is True

    monkeypatch.setattr(
        app_module,
        "_wait_for_system_clock_synchronization",
        lambda: (True, None),
    )

    run_calls = []

    def fake_run(cmd, *args, **kwargs):
        run_calls.append(tuple(cmd) if isinstance(cmd, (list, tuple)) else cmd)
        return app_module.subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    rtc_calls = []

    def fake_set_rtc(dt):
        rtc_calls.append(dt)

    monkeypatch.setattr(app_module, "set_rtc", fake_set_rtc)

    success, messages = app_module.perform_internet_time_sync()

    assert success is True
    assert any("Zeit vom Internet synchronisiert" in msg for msg in messages)
    assert len(refresh_calls) >= 2
    assert rtc_calls and rtc_calls[0].tzinfo is new_tz
    assert run_calls, "Es sollten Kommandos für die Synchronisation ausgeführt worden sein."
