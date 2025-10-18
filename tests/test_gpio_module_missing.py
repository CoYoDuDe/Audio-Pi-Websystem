import builtins
import importlib
import sys
import types

import pytest


def _create_dummy_pygame_module():
    dummy_music = types.SimpleNamespace(
        set_volume=lambda value: None,
        get_volume=lambda: 1.0,
        get_busy=lambda: False,
        load=lambda _path: None,
        play=lambda: None,
        stop=lambda: None,
        pause=lambda: None,
        unpause=lambda: None,
    )
    dummy_mixer = types.SimpleNamespace(init=lambda: None, music=dummy_music)
    dummy_pygame = types.ModuleType("pygame")
    dummy_pygame.mixer = dummy_mixer
    return dummy_pygame


@pytest.fixture(autouse=True)
def _clear_app_module():
    sys.modules.pop("app", None)
    yield
    sys.modules.pop("app", None)


def test_import_without_lgpio(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "testkey")
    monkeypatch.setenv("TESTING", "0")

    dummy_pygame = _create_dummy_pygame_module()
    monkeypatch.setitem(sys.modules, "pygame", dummy_pygame)

    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "lgpio":
            raise ImportError("lgpio missing for test")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr("subprocess.getoutput", lambda _cmd: "Lautstärke: 50%")

    app_module = importlib.import_module("app")

    assert app_module.GPIO is None
    assert app_module.GPIO_AVAILABLE is False
    assert app_module.gpio_handle is None

    assert app_module._set_amp_output(1) is False

    # Verstärkerfunktionen müssen ohne lgpio sauber zurückkehren
    app_module.activate_amplifier()
    app_module.deactivate_amplifier()
