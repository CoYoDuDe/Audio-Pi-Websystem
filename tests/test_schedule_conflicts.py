import os
import importlib
from datetime import datetime

import pytest
from flask import get_flashed_messages

os.environ.setdefault("FLASK_SECRET_KEY", "test")
os.environ.setdefault("TESTING", "1")

app = importlib.import_module("app")


def _insert_audio_file(filename, duration):
    app.cursor.execute(
        "INSERT INTO audio_files (filename, duration_seconds) VALUES (?, ?)",
        (filename, duration),
    )
    app.conn.commit()
    return app.cursor.lastrowid


def _create_playlist(name, file_ids):
    app.cursor.execute("INSERT INTO playlists (name) VALUES (?)", (name,))
    playlist_id = app.cursor.lastrowid
    for file_id in file_ids:
        app.cursor.execute(
            "INSERT INTO playlist_files (playlist_id, file_id) VALUES (?, ?)",
            (playlist_id, file_id),
        )
    app.conn.commit()
    return playlist_id


@pytest.fixture(autouse=True)
def cleanup():
    app.scheduler.remove_all_jobs()
    for table in ("playlist_files", "schedules", "playlists", "audio_files"):
        app.cursor.execute(f"DELETE FROM {table}")
    app.conn.commit()
    yield
    app.scheduler.remove_all_jobs()
    for table in ("playlist_files", "schedules", "playlists", "audio_files"):
        app.cursor.execute(f"DELETE FROM {table}")
    app.conn.commit()


def test_add_schedule_blocks_overlapping_once_files():
    file_a = _insert_audio_file("a.mp3", 120.0)
    file_b = _insert_audio_file("b.mp3", 60.0)

    with app.app.test_request_context(
        "/schedule",
        method="POST",
        data={
            "item_type": "file",
            "item_id": str(file_a),
            "time": "2024-01-01T08:00",
            "repeat": "once",
            "delay": "0",
            "start_date": "",
            "end_date": "",
        },
    ):
        response = app.add_schedule.__wrapped__()
        assert response.status_code == 302
    with app.app.test_request_context(
        "/schedule",
        method="POST",
        data={
            "item_type": "file",
            "item_id": str(file_b),
            "time": "2024-01-01T08:01",
            "repeat": "once",
            "delay": "0",
            "start_date": "",
            "end_date": "",
        },
    ):
        response = app.add_schedule.__wrapped__()
        assert response.status_code == 302
        messages = get_flashed_messages()
        assert any("überschneidet" in message for message in messages)

    count = app.cursor.execute("SELECT COUNT(*) AS cnt FROM schedules").fetchone()["cnt"]
    assert count == 1


def test_add_schedule_blocks_playlist_overlap():
    file_playlist = _insert_audio_file("playlist_track.mp3", 90.0)
    file_single = _insert_audio_file("single.mp3", 75.0)
    playlist_id = _create_playlist("Mix", [file_playlist])

    with app.app.test_request_context(
        "/schedule",
        method="POST",
        data={
            "item_type": "playlist",
            "item_id": str(playlist_id),
            "time": "2024-02-10T08:00",
            "repeat": "daily",
            "delay": "0",
            "start_date": "2024-02-10",
            "end_date": "",
        },
    ):
        response = app.add_schedule.__wrapped__()
        assert response.status_code == 302
    with app.app.test_request_context(
        "/schedule",
        method="POST",
        data={
            "item_type": "file",
            "item_id": str(file_single),
            "time": "2024-02-10T08:01",
            "repeat": "daily",
            "delay": "0",
            "start_date": "2024-02-10",
            "end_date": "",
        },
    ):
        response = app.add_schedule.__wrapped__()
        assert response.status_code == 302
        messages = get_flashed_messages()
        assert any("überschneidet" in message for message in messages)

    count = app.cursor.execute("SELECT COUNT(*) AS cnt FROM schedules").fetchone()["cnt"]
    assert count == 1


def test_has_schedule_conflict_detects_cross_midnight_once_events():
    evening_file = _insert_audio_file("late_show.mp3", 5400.0)
    night_file = _insert_audio_file("night_mix.mp3", 1800.0)

    with app.app.test_request_context(
        "/schedule",
        method="POST",
        data={
            "item_type": "file",
            "item_id": str(evening_file),
            "time": "2024-03-01T23:30",
            "repeat": "once",
            "delay": "0",
            "start_date": "",
            "end_date": "",
        },
    ):
        response = app.add_schedule.__wrapped__()
        assert response.status_code == 302

    new_schedule = {
        "item_id": str(night_file),
        "item_type": "file",
        "time": "2024-03-02 00:30:00",
        "repeat": "once",
        "delay": 0,
        "start_date": None,
        "end_date": None,
        "day_of_month": None,
    }
    new_once_dt = app.parse_once_datetime(new_schedule["time"])
    new_first_date = app._to_local_aware(new_once_dt).date()

    with app.get_db_connection() as (conn, cursor):
        new_duration = app._get_item_duration(cursor, "file", new_schedule["item_id"])
        assert new_duration is not None
        conflict = app._has_schedule_conflict(
            cursor, new_schedule, new_duration, new_first_date
        )
    assert conflict is True


def test_once_schedule_preserves_utc_timezone_information():
    file_id = _insert_audio_file("utc_track.mp3", 30.0)
    iso_input = "2025-05-10T12:34:56Z"

    with app.app.test_request_context(
        "/schedule",
        method="POST",
        data={
            "item_type": "file",
            "item_id": str(file_id),
            "time": iso_input,
            "repeat": "once",
            "delay": "0",
            "start_date": "",
            "end_date": "",
        },
    ):
        response = app.add_schedule.__wrapped__()
        assert response.status_code == 302

    row = app.cursor.execute(
        "SELECT time FROM schedules WHERE item_id=?", (str(file_id),)
    ).fetchone()
    assert row is not None
    stored_time = row["time"]
    assert "T" in stored_time
    assert stored_time.endswith("+00:00")

    original_dt = app.parse_once_datetime(iso_input)
    stored_dt = app.parse_once_datetime(stored_time)
    assert stored_dt.tzinfo is not None
    assert stored_dt.tzinfo.utcoffset(stored_dt) == original_dt.tzinfo.utcoffset(original_dt)
    assert (
        stored_dt.isoformat(timespec="seconds")
        == original_dt.isoformat(timespec="seconds")
    )


def test_once_schedule_remains_pending_when_playback_busy(monkeypatch):
    schedule_time = datetime.now().replace(microsecond=0)
    app.cursor.execute(
        """
        INSERT INTO schedules (
            item_id,
            item_type,
            time,
            repeat,
            delay,
            start_date,
            end_date,
            day_of_month,
            executed
        )
        VALUES (?, 'file', ?, 'once', 0, NULL, NULL, NULL, 0)
        """,
        (99, schedule_time.strftime("%Y-%m-%d %H:%M:%S")),
    )
    schedule_id = app.cursor.lastrowid
    app.conn.commit()

    monkeypatch.setattr(app, "pygame_available", True)
    monkeypatch.setattr(app.pygame.mixer.music, "get_busy", lambda: True)

    app.schedule_job(schedule_id)

    row = app.cursor.execute(
        "SELECT executed FROM schedules WHERE id=?", (schedule_id,)
    ).fetchone()
    assert row["executed"] == 0
