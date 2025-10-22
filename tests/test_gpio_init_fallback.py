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


def _create_dummy_gpio(success_map=None):
    if success_map is None:
        success_map = {}

    dummy_gpio = types.ModuleType("lgpio")
    call_log = []

    class DummyGPIOError(Exception):
        pass

    def gpiochip_open(chip):
        call_log.append(chip)
        if chip in success_map:
            return success_map[chip]
        raise DummyGPIOError(f"gpiochip{chip} unavailable")

    dummy_gpio.error = DummyGPIOError
    dummy_gpio.gpiochip_open = gpiochip_open
    dummy_gpio.gpio_write = lambda *args, **kwargs: None
    dummy_gpio.gpio_free = lambda *args, **kwargs: None
    dummy_gpio.gpio_claim_output = lambda *args, **kwargs: None
    dummy_gpio._call_log = call_log
    return dummy_gpio


@pytest.fixture(autouse=True)
def clear_app_module():
    sys.modules.pop("app", None)
    yield
    sys.modules.pop("app", None)


def _patch_common_dependencies(monkeypatch, dummy_gpio):
    dummy_pygame = _create_dummy_pygame()

    monkeypatch.setenv("FLASK_SECRET_KEY", "testkey")
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.setenv("AUDIO_PI_SUPPRESS_AUTOSTART", "1")
    monkeypatch.setitem(sys.modules, "pygame", dummy_pygame)
    monkeypatch.setitem(sys.modules, "lgpio", dummy_gpio)
    monkeypatch.setattr("subprocess.getoutput", lambda _cmd: "Lautstärke: 50%")


def _capture_logs(monkeypatch):
    info_messages = []
    warning_messages = []

    def _store(target_list, message, *args, **kwargs):
        formatted = message % args if args else message
        target_list.append(formatted)

    monkeypatch.setattr("logging.info", lambda msg, *a, **kw: _store(info_messages, msg, *a, **kw))
    monkeypatch.setattr(
        "logging.warning", lambda msg, *a, **kw: _store(warning_messages, msg, *a, **kw)
    )
    return info_messages, warning_messages


def test_gpio_init_prefers_gpiochip4(monkeypatch):
    dummy_gpio = _create_dummy_gpio({4: "handle-4"})
    _patch_common_dependencies(monkeypatch, dummy_gpio)
    monkeypatch.setattr("glob.glob", lambda pattern: ["/dev/gpiochip0", "/dev/gpiochip5"])
    info_messages, warning_messages = _capture_logs(monkeypatch)

    app_module = importlib.import_module("app")

    assert app_module.gpio_handle == "handle-4"
    assert dummy_gpio._call_log == [4]
    gpio_warning_messages = [msg for msg in warning_messages if "gpiochip" in msg]
    assert not gpio_warning_messages
    gpio_info_messages = [msg for msg in info_messages if "gpiochip" in msg]
    assert any("gpiochip4" in message for message in gpio_info_messages)


def test_gpio_init_falls_back_to_gpiochip0(monkeypatch):
    dummy_gpio = _create_dummy_gpio({0: "handle-0"})
    _patch_common_dependencies(monkeypatch, dummy_gpio)
    monkeypatch.setattr("glob.glob", lambda pattern: ["/dev/gpiochip0", "/dev/gpiochip2"])
    info_messages, warning_messages = _capture_logs(monkeypatch)

    app_module = importlib.import_module("app")

    assert app_module.gpio_handle == "handle-0"
    assert dummy_gpio._call_log == [4, 0]
    gpio_warning_messages = [msg for msg in warning_messages if "gpiochip" in msg]
    assert not gpio_warning_messages
    gpio_info_messages = [msg for msg in info_messages if "gpiochip" in msg]
    assert any("gpiochip0" in message for message in gpio_info_messages)


def test_gpio_init_logs_after_all_candidates_fail(monkeypatch):
    dummy_gpio = _create_dummy_gpio()
    _patch_common_dependencies(monkeypatch, dummy_gpio)
    monkeypatch.setattr("glob.glob", lambda pattern: ["/dev/gpiochip2"])
    info_messages, warning_messages = _capture_logs(monkeypatch)

    app_module = importlib.import_module("app")

    assert app_module.gpio_handle is None
    assert dummy_gpio._call_log == [4, 0, 2]
    gpio_info_messages = [msg for msg in info_messages if "gpiochip" in msg]
    assert not gpio_info_messages
    gpio_warning_messages = [msg for msg in warning_messages if "gpiochip" in msg]
    assert gpio_warning_messages and len(gpio_warning_messages) == 1
    warning_text = gpio_warning_messages[0]
    assert "gpiochip4" in warning_text
    assert "gpiochip0" in warning_text
    assert "gpiochip2" in warning_text

    # activate_amplifier darf trotz fehlendem GPIO-Handle keine Exception werfen
    app_module.activate_amplifier()


def test_button_monitor_lifecycle_managed_by_background_services(monkeypatch):
    dummy_gpio = _create_dummy_gpio({4: "handle-4"})
    _patch_common_dependencies(monkeypatch, dummy_gpio)
    monkeypatch.setattr("glob.glob", lambda pattern: ["/dev/gpiochip4"])

    app_module = importlib.import_module("app")
    monkeypatch.setattr(app_module, "skip_past_once_schedules", lambda: None)
    monkeypatch.setattr(app_module, "load_schedules", lambda: None)
    monkeypatch.setattr(app_module, "update_auto_reboot_job", lambda: None)

    class _DummyScheduler:
        def __init__(self):
            self.running = False

        def start(self):
            self.running = True

        def shutdown(self, wait=False):
            self.running = False

    dummy_scheduler = _DummyScheduler()
    monkeypatch.setattr(app_module, "scheduler", dummy_scheduler, raising=False)

    monitor_state = {"running": False}

    def fake_start_button_monitor():
        monitor_state["running"] = True

    def fake_stop_button_monitor():
        if monitor_state["running"]:
            monitor_state["running"] = False

    monkeypatch.setattr(app_module, "_start_button_monitor", fake_start_button_monitor)
    monkeypatch.setattr(app_module, "_stop_button_monitor", fake_stop_button_monitor)
    monkeypatch.setattr(app_module, "bluetooth_auto_accept", lambda: "success")
    monkeypatch.setattr(app_module, "_start_bt_audio_monitor_thread", lambda: None)
    monkeypatch.setattr(app_module, "_stop_bt_audio_monitor_thread", lambda: None)

    app_module._BACKGROUND_SERVICES_STARTED = False

    assert app_module.start_background_services(force=True) is True
    assert monitor_state["running"] is True

    assert app_module.stop_background_services() is True
    assert monitor_state["running"] is False
