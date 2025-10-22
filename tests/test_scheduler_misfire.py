import importlib
import os
import threading
from datetime import datetime, timedelta

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

os.environ.setdefault('FLASK_SECRET_KEY', 'test')
os.environ.setdefault('TESTING', '1')

app = importlib.import_module('app')


@pytest.fixture(autouse=True)
def clean_database():
    app.cursor.execute("DELETE FROM schedules")
    app.cursor.execute(
        "DELETE FROM settings WHERE key='scheduler_misfire_grace_time'"
    )
    app.conn.commit()
    yield
    app.cursor.execute("DELETE FROM schedules")
    app.cursor.execute(
        "DELETE FROM settings WHERE key='scheduler_misfire_grace_time'"
    )
    app.conn.commit()


@pytest.fixture
def managed_scheduler(monkeypatch):
    scheduler = BackgroundScheduler()
    monkeypatch.setattr(app, "scheduler", scheduler)
    monkeypatch.setattr(app, "_BACKGROUND_SERVICES_STARTED", False, raising=False)
    yield scheduler
    app.stop_background_services(wait=False)
    if scheduler.running:
        scheduler.shutdown(wait=False)
    scheduler.remove_all_jobs()


def _wait_for_execution(event):
    if not event.wait(3):
        pytest.fail("Zeitplan wurde trotz aktivem Misfire-Puffer nicht ausgelöst")


def test_once_schedule_runs_after_delay(monkeypatch, managed_scheduler):
    executed = threading.Event()
    monkeypatch.setattr(app, "play_item", lambda *args, **kwargs: executed.set())

    run_time = datetime.now() + timedelta(seconds=1)
    app.cursor.execute(
        """
        INSERT INTO schedules (item_id, item_type, time, repeat, delay, start_date, end_date, day_of_month, executed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (1, "file", run_time.strftime("%Y-%m-%d %H:%M:%S"), "once", 0, None, None, None),
    )
    app.conn.commit()
    schedule_id = app.cursor.lastrowid

    app.start_background_services()
    job = managed_scheduler.get_job(str(schedule_id))
    assert job is not None
    managed_scheduler.modify_job(
        job.id, next_run_time=datetime.now() - timedelta(seconds=2)
    )

    _wait_for_execution(executed)


def test_recurring_schedule_runs_after_delay(monkeypatch, managed_scheduler):
    executed = threading.Event()
    monkeypatch.setattr(app, "play_item", lambda *args, **kwargs: executed.set())

    target_time = datetime.now() + timedelta(seconds=1)
    app.cursor.execute(
        """
        INSERT INTO schedules (item_id, item_type, time, repeat, delay, start_date, end_date, day_of_month, executed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            2,
            "file",
            target_time.strftime("%H:%M:%S"),
            "daily",
            0,
            target_time.date().isoformat(),
            None,
            None,
        ),
    )
    app.conn.commit()
    schedule_id = app.cursor.lastrowid

    app.start_background_services()
    job = managed_scheduler.get_job(str(schedule_id))
    assert job is not None
    managed_scheduler.modify_job(
        job.id, next_run_time=datetime.now() - timedelta(seconds=2)
    )

    _wait_for_execution(executed)
