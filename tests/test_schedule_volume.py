import os
import re
import importlib
from datetime import datetime
from pathlib import Path

import pytest
from flask import get_flashed_messages

os.environ.setdefault("FLASK_SECRET_KEY", "test")
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "password")

app = importlib.import_module("app")


@pytest.fixture(autouse=True)
def cleanup():
    app.scheduler.remove_all_jobs()
    for table in ("playlist_files", "schedules", "playlists", "audio_files"):
        app.cursor.execute(f"DELETE FROM {table}")
    for key in (
        app.SCHEDULE_VOLUME_PERCENT_SETTING_KEY,
        app.SCHEDULE_VOLUME_DB_SETTING_KEY,
    ):
        app.cursor.execute("DELETE FROM settings WHERE key=?", (key,))
    app.conn.commit()
    yield
    app.scheduler.remove_all_jobs()
    for table in ("playlist_files", "schedules", "playlists", "audio_files"):
        app.cursor.execute(f"DELETE FROM {table}")
    for key in (
        app.SCHEDULE_VOLUME_PERCENT_SETTING_KEY,
        app.SCHEDULE_VOLUME_DB_SETTING_KEY,
    ):
        app.cursor.execute("DELETE FROM settings WHERE key=?", (key,))
    app.conn.commit()


def _insert_audio_file(filename="probe.mp3", duration=1.5):
    app.cursor.execute(
        "INSERT INTO audio_files (filename, duration_seconds) VALUES (?, ?)",
        (filename, duration),
    )
    app.conn.commit()
    return app.cursor.lastrowid


def test_schedule_form_respects_zero_default():
    with app.app.test_request_context(
        "/settings/schedule_default_volume",
        method="POST",
        data={"schedule_default_volume": "0"},
    ):
        response = app.save_schedule_default_volume.__wrapped__()
        assert response.status_code == 302

    with app.get_db_connection() as (conn, cursor):
        cursor.execute("SELECT id FROM users WHERE username=?", ("admin",))
        user_row = cursor.fetchone()
        assert user_row is not None
        user_id = user_row["id"]
        cursor.execute(
            "UPDATE users SET must_change_password=0 WHERE id=?",
            (user_id,),
        )
        conn.commit()

    original_get_busy = app.pygame.mixer.music.get_busy
    app.pygame.mixer.music.get_busy = lambda: False
    try:
        with app.app.test_client() as client:
            with client.session_transaction() as session:
                session["_user_id"] = str(user_id)
                session["_fresh"] = True

            response = client.get("/")
            assert response.status_code == 200
            content = response.get_data(as_text=True)
    finally:
        app.pygame.mixer.music.get_busy = original_get_busy

    assert re.search(r'id="schedule-volume"[^>]*value="0"', content)
    assert re.search(r'id="schedule-volume-value"[^>]*>\s*0%<', content)

    with app.get_db_connection() as (conn, cursor):
        cursor.execute(
            "UPDATE users SET must_change_password=1 WHERE id=?",
            (user_id,),
        )
        conn.commit()


