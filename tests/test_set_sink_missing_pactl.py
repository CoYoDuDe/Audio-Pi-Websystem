import importlib
import sys
import types
from contextlib import contextmanager
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

    monkeypatch.setattr(app_module, "activate_amplifier", lambda: None)
    monkeypatch.setattr(app_module, "deactivate_amplifier", lambda: None)
    monkeypatch.setattr(app_module, "_prepare_audio_for_playback", lambda *_args, **_kwargs: True)

    yield app_module

    sys.modules.pop("app", None)


def test_set_sink_handles_missing_pactl(monkeypatch, tmp_path, app_module):
    app_module.audio_status["dac_sink_detected"] = None
    monkeypatch.setattr(app_module, "_list_pulse_sinks", lambda: [app_module.DAC_SINK])

    def raise_file_not_found(_cmd):
        raise FileNotFoundError("pactl not found")

    monkeypatch.setattr(app_module.subprocess, "call", raise_file_not_found)

    result = app_module.set_sink(app_module.DAC_SINK)

    assert result is False
    assert app_module.audio_status["dac_sink_detected"] is False

    audio_file = tmp_path / "audio.mp3"
    audio_file.write_bytes(b"dummy")

    @contextmanager
    def dummy_connection():
        class DummyCursor:
            def execute(self, *_args, **_kwargs):
                return None

            def fetchone(self):
                return {"filename": audio_file.name, "duration_seconds": 1}

        yield (None, DummyCursor())

    monkeypatch.setattr(app_module, "get_db_connection", dummy_connection)

    with app_module.app.test_request_context("/play_now/file/1", method="POST"):
        play_result = app_module.play_item(1, "file", delay=0, is_schedule=False)
        flashes = get_flashed_messages()

    assert play_result is None
    assert flashes, "Erwarte eine Nutzerbenachrichtigung bei fehlendem pactl"
