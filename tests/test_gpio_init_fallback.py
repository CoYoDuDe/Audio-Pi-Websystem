import importlib
import sys
import types

import pytest


def _create_dummy_pygame():
    music_state = {"volume": 1.0, "busy": False}

    class DummyMusic:
        def set_volume(self, value):
            music_state["volume"] = value

        def get_volume(self):
            return music_state["volume"]

        def get_busy(self):
            return music_state["busy"]

        def load(self, _path):
            music_state["busy"] = True

        def play(self):
            music_state["busy"] = False

        def stop(self):
            music_state["busy"] = False

        def pause(self):
            pass

        def unpause(self):
            pass

    dummy_music = DummyMusic()
    dummy_mixer = types.SimpleNamespace(init=lambda: None, music=dummy_music)
    dummy_pygame = types.ModuleType("pygame")
    dummy_pygame.mixer = dummy_mixer
    return dummy_pygame


def _create_dummy_gpio():
    dummy_gpio = types.ModuleType("lgpio")

    class DummyGPIOError(Exception):
        pass

    def fail_open(_chip):
        raise DummyGPIOError("gpiochip unavailable")

    dummy_gpio.error = DummyGPIOError
    dummy_gpio.gpiochip_open = fail_open
    dummy_gpio.gpio_write = lambda *args, **kwargs: None
    dummy_gpio.gpio_free = lambda *args, **kwargs: None
    dummy_gpio.gpio_claim_output = lambda *args, **kwargs: None
    return dummy_gpio


@pytest.fixture(autouse=True)
def clear_app_module():
    sys.modules.pop("app", None)
    yield
    sys.modules.pop("app", None)


def test_gpio_init_fallback(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "testkey")
    monkeypatch.setenv("TESTING", "0")

    dummy_pygame = _create_dummy_pygame()
    dummy_gpio = _create_dummy_gpio()

    monkeypatch.setitem(sys.modules, "pygame", dummy_pygame)
    monkeypatch.setitem(sys.modules, "lgpio", dummy_gpio)

    monkeypatch.setattr("subprocess.getoutput", lambda _cmd: "Lautstärke: 50%")

    try:
        app_module = importlib.import_module("app")
    except SystemExit as exc:  # pragma: no cover - Sicherstellung gegen sys.exit
        pytest.fail(f"SystemExit ausgelöst: {exc}")

    assert app_module.gpio_handle is None

    # activate_amplifier darf trotz fehlendem GPIO-Handle keine Exception werfen
    app_module.activate_amplifier()
