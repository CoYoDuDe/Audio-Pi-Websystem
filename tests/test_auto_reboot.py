import os
import logging
from types import SimpleNamespace
import pytest
from unittest.mock import MagicMock

from tests.csrf_utils import csrf_post

os.environ.setdefault("FLASK_SECRET_KEY", "test")
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "password")

import app  # noqa: E402


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    db_path = tmp_path / "auto-reboot.db"
    if db_path.exists():
        db_path.unlink()
    monkeypatch.setattr(app, "DB_FILE", str(db_path))
    app.initialize_database()
    app.scheduler.remove_all_jobs()
    monkeypatch.setattr(app.pygame.mixer, "music", MagicMock(get_busy=lambda: False))
    yield app
    app.scheduler.remove_all_jobs()


def test_auto_reboot_defaults_inserted(app_module):
    assert app_module.get_setting("auto_reboot_enabled") == "0"
    assert app_module.get_setting("auto_reboot_mode") == "daily"
    assert app_module.get_setting("auto_reboot_time") == "03:00"
    assert app_module.get_setting("auto_reboot_weekday") == "monday"


def test_update_auto_reboot_job_daily_registers_cron(app_module, monkeypatch):
    scheduler_mock = MagicMock()
    monkeypatch.setattr(app_module, "scheduler", scheduler_mock)

    captured_kwargs = {}

    class DummyCron:
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)

    monkeypatch.setattr(app_module, "CronTrigger", lambda **kwargs: DummyCron(**kwargs))

    app_module.set_setting("auto_reboot_enabled", "1")
    app_module.set_setting("auto_reboot_mode", "daily")
    app_module.set_setting("auto_reboot_time", "04:15")

    assert app_module.update_auto_reboot_job() is True
    scheduler_mock.add_job.assert_called_once()
    trigger = scheduler_mock.add_job.call_args[0][1]
    assert isinstance(trigger, DummyCron)
    assert captured_kwargs["hour"] == 4
    assert captured_kwargs["minute"] == 15
    assert captured_kwargs.get("day_of_week") is None


def test_update_auto_reboot_job_weekly_uses_weekday(app_module, monkeypatch):
    scheduler_mock = MagicMock()
    scheduler_mock.get_job.return_value = None
    monkeypatch.setattr(app_module, "scheduler", scheduler_mock)

    captured_kwargs = {}

    class DummyCron:
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)

    monkeypatch.setattr(app_module, "CronTrigger", lambda **kwargs: DummyCron(**kwargs))

    app_module.set_setting("auto_reboot_enabled", "1")
    app_module.set_setting("auto_reboot_mode", "weekly")
    app_module.set_setting("auto_reboot_time", "06:45")
    app_module.set_setting("auto_reboot_weekday", "thursday")

    app_module.update_auto_reboot_job()
    scheduler_mock.add_job.assert_called_once()
    assert captured_kwargs["day_of_week"] == "thursday"


def test_update_auto_reboot_job_disabled_removes_existing(app_module, monkeypatch):
    scheduler_mock = MagicMock()
    scheduler_mock.get_job.return_value = object()
    monkeypatch.setattr(app_module, "scheduler", scheduler_mock)

    app_module.set_setting("auto_reboot_enabled", "0")

    assert app_module.update_auto_reboot_job() is False
    scheduler_mock.remove_job.assert_called_once_with(app_module.AUTO_REBOOT_JOB_ID)
    scheduler_mock.add_job.assert_not_called()


def test_save_auto_reboot_settings_route_updates_values(app_module, monkeypatch):
    update_mock = MagicMock()
    monkeypatch.setattr(app_module, "update_auto_reboot_job", update_mock)

    client = app_module.app.test_client()
    with client:
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
        response = csrf_post(
            client,
            "/settings/auto_reboot",
            data={
                "auto_reboot_enabled": "on",
                "auto_reboot_time": "05:30",
                "auto_reboot_mode": "weekly",
                "auto_reboot_weekday": "friday",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

    assert app_module.get_setting("auto_reboot_enabled") == "1"
    assert app_module.get_setting("auto_reboot_time") == "05:30"
    assert app_module.get_setting("auto_reboot_mode") == "weekly"
    assert app_module.get_setting("auto_reboot_weekday") == "friday"
    update_mock.assert_called_once()


def test_save_auto_reboot_settings_rejects_invalid_time(app_module, monkeypatch):
    update_mock = MagicMock()
    monkeypatch.setattr(app_module, "update_auto_reboot_job", update_mock)

    client = app_module.app.test_client()
    with client:
        csrf_post(
            client,
            "/login",
            data={"username": "admin", "password": "password"},
            follow_redirects=True,
        )
        change_response = csrf_post(
            client,
            "/change_password",
            data={"old_password": "password", "new_password": "password1234"},
            follow_redirects=True,
            source_url="/change_password",
        )
        assert b"Passwort ge\xc3\xa4ndert" in change_response.data
        response = csrf_post(
            client,
            "/settings/auto_reboot",
            data={
                "auto_reboot_enabled": "on",
                "auto_reboot_time": "99:99",
                "auto_reboot_mode": "daily",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

    assert "Ungültige Uhrzeit" in response.get_data(as_text=True)
    assert app_module.get_setting("auto_reboot_enabled") == "0"
    update_mock.assert_not_called()


def test_load_schedules_preserves_auto_reboot_job(app_module):
    scheduler = app_module.scheduler
    scheduler.remove_all_jobs()

    app_module.set_setting("auto_reboot_enabled", "1")
    app_module.set_setting("auto_reboot_mode", "daily")
    app_module.set_setting("auto_reboot_time", "04:00")

    assert app_module.update_auto_reboot_job() is True
    assert scheduler.get_job(app_module.AUTO_REBOOT_JOB_ID) is not None

    app_module.load_schedules()

    assert scheduler.get_job(app_module.AUTO_REBOOT_JOB_ID) is not None


def test_run_auto_reboot_job_missing_systemctl_sudo(app_module, monkeypatch, caplog):
    monkeypatch.setenv("AUDIO_PI_DISABLE_SUDO", "0")
    monkeypatch.setattr(app_module, "_SUDO_DISABLED", False)
    app_module.refresh_subprocess_wrapper_state()

    captured = {}

    def fake_run(command, *, check, capture_output, text):
        captured["command"] = command
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="sudo: systemctl: command not found\n",
        )

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    caplog.set_level(logging.ERROR)

    app_module.run_auto_reboot_job()

    assert captured["command"] == ["sudo", "systemctl", "reboot"]
    assert any(
        "Automatischer Neustart fehlgeschlagen: systemctl nicht gefunden" in message
        for message in (record.getMessage() for record in caplog.records)
    )
