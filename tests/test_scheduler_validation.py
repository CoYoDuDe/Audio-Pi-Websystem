import importlib
import os

import pytest
from flask import get_flashed_messages, url_for

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


def test_add_schedule_missing_time_field_redirects_with_flash():
    with app.app.test_request_context(
        "/schedule",
        method="POST",
        data={
            "item_type": "File",
            "item_id": "42",
            "repeat": "ONCE",
            "delay": "0",
            "start_date": "",
            "end_date": "",
        },
    ):
        response = app.add_schedule.__wrapped__()
        assert response.status_code == 302
        assert response.headers["Location"] == url_for("index")
        messages = get_flashed_messages()
        assert "Zeitplan konnte nicht hinzugefügt werden: Erforderliche Felder fehlen." in messages

