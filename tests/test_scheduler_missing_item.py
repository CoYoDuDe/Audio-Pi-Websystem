import importlib
import os

import pytest
from flask import get_flashed_messages

os.environ.setdefault("FLASK_SECRET_KEY", "test")
os.environ.setdefault("TESTING", "1")

app = importlib.import_module("app")


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


def test_add_schedule_rejects_missing_item():
    with app.app.test_request_context(
        "/schedule",
        method="POST",
        data={
            "item_type": "file",
            "item_id": "999999",
            "time": "2024-01-01T08:00",
            "repeat": "once",
            "delay": "0",
            "start_date": "",
            "end_date": "",
        },
    ):
        response = app.add_schedule.__wrapped__()
        assert response.status_code == 302
        messages = get_flashed_messages()
        assert "Ausgewähltes Element existiert nicht mehr." in messages

    count = app.cursor.execute("SELECT COUNT(*) AS cnt FROM schedules").fetchone()["cnt"]
    assert count == 0
