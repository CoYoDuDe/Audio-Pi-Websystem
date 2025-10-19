import importlib
import sys
import types
from pathlib import Path

import pytest


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

    monkeypatch.setattr(app_module, "activate_amplifier", lambda: None)
    monkeypatch.setattr(app_module, "deactivate_amplifier", lambda: None)
    monkeypatch.setattr(app_module, "_prepare_audio_for_playback", lambda *_args, **_kwargs: True)

    yield app_module

    sys.modules.pop("app", None)


def test_set_sink_handles_non_zero_exit_code(monkeypatch, app_module):
    original_sink = app_module.DAC_SINK
    app_module.audio_status["dac_sink_detected"] = True

    monkeypatch.setattr(app_module, "_list_pulse_sinks", lambda: [original_sink])

    def fail_if_called(_sink_name):
        pytest.fail("_sink_is_configured wurde unerwartet aufgerufen")

    monkeypatch.setattr(app_module, "_sink_is_configured", fail_if_called)
    monkeypatch.setattr(app_module.subprocess, "call", lambda _cmd: 1)

    result = app_module.set_sink(original_sink)

    assert result is False
    assert app_module.DAC_SINK == original_sink
    assert app_module.audio_status["dac_sink_detected"] is False
