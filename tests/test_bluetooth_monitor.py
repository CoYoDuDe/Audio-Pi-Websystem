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