def test_add_schedule_accepts_custom_volume():
    file_id = _insert_audio_file()
    with app.app.test_request_context(
        "/schedule",
        method="POST",
        data={
            "item_type": "file",
            "item_id": str(file_id),
            "time": "2024-01-01T08:00",
            "repeat": "once",
            "delay": "0",
            "start_date": "",
            "end_date": "",
            "volume_percent": "45",
        },
    ):
        response = app.add_schedule.__wrapped__()
        assert response.status_code == 302
    row = app.cursor.execute(
        "SELECT volume_percent FROM schedules ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["volume_percent"] == 45


def test_add_schedule_uses_configured_default_volume():
    file_id = _insert_audio_file()
    with app.app.test_request_context(
        "/settings/schedule_default_volume",
        method="POST",
        data={"schedule_default_volume": "37%"},
    ):
        response = app.save_schedule_default_volume.__wrapped__()
        assert response.status_code == 302

    with app.app.test_request_context(
        "/schedule",
        method="POST",
        data={
            "item_type": "file",
            "item_id": str(file_id),
            "time": "2024-01-01T09:00",
            "repeat": "once",
            "delay": "0",
            "start_date": "",
            "end_date": "",
            "volume_percent": "",
        },
    ):
        response = app.add_schedule.__wrapped__()
        assert response.status_code == 302

    row = app.cursor.execute(
        "SELECT volume_percent FROM schedules ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["volume_percent"] == 37


def test_add_schedule_rejects_volume_out_of_range():
    file_id = _insert_audio_file()
    with app.app.test_request_context(
        "/schedule",
        method="POST",
        data={
            "item_type": "file",
            "item_id": str(file_id),
            "time": "2024-01-01T08:00",
            "repeat": "once",
            "delay": "0",
            "start_date": "",
            "end_date": "",
            "volume_percent": "150",
        },
    ):
        response = app.add_schedule.__wrapped__()
        assert response.status_code == 302
        messages = get_flashed_messages()
        assert any("Lautstärke" in message for message in messages)
    count = app.cursor.execute("SELECT COUNT(*) AS cnt FROM schedules").fetchone()["cnt"]
    assert count == 0


def test_default_schedule_volume_can_be_defined_in_db():
    with app.app.test_request_context(
        "/settings/schedule_default_volume",
        method="POST",
        data={"schedule_default_volume": "-6 dB"},
    ):
        response = app.save_schedule_default_volume.__wrapped__()
        assert response.status_code == 302
    details = app.get_schedule_default_volume_details()
    assert details["source"] == "settings_db"
    assert details["percent"] == 50

    file_id = _insert_audio_file()
    with app.app.test_request_context(
        "/schedule",
        method="POST",
        data={
            "item_type": "file",
            "item_id": str(file_id),
            "time": "2024-01-01T10:00",
            "repeat": "once",
            "delay": "0",
            "start_date": "",
            "end_date": "",
        },
    ):
        response = app.add_schedule.__wrapped__()
        assert response.status_code == 302

    row = app.cursor.execute(
        "SELECT volume_percent FROM schedules ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["volume_percent"] == 50


def test_schedule_job_passes_volume_to_play_item(monkeypatch):
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
            executed,
            volume_percent
        )
        VALUES (?, 'file', ?, 'once', 0, NULL, NULL, NULL, 0, 25)
        """,
        (42, schedule_time.strftime("%Y-%m-%d %H:%M:%S")),
    )
    schedule_id = app.cursor.lastrowid
    app.conn.commit()
    captured = {}

    def fake_play_item(item_id, item_type, delay, is_schedule=False, volume_percent=100):
        captured["args"] = (item_id, item_type, delay, is_schedule, volume_percent)
        return True

    monkeypatch.setattr(app, "play_item", fake_play_item)

    app.schedule_job(schedule_id)

    assert captured["args"][4] == 25
    assert captured["args"][3] is True


def test_play_item_scales_volume_temporarily(monkeypatch, tmp_path):
    uploads_dir = Path(app.app.config["UPLOAD_FOLDER"])
    uploads_dir.mkdir(parents=True, exist_ok=True)
    test_filename = "scheduled.mp3"
    file_path = uploads_dir / test_filename
    file_path.write_bytes(b"fake")
    file_id = _insert_audio_file(test_filename, duration=2.0)

    class DummySegment:
        def normalize(self, headroom=0.1):
            return self

        def export(self, dest, format="wav"):
            Path(dest).write_bytes(b"data")

    monkeypatch.setattr(app.AudioSegment, "from_file", lambda path: DummySegment())
    monkeypatch.setattr(app, "set_sink", lambda sink: True)
    monkeypatch.setattr(app, "activate_amplifier", lambda: None)
    monkeypatch.setattr(app, "deactivate_amplifier", lambda: None)
    monkeypatch.setattr(app.pygame.mixer.music, "load", lambda path: None)
    monkeypatch.setattr(app.pygame.mixer.music, "play", lambda: None)
    monkeypatch.setattr(app.pygame.mixer.music, "get_busy", lambda: False)

    master_volume = 0.8
    monkeypatch.setattr(app.pygame.mixer.music, "get_volume", lambda: master_volume)
    recorded = []

    def fake_set_volume(value):
        recorded.append(value)

    monkeypatch.setattr(app.pygame.mixer.music, "set_volume", fake_set_volume)

    app.play_item(file_id, "file", delay=0, is_schedule=True, volume_percent=50)

    assert len(recorded) >= 2
    assert recorded[0] == pytest.approx(master_volume * 0.5, rel=1e-3)
    assert recorded[-1] == pytest.approx(master_volume, rel=1e-3)

    file_path.unlink(missing_ok=True)
