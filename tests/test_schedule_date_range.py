import os
import importlib
from datetime import datetime, timedelta

import pytest

os.environ.setdefault('FLASK_SECRET_KEY', 'test')
os.environ.setdefault('TESTING', '1')

app = importlib.import_module('app')


@pytest.fixture(autouse=True)
def cleanup_schedules():
    app.cursor.execute('DELETE FROM schedules')
    app.conn.commit()
    yield
    app.cursor.execute('DELETE FROM schedules')
    app.conn.commit()


def _insert_recurring_schedule(start_date, end_date):
    app.cursor.execute(
        """
        INSERT INTO schedules (item_id, item_type, time, repeat, delay, start_date, end_date, executed)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            99,
            'file',
            '00:00:00',
            'daily',
            0,
            start_date.isoformat() if start_date else None,
            end_date.isoformat() if end_date else None,
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
