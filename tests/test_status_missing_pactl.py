import importlib
import sys
import types
from pathlib import Path

import pytest
from flask import get_flashed_messages


class DummyMusic:
    def set_volume(self, value):
        self.last_set_volume = value

    def get_volume(self):
        return 1.0

    def load(self, path):
        self.last_loaded = path

    def play(self):
        self.play_called = True

    def get_busy(self):
        return False


@pytest.fixture
def app_module(monkeypatch, tmp_path):
    sys.modules.pop("app", None)

    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "password")
    monkeypatch.setenv("DB_FILE", str(tmp_path / "test.db"))
    monkeypatch.setenv("TESTING", "1")

    dummy_music = DummyMusic()
    dummy_pygame = types.ModuleType("pygame")
    dummy_pygame.error = RuntimeError
    dummy_pygame.mixer = types.SimpleNamespace(music=dummy_music)

    dummy_lgpio = types.ModuleType("lgpio")
    dummy_lgpio.error = RuntimeError
    dummy_lgpio.gpiochip_open = lambda *_args, **_kwargs: object()
    dummy_lgpio.gpio_write = lambda *_args, **_kwargs: None
    dummy_lgpio.gpio_free = lambda *_args, **_kwargs: None
    dummy_lgpio.gpio_claim_output = lambda *_args, **_kwargs: None

    dummy_smbus = types.ModuleType("smbus")

    monkeypatch.setitem(sys.modules, "pygame", dummy_pygame)
    monkeypatch.setitem(sys.modules, "lgpio", dummy_lgpio)
    monkeypatch.setitem(sys.modules, "smbus", dummy_smbus)

    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(repo_root))

    app_module = importlib.import_module("app")
    app_module.app.config["LOGIN_DISABLED"] = True
    app_module.app.config["UPLOAD_FOLDER"] = str(tmp_path)

    yield app_module

    sys.modules.pop("app", None)


def test_gather_status_fallbacks(monkeypatch, app_module):
    app_module.audio_status["dac_sink_detected"] = None

    pactl_calls = 0

    def fake_run_pactl(*_args):
        nonlocal pactl_calls
        pactl_calls += 1
        app_module._notify_missing_pactl()
        return None

    monkeypatch.setattr(app_module, "_run_pactl_command", fake_run_pactl)

    with app_module.app.test_request_context("/status"):
        status = app_module.gather_status()
        flashes = get_flashed_messages()

    assert pactl_calls >= 1
    assert status["current_volume"] == "Unbekannt"
    assert status["current_sink"] == "Nicht verfügbar"
    assert "pactl" not in " ".join(str(value).lower() for value in status.values())
    assert flashes == [app_module._PACTL_MISSING_MESSAGE]
