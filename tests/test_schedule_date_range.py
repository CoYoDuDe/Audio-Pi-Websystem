import os
import importlib
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

import pytest
from flask import get_flashed_messages

os.environ.setdefault('FLASK_SECRET_KEY', 'test')
os.environ.setdefault('TESTING', '1')

app = importlib.import_module('app')


@pytest.fixture(autouse=True)
def cleanup_schedules():
    app.scheduler.remove_all_jobs()
    app.cursor.execute('DELETE FROM schedules')
    app.conn.commit()
    yield
    app.scheduler.remove_all_jobs()
    app.cursor.execute('DELETE FROM schedules')
    app.conn.commit()


def _insert_recurring_schedule(start_date, end_date):
    app.cursor.execute(
        """
        INSERT INTO schedules (item_id, item_type, time, repeat, delay, start_date, end_date, day_of_month, executed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            99,
            'file',
            '00:00:00',
            'daily',
            0,
            start_date.isoformat() if start_date else None,
            end_date.isoformat() if end_date else None,
            None,
        ),
    )
    app.conn.commit()
    return app.cursor.lastrowid


def _insert_monthly_schedule(day_of_month, start_date, end_date=None):
    app.cursor.execute(
        """
        INSERT INTO schedules (item_id, item_type, time, repeat, delay, start_date, end_date, day_of_month, executed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            99,
            'file',
            '08:00:00',
            'monthly',
            0,
            start_date.isoformat() if start_date else None,
            end_date.isoformat() if end_date else None,
            day_of_month,
        ),
    )
    app.conn.commit()
    return app.cursor.lastrowid


def _insert_once_schedule(run_time):
    app.cursor.execute(
        """
        INSERT INTO schedules (item_id, item_type, time, repeat, delay, start_date, end_date, day_of_month, executed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            42,
            'file',
            run_time.isoformat(timespec='seconds'),
            'once',
            0,
            None,
            None,
            None,
        ),
    )
    app.conn.commit()
    return app.cursor.lastrowid


def test_recurring_schedule_runs_within_date_range(monkeypatch):
    today = datetime.now().date()
    schedule_id = _insert_recurring_schedule(today, today)
    calls = []
    monkeypatch.setattr(app, 'play_item', lambda *args, **kwargs: calls.append((args, kwargs)))

    app.schedule_job(schedule_id)

    assert calls, 'Wiedergabe sollte innerhalb des Zeitfensters ausgelöst werden'


def test_recurring_schedule_skipped_after_end_date(monkeypatch):
    today = datetime.now().date()
    end_date = today - timedelta(days=1)
    schedule_id = _insert_recurring_schedule(today - timedelta(days=5), end_date)
    calls = []
    monkeypatch.setattr(app, 'play_item', lambda *args, **kwargs: calls.append((args, kwargs)))

    app.schedule_job(schedule_id)

    assert not calls, 'Wiedergabe darf nach dem Enddatum nicht stattfinden'
    row = app.cursor.execute('SELECT executed FROM schedules WHERE id=?', (schedule_id,)).fetchone()
    assert row['executed'] == 0


def test_recurring_schedule_skipped_before_start(monkeypatch):
    today = datetime.now().date()
    start_date = today + timedelta(days=2)
    schedule_id = _insert_recurring_schedule(start_date, start_date + timedelta(days=10))
    calls = []
    monkeypatch.setattr(app, 'play_item', lambda *args, **kwargs: calls.append((args, kwargs)))

    app.schedule_job(schedule_id)

    assert not calls, 'Wiedergabe darf vor dem Startdatum nicht stattfinden'


def test_add_schedule_stores_day_of_month():
    app.cursor.execute('DELETE FROM audio_files')
    app.cursor.execute("INSERT INTO audio_files (filename) VALUES (?)", ('probe.mp3',))
    file_id = app.cursor.lastrowid
    app.conn.commit()

    with app.app.test_request_context(
        '/schedule',
        method='POST',
        data={
            'item_type': 'file',
            'item_id': str(file_id),
            'time': '2024-01-30T08:15',
            'repeat': 'monthly',
            'delay': '0',
            'start_date': '',
            'end_date': '',
        },
    ):
        response = app.add_schedule.__wrapped__()
        assert response.status_code == 302

    row = app.cursor.execute(
        'SELECT day_of_month FROM schedules ORDER BY id DESC LIMIT 1'
    ).fetchone()
    assert row['day_of_month'] == 30


def test_add_schedule_rejects_impossible_month_range():
    app.cursor.execute('DELETE FROM audio_files')
    app.cursor.execute("INSERT INTO audio_files (filename) VALUES (?)", ('probe.mp3',))
    file_id = app.cursor.lastrowid
    app.conn.commit()

    with app.app.test_request_context(
        '/schedule',
        method='POST',
        data={
            'item_type': 'file',
            'item_id': str(file_id),
            'time': '2024-01-31T08:00',
            'repeat': 'monthly',
            'delay': '0',
            'start_date': '2024-02-01',
            'end_date': '2024-02-28',
        },
    ):
        response = app.add_schedule.__wrapped__()
        assert response.status_code == 302

    count = app.cursor.execute('SELECT COUNT(*) AS cnt FROM schedules').fetchone()['cnt']
    assert count == 0


def test_add_schedule_rejects_negative_delay():
    app.cursor.execute('DELETE FROM audio_files')
    app.cursor.execute("INSERT INTO audio_files (filename) VALUES (?)", ('probe.mp3',))
    file_id = app.cursor.lastrowid
    app.conn.commit()

    with app.app.test_request_context(
        '/schedule',
        method='POST',
        data={
            'item_type': 'file',
            'item_id': str(file_id),
            'time': '2024-01-31T08:00',
            'repeat': 'once',
            'delay': '-5',
            'start_date': '',
            'end_date': '',
        },
    ):
        response = app.add_schedule.__wrapped__()
        assert response.status_code == 302
        messages = get_flashed_messages()
        assert any('Verzögerung' in message for message in messages)

    count = app.cursor.execute('SELECT COUNT(*) AS cnt FROM schedules').fetchone()['cnt']
    assert count == 0


def test_monthly_schedule_trigger_respects_day():
    schedule_id = _insert_monthly_schedule(31, datetime(2024, 1, 31).date())

    app.load_schedules()
    job = app.scheduler.get_job(str(schedule_id))
    assert job is not None

    day_field = next(field for field in job.trigger.fields if field.name == 'day')
    expression = day_field.expressions[0]
    assert expression.first == 31 and expression.last == 31

    next_run = job.trigger.get_next_fire_time(
        None, datetime(2024, 2, 1, 9, 0, 0, tzinfo=timezone.utc)
    )
    assert next_run.month == 3 and next_run.day == 31


def test_job_next_run_time_uses_local_timezone():
    future = datetime.now() + timedelta(minutes=5)
    future = future.replace(microsecond=0)
    schedule_id = _insert_once_schedule(future)

    app.load_schedules()
    job_id = str(schedule_id)
    job = app.scheduler.get_job(job_id)

    assert job is not None

    app.scheduler.start(paused=True)
    try:
        job = app.scheduler.get_job(job_id)
        assert job.next_run_time.tzinfo == app.LOCAL_TZ
    finally:
        app.scheduler.shutdown(wait=False)
        app.scheduler = BackgroundScheduler(timezone=app.LOCAL_TZ)
