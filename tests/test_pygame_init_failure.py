import importlib
import sys
import types
from pathlib import Path

import pytest


class DummyPygameError(Exception):
    """Dummy pygame.error Ersatz."""


def _create_dummy_pygame(pytest_module):
    def _fail(*_args, **_kwargs):
        pytest_module.fail("pygame-Musikfunktion sollte nicht aufgerufen werden")

    dummy_music = types.SimpleNamespace(
        set_volume=_fail,
        get_volume=lambda: pytest_module.fail("get_volume sollte nicht genutzt werden"),
        get_busy=lambda: pytest_module.fail("get_busy sollte nicht genutzt werden"),
        unpause=_fail,
        pause=_fail,
        stop=_fail,
        load=_fail,
        play=_fail,
    )

    def failing_init():
        raise DummyPygameError("pygame mixer init failure")

    dummy_mixer = types.SimpleNamespace(init=failing_init, music=dummy_music)
    dummy_pygame = types.ModuleType("pygame")
    dummy_pygame.mixer = dummy_mixer
    dummy_pygame.error = DummyPygameError
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


def _create_dummy_smbus():
    dummy_smbus = types.ModuleType("smbus")

    class DummySMBus:
        def __init__(self, _bus):
            raise FileNotFoundError("no bus")

    dummy_smbus.SMBus = DummySMBus
    return dummy_smbus


@pytest.fixture(autouse=True)
def clear_app_module():
    sys.modules.pop("app", None)
    yield
    sys.modules.pop("app", None)


def test_pygame_init_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_SECRET_KEY", "testkey")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "password")
    monkeypatch.setenv("DB_FILE", str(tmp_path / "test.db"))
    monkeypatch.setenv("TESTING", "0")

    dummy_pygame = _create_dummy_pygame(pytest)
    dummy_gpio = _create_dummy_gpio()
    dummy_smbus = _create_dummy_smbus()

    monkeypatch.setitem(sys.modules, "pygame", dummy_pygame)
    monkeypatch.setitem(sys.modules, "lgpio", dummy_gpio)
    monkeypatch.setitem(sys.modules, "smbus", dummy_smbus)

    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(repo_root))

    app_module = importlib.import_module("app")

    assert app_module.pygame_available is False

    app_module.app.config["LOGIN_DISABLED"] = True

    with app_module.app.test_request_context("/toggle_pause", method="POST"):
        app_module.toggle_pause.__wrapped__()

    with app_module.app.test_request_context("/stop_playback", method="POST"):
        app_module.stop_playback.__wrapped__()

    with app_module.app.test_request_context("/volume", method="POST"):
        app_module.set_volume.__wrapped__()

    monkeypatch.setattr(
        app_module.threading,
        "Thread",
        lambda *args, **kwargs: pytest.fail("Es darf kein Wiedergabe-Thread gestartet werden"),
    )
    with app_module.app.test_request_context("/play_now/file/1", method="POST"):
        app_module.play_now.__wrapped__("file", 1)

    monkeypatch.setattr(
        app_module,
        "get_db_connection",
        lambda *args, **kwargs: pytest.fail("Datenbank darf bei fehlendem pygame nicht genutzt werden"),
    )
    app_module.play_item(123, "file", delay=0)

    monkeypatch.setattr(
        app_module.subprocess,
        "getoutput",
        lambda *args, **kwargs: pytest.fail("Bluetooth-Kommandos dürfen bei fehlendem pygame nicht laufen"),
    )
    app_module.resume_bt_audio()
