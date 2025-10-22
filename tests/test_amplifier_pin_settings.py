import importlib
import os
from unittest.mock import MagicMock

import pytest

from .csrf_utils import csrf_post

os.environ.setdefault("FLASK_SECRET_KEY", "test")
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "password")

app = importlib.import_module("app")


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    db_path = tmp_path / "amplifier-settings.db"
    monkeypatch.setattr(app, "DB_FILE", str(db_path), raising=False)
    app.initialize_database()
    app.load_amplifier_gpio_pin_from_settings(log_source=False)
    app.scheduler.remove_all_jobs()
    monkeypatch.setattr(app.pygame.mixer, "music", MagicMock(get_busy=lambda: False))
    yield app
    app.scheduler.remove_all_jobs()


def _login_and_change_password(client):
    csrf_post(
        client,
        "/login",
        data={"username": "admin", "password": "password"},
        follow_redirects=True,
    )
    csrf_post(
        client,
        "/change_password",
        data={"old_password": "password", "new_password": "password1234"},
        follow_redirects=True,
        source_url="/change_password",
    )


def test_default_amplifier_pin_inserted(app_module):
    assert (
        app_module.get_setting(app_module.AMPLIFIER_GPIO_PIN_SETTING_KEY)
        == str(app_module.DEFAULT_AMPLIFIER_GPIO_PIN)
    )
    assert app_module.GPIO_PIN_ENDSTUFE == app_module.DEFAULT_AMPLIFIER_GPIO_PIN
    assert app_module.CONFIGURED_AMPLIFIER_GPIO_PIN is None


def test_save_amplifier_pin_updates_setting(app_module):
    client = app_module.app.test_client()
    with client:
        _login_and_change_password(client)
        response = csrf_post(
            client,
            "/settings/amplifier_pin",
            data={"amplifier_gpio_pin": "18"},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert "Verstärker-Pin auf GPIO 18 gespeichert." in response.get_data(as_text=True)
    assert app_module.get_setting(app_module.AMPLIFIER_GPIO_PIN_SETTING_KEY) == "18"
    assert app_module.GPIO_PIN_ENDSTUFE == 18
    assert app_module.CONFIGURED_AMPLIFIER_GPIO_PIN == 18


def test_save_amplifier_pin_rejects_conflict_with_button(app_module):
    with app_module.get_db_connection() as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO hardware_buttons (gpio_pin, action, item_type, item_id, debounce_ms, enabled)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                18,
                "STOP",
                None,
                None,
                app_module.DEFAULT_BUTTON_DEBOUNCE_MS,
                1,
            ),
        )
        conn.commit()

    client = app_module.app.test_client()
    with client:
        _login_and_change_password(client)
        response = csrf_post(
            client,
            "/settings/amplifier_pin",
            data={"amplifier_gpio_pin": "18"},
            follow_redirects=True,
        )

    body = response.get_data(as_text=True)
    assert "GPIO 18 ist bereits einem Hardware-Button zugewiesen" in body
    assert app_module.get_setting(app_module.AMPLIFIER_GPIO_PIN_SETTING_KEY) == str(
        app_module.DEFAULT_AMPLIFIER_GPIO_PIN
    )
    assert app_module.GPIO_PIN_ENDSTUFE == app_module.DEFAULT_AMPLIFIER_GPIO_PIN
    assert app_module.CONFIGURED_AMPLIFIER_GPIO_PIN is None


def test_create_hardware_button_rejects_amplifier_pin(app_module):
    client = app_module.app.test_client()
    with client:
        _login_and_change_password(client)
        response = csrf_post(
            client,
            "/hardware_buttons",
            data={
                "gpio_pin": str(app_module.GPIO_PIN_ENDSTUFE),
                "action": "STOP",
                "item_reference": "",
            },
            follow_redirects=True,
        )

    body = response.get_data(as_text=True)
    assert "ist für die Endstufe reserviert" in body
    with app_module.get_db_connection() as (conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM hardware_buttons")
        count = cursor.fetchone()[0]
    assert count == 0

