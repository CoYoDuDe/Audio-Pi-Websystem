import threading
import sys

import pytest

from tests.test_playback_decode_failure import _setup_app


@pytest.fixture(autouse=True)
def clear_app_module():
    sys.modules.pop("app", None)
    yield
    sys.modules.pop("app", None)


@pytest.fixture
def app_module(monkeypatch, tmp_path):
    module, _dummy_music = _setup_app(monkeypatch, tmp_path)
    return module


def test_is_bt_audio_active_true_with_matching_sink_id(monkeypatch, app_module):
    def fake_run_pactl(*args):
        if args[:3] == ("list", "short", "sinks"):
            return "2\tbluez_sink.test\tmodule-bluetooth-device.c"
        if args[:3] == ("list", "short", "sink-inputs"):
            return "51\t2\tprotocol-native.c\tTest-Stream"
        raise AssertionError(f"Unbekannter pactl-Befehl: {args}")

    monkeypatch.setattr(app_module, "_run_pactl_command", fake_run_pactl)

    assert app_module.is_bt_audio_active() is True


def test_is_bt_audio_active_false_with_non_matching_sink_id(monkeypatch, app_module):
    def fake_run_pactl(*args):
        if args[:3] == ("list", "short", "sinks"):
            return "2\tbluez_sink.test\tmodule-bluetooth-device.c"
        if args[:3] == ("list", "short", "sink-inputs"):
            return "52\t5\tprotocol-native.c\tAnderer-Stream"
        raise AssertionError(f"Unbekannter pactl-Befehl: {args}")

    monkeypatch.setattr(app_module, "_run_pactl_command", fake_run_pactl)

    assert app_module.is_bt_audio_active() is False


def test_is_bt_audio_active_true_with_name_fallback(monkeypatch, app_module):
    def fake_run_pactl(*args):
        if args[:3] == ("list", "short", "sinks"):
            return "2\tbluez_sink.test\tmodule-bluetooth-device.c"
        if args[:3] == ("list", "short", "sink-inputs"):
            return "53\t7\tprotocol-native.c\tbluez_sink.test"
        raise AssertionError(f"Unbekannter pactl-Befehl: {args}")

    monkeypatch.setattr(app_module, "_run_pactl_command", fake_run_pactl)

    assert app_module.is_bt_audio_active() is True


def test_background_services_control_bt_monitor(monkeypatch, app_module):
    monkeypatch.setattr(app_module, "TESTING", False, raising=False)
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

    start_calls = []
    monitor_stopped = threading.Event()

    def fake_bt_audio_monitor(*, stop_event=None):
        start_calls.append(stop_event)
        assert stop_event is not None
        stop_event.wait()
        monitor_stopped.set()

    auto_accept_triggered = threading.Event()

    def fake_auto_accept():
        auto_accept_triggered.set()
        return "success"

    button_start_calls = []
    button_stop_calls = []

    monkeypatch.setattr(app_module, "bt_audio_monitor", fake_bt_audio_monitor)
    monkeypatch.setattr(app_module, "bluetooth_auto_accept", fake_auto_accept)
    monkeypatch.setattr(app_module, "_start_button_monitor", lambda: button_start_calls.append(True))
    monkeypatch.setattr(app_module, "_stop_button_monitor", lambda: button_stop_calls.append(True))

    app_module._bt_audio_monitor_thread = None
    app_module._bt_audio_monitor_stop_event = None
    app_module._BACKGROUND_SERVICES_STARTED = False

    assert app_module.start_background_services(force=True) is True
    assert auto_accept_triggered.wait(1.0)
    assert len(start_calls) == 1
    stop_event = start_calls[0]
    assert stop_event is not None and not stop_event.is_set()
    assert button_start_calls

    assert app_module.stop_background_services() is True
    assert monitor_stopped.wait(1.0)
    assert stop_event.is_set()
    assert button_stop_calls
    assert app_module._bt_audio_monitor_thread is None
