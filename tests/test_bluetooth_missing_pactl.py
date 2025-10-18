import importlib
import sys
import types
from pathlib import Path

import pytest
from flask import get_flashed_messages


@pytest.fixture
def app_module(monkeypatch, tmp_path):
    sys.modules.pop("app", None)

    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "password")
    monkeypatch.setenv("DB_FILE", str(tmp_path / "test.db"))
    monkeypatch.setenv("TESTING", "1")

    dummy_music = types.SimpleNamespace(
        set_volume=lambda *_args, **_kwargs: None,
        get_volume=lambda: 1.0,
        get_busy=lambda: False,
        load=lambda *_args, **_kwargs: None,
        play=lambda *_args, **_kwargs: None,
        pause=lambda *_args, **_kwargs: None,
        stop=lambda *_args, **_kwargs: None,
        unpause=lambda *_args, **_kwargs: None,
    )

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


def test_missing_pactl_disables_bt_detection(monkeypatch, app_module):
    real_run = app_module.subprocess.run

    def raise_file_not_found(cmd, *args, **kwargs):
        if cmd and cmd[0] == "pactl":
            raise FileNotFoundError("pactl not found")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(app_module.subprocess, "run", raise_file_not_found)
    app_module._PACTL_MISSING_LOGGED = False

    with app_module.app.test_request_context("/"):
        connected = app_module.is_bt_connected()
        active = app_module.is_bt_audio_active()
        flashes = get_flashed_messages()

    assert connected is False
    assert active is False
    assert flashes, "Erwarte Nutzerhinweis bei fehlendem pactl"
    assert any("PulseAudio" in message for message in flashes)

    amplifier_triggered = False

    def mark_activation():
        nonlocal amplifier_triggered
        amplifier_triggered = True

    monkeypatch.setattr(app_module, "activate_amplifier", mark_activation)
    monkeypatch.setattr(app_module, "deactivate_amplifier", lambda: None)

    def stop_after_first_cycle(_seconds):
        raise RuntimeError("stop monitor")

    monkeypatch.setattr(app_module.time, "sleep", stop_after_first_cycle)

    with pytest.raises(RuntimeError):
        app_module.bt_audio_monitor()

    assert amplifier_triggered is False
