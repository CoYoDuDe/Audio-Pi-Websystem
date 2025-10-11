import importlib
import sys

from flask import get_flashed_messages


def test_negative_env_fallback_allows_zero_delay(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "test")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("MAX_SCHEDULE_DELAY_SECONDS", "-5")

    previous_module = sys.modules.pop("app", None)

    app_module = importlib.import_module("app")

    try:
        assert (
            app_module.MAX_SCHEDULE_DELAY_SECONDS
            == app_module.DEFAULT_MAX_SCHEDULE_DELAY_SECONDS
        )

        app_module.cursor.execute("DELETE FROM audio_files")
        app_module.cursor.execute("DELETE FROM schedules")
        app_module.conn.commit()

        app_module.cursor.execute(
            "INSERT INTO audio_files (filename) VALUES (?)",
            ("probe.mp3",),
        )
        file_id = app_module.cursor.lastrowid
        app_module.conn.commit()

        with app_module.app.test_request_context(
            "/schedule",
            method="POST",
            data={
                "item_type": "file",
                "item_id": str(file_id),
                "time": "2024-01-31T08:00",
                "repeat": "once",
                "delay": "0",
                "start_date": "",
                "end_date": "",
            },
        ):
            response = app_module.add_schedule.__wrapped__()
            assert response.status_code == 302
            messages = get_flashed_messages()
            assert not any("Verzögerung" in message for message in messages)

        count = (
            app_module.cursor.execute(
                "SELECT COUNT(*) AS cnt FROM schedules"
            ).fetchone()["cnt"]
        )
        assert count == 1
    finally:
        if "app_module" in locals():
            app_module.scheduler.remove_all_jobs()
            app_module.cursor.execute("DELETE FROM schedules")
            app_module.cursor.execute("DELETE FROM audio_files")
            app_module.conn.commit()
            app_module.cursor.close()
            app_module.conn.close()
        sys.modules.pop("app", None)
        if previous_module is not None:
            sys.modules["app"] = previous_module
