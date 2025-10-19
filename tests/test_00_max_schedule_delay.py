import importlib
import sys

from flask import get_flashed_messages

from .csrf_utils import csrf_post


def _disable_pygame(app_module):
    app_module.pygame_available = False
    dummy_mixer = type(
        "DummyMixer",
        (),
        {
            "music": type(
                "DummyMusic",
                (),
                {"get_busy": staticmethod(lambda: False)},
            )()
        },
    )()
    app_module.pygame.mixer = dummy_mixer


def test_negative_env_fallback_allows_zero_delay(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "test")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("MAX_SCHEDULE_DELAY_SECONDS", "-5")

    previous_module = sys.modules.pop("app", None)

    app_module = importlib.import_module("app")

    _disable_pygame(app_module)

    try:
        assert (
            app_module.MAX_SCHEDULE_DELAY_SECONDS
            == app_module.DEFAULT_MAX_SCHEDULE_DELAY_SECONDS
        )

        app_module.cursor.execute("DELETE FROM audio_files")
        app_module.cursor.execute("DELETE FROM schedules")
        app_module.conn.commit()

        app_module.cursor.execute(
            "INSERT INTO audio_files (filename, duration_seconds) VALUES (?, ?)",
            ("probe.mp3", 1.0),
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


def test_index_template_reflects_configured_schedule_delay(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_SECRET_KEY", "test")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "password")
    db_file = tmp_path / "max-delay.db"
    monkeypatch.setenv("DB_FILE", str(db_file))
    expected_limit = 90
    monkeypatch.setenv("MAX_SCHEDULE_DELAY_SECONDS", str(expected_limit))

    previous_module = sys.modules.pop("app", None)

    app_module = importlib.import_module("app")

    _disable_pygame(app_module)

    try:
        client = app_module.app.test_client()
        with client:
            login_response = csrf_post(
                client,
                "/login",
                data={"username": "admin", "password": "password"},
                follow_redirects=True,
            )
            assert login_response.status_code == 200

            change_response = csrf_post(
                client,
                "/change_password",
                data={"old_password": "password", "new_password": "password1234"},
                follow_redirects=True,
                source_url="/change_password",
            )
            assert change_response.status_code == 200

            response = client.get("/", follow_redirects=True)
            assert response.status_code == 200
            html = response.get_data(as_text=True)

        assert f'max="{expected_limit}"' in html
        assert f"Maximale Verzögerung: {expected_limit}" in html
    finally:
        if "app_module" in locals():
            try:
                app_module.scheduler.remove_all_jobs()
            except Exception:
                pass
            try:
                if getattr(app_module.scheduler, "running", False):
                    app_module.scheduler.shutdown(wait=False)
            except Exception:
                pass
            try:
                app_module.cursor.close()
            except Exception:
                pass
            try:
                app_module.conn.close()
            except Exception:
                pass
        sys.modules.pop("app", None)
        if previous_module is not None:
            sys.modules["app"] = previous_module
