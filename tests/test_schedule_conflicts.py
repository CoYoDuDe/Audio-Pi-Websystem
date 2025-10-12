import os
import importlib

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
