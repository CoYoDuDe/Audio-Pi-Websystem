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
    db_path = tmp_path / "dac-settings.db"
    monkeypatch.setattr(app, "DB_FILE", str(db_path), raising=False)
    monkeypatch.setattr(app, "DAC_SINK", app.DAC_SINK, raising=False)
    monkeypatch.setattr(app, "CONFIGURED_DAC_SINK", app.CONFIGURED_DAC_SINK, raising=False)
    app.initialize_database()
    app.scheduler.remove_all_jobs()
    monkeypatch.setattr(app.pygame.mixer, "music", MagicMock(get_busy=lambda: False))
    yield app
    app.scheduler.remove_all_jobs()


def test_save_dac_sink_updates_setting(app_module):
    client = app_module.app.test_client()
    with client:
        response = csrf_post(
            client,
            "/login",
            data={"username": "admin", "password": "password"},
            follow_redirects=True,
        )
        assert response.status_code == 200

        change_response = csrf_post(
            client,
            "/change_password",
            data={"old_password": "password", "new_password": "password1234"},
            follow_redirects=True,
            source_url="/change_password",
        )
        assert change_response.status_code == 200

        update_response = csrf_post(
            client,
            "/settings/dac_sink",
            data={"dac_sink_name": "alsa_output.custom_sink"},
            follow_redirects=True,
        )
        assert update_response.status_code == 200

    assert app_module.get_setting(app_module.DAC_SINK_SETTING_KEY) == "alsa_output.custom_sink"
    assert app_module.DAC_SINK == "alsa_output.custom_sink"
    assert app_module.CONFIGURED_DAC_SINK == "alsa_output.custom_sink"


def test_save_dac_sink_reset_to_default(app_module):
    client = app_module.app.test_client()
    with client:
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
        csrf_post(
            client,
            "/settings/dac_sink",
            data={"dac_sink_name": ""},
            follow_redirects=True,
        )

    assert app_module.get_setting(app_module.DAC_SINK_SETTING_KEY) == ""
    assert app_module.DAC_SINK == app_module.DEFAULT_DAC_SINK
    assert app_module.CONFIGURED_DAC_SINK is None
