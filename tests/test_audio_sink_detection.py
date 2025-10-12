import importlib
import os

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
