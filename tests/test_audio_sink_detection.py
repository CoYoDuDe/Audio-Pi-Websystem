import importlib
import os

import pytest

os.environ.setdefault('FLASK_SECRET_KEY', 'test')
os.environ.setdefault('TESTING', '1')

app = importlib.import_module('app')


def test_set_sink_detected(monkeypatch):
    calls = []

    def fake_check_output(cmd, text=None, encoding=None, errors=None):
        assert cmd == ["pactl", "list", "short", "sinks"]
        return f"0\t{app.DAC_SINK}\n"

    def fake_call(cmd):
        calls.append(cmd)
        return 0

    app.audio_status["hifiberry_detected"] = None
    monkeypatch.setattr(app.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(app.subprocess, "call", fake_call)

    result = app.set_sink(app.DAC_SINK)

    assert result is True
    assert app.audio_status["hifiberry_detected"] is True
    assert calls == [["pactl", "set-default-sink", app.DAC_SINK]]


def test_set_sink_missing(monkeypatch):
    calls = []

    def fake_check_output(cmd, text=None, encoding=None, errors=None):
        assert cmd == ["pactl", "list", "short", "sinks"]
        return "0\talsa_output.internal\n"

    def fake_call(cmd):
        calls.append(cmd)
        return 0

    app.audio_status["hifiberry_detected"] = None
    monkeypatch.setattr(app.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(app.subprocess, "call", fake_call)

    result = app.set_sink(app.DAC_SINK)

    assert result is False
    assert app.audio_status["hifiberry_detected"] is False
    assert calls == []


def test_set_sink_keeps_flag_for_non_dac(monkeypatch):
    calls = []

    bluetooth_sink = "bluez_sink.12345"

    def fake_check_output(cmd, text=None, encoding=None, errors=None):
        assert cmd == ["pactl", "list", "short", "sinks"]
        return f"0\t{bluetooth_sink}\tRUNNING\n"

    def fake_call(cmd):
        calls.append(cmd)
        return 0

    app.audio_status["hifiberry_detected"] = False
    monkeypatch.setattr(app.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(app.subprocess, "call", fake_call)

    result = app.set_sink(bluetooth_sink)

    assert result is True
    assert app.audio_status["hifiberry_detected"] is False
    assert calls == [["pactl", "set-default-sink", bluetooth_sink]]


def test_set_sink_resolves_pattern(monkeypatch):
    original_hint = app.DAC_SINK_HINT
    original_sink = app.DAC_SINK
    original_flag = app.audio_status.get("hifiberry_detected")
    app.DAC_SINK_HINT = "pattern:alsa_output.pattern_test*"
    app.DAC_SINK = app.DAC_SINK_HINT

    calls = []

    def fake_check_output(cmd, text=None, encoding=None, errors=None):
        assert cmd == ["pactl", "list", "short", "sinks"]
        return "0\talsa_output.pattern_test-dac\tRUNNING\n"
def test_set_sink_uses_default_when_name_missing(monkeypatch):
    calls = []
    default_sink = "alsa_output.default"

    def fake_check_output(cmd, text=None, encoding=None, errors=None):
        assert cmd == ["pactl", "list", "short", "sinks"]
        return f"0\t{default_sink}\tRUNNING\n"

    def fake_call(cmd):
        calls.append(cmd)
        return 0

    monkeypatch.setattr(app.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(app.subprocess, "call", fake_call)

    try:
        result = app.set_sink(app.DAC_SINK_HINT)
        assert result is True
        assert app.DAC_SINK == "alsa_output.pattern_test-dac"
        assert calls == [["pactl", "set-default-sink", "alsa_output.pattern_test-dac"]]
        assert app.audio_status["hifiberry_detected"] is True
    finally:
        app.DAC_SINK_HINT = original_hint
        app.DAC_SINK = original_sink
        app.audio_status["hifiberry_detected"] = original_flag
    monkeypatch.setattr(app, "DAC_SINK", default_sink, raising=False)
    app.audio_status["hifiberry_detected"] = None
    monkeypatch.setattr(app.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(app.subprocess, "call", fake_call)

    result = app.set_sink(None)

    assert result is True
    assert app.audio_status["hifiberry_detected"] is True
    assert calls == [["pactl", "set-default-sink", default_sink]]


def test_load_dac_sink_from_settings_roundtrip(monkeypatch, tmp_path):
    db_path = tmp_path / "dac-settings.db"
    monkeypatch.setattr(app, "DB_FILE", str(db_path), raising=False)
    monkeypatch.setattr(app, "DAC_SINK", app.DAC_SINK, raising=False)
    monkeypatch.setattr(app, "CONFIGURED_DAC_SINK", app.CONFIGURED_DAC_SINK, raising=False)
    app.initialize_database()

    custom_sink = "alsa_output.custom_sink"
    app.set_setting(app.DAC_SINK_SETTING_KEY, custom_sink)
    app.load_dac_sink_from_settings()

    assert app.DAC_SINK == custom_sink
    assert app.CONFIGURED_DAC_SINK == custom_sink

    app.set_setting(app.DAC_SINK_SETTING_KEY, "")
    app.load_dac_sink_from_settings()

    assert app.DAC_SINK == app.DEFAULT_DAC_SINK
    assert app.CONFIGURED_DAC_SINK is None


def test_gather_status_includes_hifiberry_flag(monkeypatch):
    class FakeDateTime:
        @staticmethod
        def now():
            class FakeNow:
                def strftime(self, fmt):
                    return "2024-01-01 12:00:00"

            return FakeNow()

    def fake_getoutput(cmd):
        if "iwgetid" in cmd:
            return "TestSSID"
        if "pactl get-sink-volume" in cmd:
            return "55%"
        if "pactl get-default-sink" in cmd:
            return "alsa_output.default"
        return ""

    app.audio_status["hifiberry_detected"] = False
    monkeypatch.setattr(app, "datetime", FakeDateTime)
    monkeypatch.setattr(app.pygame.mixer.music, "get_busy", lambda: True)
    monkeypatch.setattr(app, "is_bt_connected", lambda: True)
    monkeypatch.setattr(app, "RELAY_INVERT", True)
    monkeypatch.setattr(app.subprocess, "getoutput", fake_getoutput)

    status = app.gather_status()

    assert status["hifiberry_detected"] is False
    assert status["wlan_status"] == "TestSSID"
    assert status["current_sink"] == "alsa_output.default"
    assert status["current_volume"] == "55%"
    assert status["current_time"] == "2024-01-01 12:00:00"
    assert status["playing"] is True
    assert status["bluetooth_status"] == "Verbunden"
    assert status["relay_invert"] is True
    assert "configured_dac_sink" in status
    assert status["default_dac_sink"] == app.DEFAULT_DAC_SINK


@pytest.mark.parametrize("sink_available", [False, True])
def test_gather_status_auto_detects_hifiberry(monkeypatch, sink_available):
    class FakeDateTime:
        @staticmethod
        def now():
            class FakeNow:
                def strftime(self, fmt):
                    return "2024-01-01 12:00:00"

            return FakeNow()

    def fake_getoutput(cmd):
        if "iwgetid" in cmd:
            return "TestSSID"
        if "pactl get-sink-volume" in cmd:
            return "55%"
        if "pactl get-default-sink" in cmd:
            return "alsa_output.default"
        return ""

    app.audio_status["hifiberry_detected"] = None
    monkeypatch.setattr(app, "_is_sink_available", lambda sink: sink_available)
    monkeypatch.setattr(app, "datetime", FakeDateTime)
    monkeypatch.setattr(app.pygame.mixer.music, "get_busy", lambda: True)
    monkeypatch.setattr(app, "is_bt_connected", lambda: True)
    monkeypatch.setattr(app, "RELAY_INVERT", True)
    monkeypatch.setattr(app.subprocess, "getoutput", fake_getoutput)

    status = app.gather_status()

    assert status["hifiberry_detected"] is sink_available
    assert app.audio_status["hifiberry_detected"] is sink_available
