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
    db_path = tmp_path / "dac-env-settings.db"
    monkeypatch.setattr(app, "DB_FILE", str(db_path), raising=False)
    monkeypatch.setattr(app, "DAC_SINK", app.DAC_SINK, raising=False)
    monkeypatch.setattr(app, "DAC_SINK_HINT", app.DAC_SINK_HINT, raising=False)
    monkeypatch.setattr(app, "CONFIGURED_DAC_SINK", app.CONFIGURED_DAC_SINK, raising=False)
    app.initialize_database()
    app.scheduler.remove_all_jobs()
    monkeypatch.setattr(app.pygame.mixer, "music", MagicMock(get_busy=lambda: False))
    yield app
    app.scheduler.remove_all_jobs()


def _login_and_initialize(client):
    response = csrf_post(
        client,
        "/login",
        data={"username": "admin", "password": "password"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    password_response = csrf_post(
        client,
        "/change_password",
        data={"old_password": "password", "new_password": "password1234"},
        follow_redirects=True,
        source_url="/change_password",
    )
    assert password_response.status_code == 200


def test_reset_uses_environment_default(app_module, monkeypatch):
    env_sink = "alsa_output.environment_sink"

    client = app_module.app.test_client()
    with client:
        _login_and_initialize(client)

        set_response = csrf_post(
            client,
            "/settings/dac_sink",
            data={"dac_sink_name": "alsa_output.custom_sink"},
            follow_redirects=True,
        )
        assert set_response.status_code == 200
        assert app_module.DAC_SINK == "alsa_output.custom_sink"
        assert app_module.CONFIGURED_DAC_SINK == "alsa_output.custom_sink"

        monkeypatch.setenv("DAC_SINK_NAME", env_sink)

        reset_response = csrf_post(
            client,
            "/settings/dac_sink",
            data={"dac_sink_name": ""},
            follow_redirects=True,
        )
        assert reset_response.status_code == 200

    assert app_module.get_setting(app_module.DAC_SINK_SETTING_KEY) == ""
    assert app_module.DAC_SINK == env_sink
    assert app_module.DAC_SINK_HINT == env_sink
    assert app_module.CONFIGURED_DAC_SINK is None
