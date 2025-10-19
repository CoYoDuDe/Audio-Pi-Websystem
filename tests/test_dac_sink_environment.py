import importlib.util
import sys
from pathlib import Path

import pytest

from .csrf_utils import csrf_post


@pytest.fixture
def env_app(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_SECRET_KEY", "test")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "password")
    monkeypatch.setenv("DAC_SINK_NAME", "alsa_output.env_sink")

    module_name = "app_env_test_module"
    spec = importlib.util.spec_from_file_location(
        module_name, Path(__file__).resolve().parents[1] / "app.py"
    )
    module = importlib.util.module_from_spec(spec)
    loader = spec.loader
    assert loader is not None
    sys.modules[module_name] = module
    try:
        loader.exec_module(module)
        monkeypatch.setattr(
            module, "DB_FILE", str(tmp_path / "dac-env-status.db"), raising=False
        )
        monkeypatch.setattr(module, "pygame_available", False, raising=False)
        module.initialize_database()
        module.scheduler.remove_all_jobs()
        yield module
    finally:
        try:
            module.scheduler.remove_all_jobs()
        except Exception:
            pass
        try:
            if getattr(module.scheduler, "running", False):
                module.scheduler.shutdown(wait=False)
        except Exception:
            pass
        sys.modules.pop(module_name, None)


def test_status_and_template_use_environment_default(env_app):
    expected_sink = "alsa_output.env_sink"

    status = env_app.gather_status()
    assert status["default_dac_sink"] == expected_sink

    client = env_app.app.test_client()
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
        assert expected_sink in html
