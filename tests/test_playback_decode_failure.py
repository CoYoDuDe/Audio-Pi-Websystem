import importlib
import logging
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest
from flask import get_flashed_messages
from pydub.exceptions import CouldntDecodeError


class DummyMusic:
    def __init__(self):
        self.loaded_files = []
        self.play_calls = 0
        self.last_set_volume = None

    def set_volume(self, value):
        self.last_set_volume = value

    def get_volume(self):
        return 1.0

    def get_busy(self):
        return False

    def load(self, path):
        self.loaded_files.append(path)

    def play(self):
        self.play_calls += 1

    def stop(self):
        return None

    def pause(self):
        return None

    def unpause(self):
        return None


class ImmediateThread:
    def __init__(self, target, args=(), kwargs=None):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self.started = False

    def start(self):
        self.started = True
        self.target(*self.args, **self.kwargs)


def _create_dummy_pygame(dummy_music):
    dummy_pygame = types.ModuleType("pygame")
    dummy_pygame.error = RuntimeError
    dummy_pygame.mixer = types.SimpleNamespace(music=dummy_music)
    return dummy_pygame


def _create_dummy_gpio():
    dummy_gpio = types.ModuleType("lgpio")
    dummy_gpio.error = RuntimeError
    dummy_gpio.gpiochip_open = lambda *_args, **_kwargs: object()
    dummy_gpio.gpio_write = lambda *_args, **_kwargs: None
    dummy_gpio.gpio_free = lambda *_args, **_kwargs: None
    dummy_gpio.gpio_claim_output = lambda *_args, **_kwargs: None
    return dummy_gpio


def _create_dummy_smbus():
    dummy_smbus = types.ModuleType("smbus")

    class DummyBus:
        def __init__(self, *_args, **_kwargs):
            raise FileNotFoundError("no bus")

    dummy_smbus.SMBus = DummyBus
    return dummy_smbus


@pytest.fixture(autouse=True)
def clear_app_module():
    sys.modules.pop("app", None)
    yield
    sys.modules.pop("app", None)


def _setup_app(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_SECRET_KEY", "testkey")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "password")
    monkeypatch.setenv("DB_FILE", str(tmp_path / "test.db"))
    monkeypatch.setenv("TESTING", "1")

    dummy_music = DummyMusic()
    monkeypatch.setitem(sys.modules, "pygame", _create_dummy_pygame(dummy_music))
    monkeypatch.setitem(sys.modules, "lgpio", _create_dummy_gpio())
    monkeypatch.setitem(sys.modules, "smbus", _create_dummy_smbus())

    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(repo_root))

    app_module = importlib.import_module("app")
    app_module.app.config["LOGIN_DISABLED"] = True
    app_module.app.config["UPLOAD_FOLDER"] = str(tmp_path)

    monkeypatch.setattr(app_module, "set_sink", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(app_module, "activate_amplifier", lambda: None)
    monkeypatch.setattr(app_module, "deactivate_amplifier", lambda: None)

    return app_module, dummy_music


def test_play_item_handles_decode_error(monkeypatch, tmp_path, caplog):
    app_module, dummy_music = _setup_app(monkeypatch, tmp_path)

    broken_file = tmp_path / "broken.mp3"
    broken_file.write_bytes(b"broken")

    @contextmanager
    def dummy_connection():
        class DummyCursor:
            def execute(self, *_args, **_kwargs):
                return None

            def fetchone(self):
                return {"filename": broken_file.name, "duration_seconds": 5}

        yield (None, DummyCursor())

    monkeypatch.setattr(app_module, "get_db_connection", dummy_connection)

    def raise_decode_error(*_args, **_kwargs):
        raise CouldntDecodeError("boom")

    monkeypatch.setattr(app_module.AudioSegment, "from_file", raise_decode_error)

    with app_module.app.test_request_context("/"):
        caplog.clear()
        with caplog.at_level(logging.ERROR):
            app_module.play_item(1, "file", delay=0, is_schedule=False)
        messages = get_flashed_messages()
    error_messages = [record.message for record in caplog.records]

    assert dummy_music.loaded_files == []
    assert any("Konnte Audiodatei" in message for message in error_messages)
    assert any("konnte nicht dekodiert" in message.lower() for message in messages)


def test_play_now_thread_handles_decode_error(monkeypatch, tmp_path, caplog):
    app_module, dummy_music = _setup_app(monkeypatch, tmp_path)

    playlist_file = tmp_path / "playlist_broken.mp3"
    playlist_file.write_bytes(b"broken")

    @contextmanager
    def dummy_playlist_connection():
        class DummyCursor:
            def execute(self, *_args, **_kwargs):
                return None

            def fetchall(self):
                return [{"filename": playlist_file.name, "duration_seconds": None}]

        yield (None, DummyCursor())

    monkeypatch.setattr(app_module, "get_db_connection", dummy_playlist_connection)

    def raise_decode_error(*_args, **_kwargs):
        raise CouldntDecodeError("boom")

    monkeypatch.setattr(app_module.AudioSegment, "from_file", raise_decode_error)
    monkeypatch.setattr(app_module.threading, "Thread", ImmediateThread)

    with app_module.app.test_request_context("/play_now/playlist/1", method="POST"):
        caplog.clear()
        with caplog.at_level(logging.ERROR):
            response = app_module.play_now.__wrapped__("playlist", 1)
        messages = get_flashed_messages()
    error_messages = [record.message for record in caplog.records]

    assert response.status_code == 302
    assert dummy_music.loaded_files == []
    assert any("Konnte Audiodatei" in message for message in error_messages)
    assert "Abspielen gestartet" in messages


def test_play_item_keeps_amp_active_for_bt(monkeypatch, tmp_path, caplog):
    app_module, _dummy_music = _setup_app(monkeypatch, tmp_path)

    @contextmanager
    def dummy_connection():
        class DummyCursor:
            def execute(self, *_args, **_kwargs):
                return None

            def fetchone(self):
                return {"filename": "missing.mp3", "duration_seconds": 5}

        yield (None, DummyCursor())

    monkeypatch.setattr(app_module, "get_db_connection", dummy_connection)

    calls = {"deactivate": 0, "resume": 0, "loopback": 0}

    def fake_deactivate():
        calls["deactivate"] += 1

    def fake_resume():
        calls["resume"] += 1

    def fake_loopback():
        calls["loopback"] += 1

    monkeypatch.setattr(app_module, "deactivate_amplifier", fake_deactivate)
    monkeypatch.setattr(app_module, "resume_bt_audio", fake_resume)
    monkeypatch.setattr(app_module, "load_loopback", fake_loopback)
    monkeypatch.setattr(app_module, "is_bt_connected", lambda: True)

    with caplog.at_level(logging.INFO):
        app_module.play_item(1, "file", delay=0, is_schedule=True)

    assert calls["deactivate"] == 0
    assert calls["resume"] == 1
    assert calls["loopback"] == 1
    assert any(
        "Bluetooth-Verbindung aktiv" in record.message for record in caplog.records
    )
