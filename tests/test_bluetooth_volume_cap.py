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


def test_bluetooth_volume_cap_reduces_high_volume(monkeypatch, app_module):
    monkeypatch.setattr(app_module, "get_normalization_headroom_db", lambda: 3.0)
    cap = app_module.get_bluetooth_volume_cap_percent()
    assert cap.percent == 89
    assert cap.headroom_db == pytest.approx(3.0)

    calls = []
    volume_responses = [
        (
            "Volume: front-left: 65536 / 150% / 12.00 dB, "
            "front-right: 65536 / 148% / 11.50 dB"
        ),
        (
            "Volume: front-left: 65536 / 86% / -3.00 dB, "
            "front-right: 65536 / 85% / -3.10 dB"
        ),
    ]

    def fake_run_pactl(*args):
        calls.append(args)
        if args[0] == "get-sink-volume":
            if not volume_responses:
                raise AssertionError("Zu viele get-sink-volume Aufrufe")
            return volume_responses.pop(0)
        if args[0] == "set-sink-volume":
            return "OK"
        if args[:3] == ("list", "short", "sinks"):
            return "2\tbluez_sink.test\tmodule-bluetooth-device.c"  # pragma: no cover - Fallback
        raise AssertionError(f"Unbekannter pactl-Befehl: {args}")

    def fail_run(*_args, **_kwargs):  # pragma: no cover - Absicherung gegen echte Aufrufe
        raise AssertionError("subprocess.run darf im Test nicht direkt aufgerufen werden")

    monkeypatch.setattr(app_module, "_run_pactl_command", fake_run_pactl)
    monkeypatch.setattr(app_module.subprocess, "run", fail_run)

    changed = app_module._enforce_bluetooth_volume_cap_for_sink("bluez_sink.test", cap)
    assert changed is True
    set_calls = [call for call in calls if call[0] == "set-sink-volume"]
    assert len(set_calls) == 1
    volume_call = set_calls[0]
    assert volume_call[1] == "bluez_sink.test"
    assert volume_call[2] == "-15.0dB"
    assert not volume_responses


def test_bluetooth_volume_cap_triggers_for_small_headroom(monkeypatch, app_module):
    monkeypatch.setattr(app_module, "get_normalization_headroom_db", lambda: 0.1)
    cap = app_module.get_bluetooth_volume_cap_percent()
    assert cap.percent < 100
    assert cap.percent == 99
    assert cap.headroom_db == pytest.approx(0.1)

    calls = []
    volume_responses = [
        (
            "Volume: front-left: 65536 / 101% / 0.15 dB, "
            "front-right: 65536 / 100% / 0.12 dB"
        ),
        (
            "Volume: front-left: 65536 / 100% / 0.05 dB, "
            "front-right: 65536 / 99% / 0.02 dB"
        ),
        (
            "Volume: front-left: 65536 / 99% / -0.20 dB, "
            "front-right: 65536 / 98% / -0.22 dB"
        ),
    ]

    def fake_run_pactl(*args):
        calls.append(args)
        if args[0] == "get-sink-volume":
            if not volume_responses:
                raise AssertionError("Zu viele get-sink-volume Aufrufe")
            return volume_responses.pop(0)
        if args[0] == "set-sink-volume":
            return "OK"
        if args[:3] == ("list", "short", "sinks"):
            return "2\tbluez_sink.test\tmodule-bluetooth-device.c"  # pragma: no cover - Fallback
        raise AssertionError(f"Unbekannter pactl-Befehl: {args}")

    def fail_run(*_args, **_kwargs):  # pragma: no cover - Absicherung gegen echte Aufrufe
        raise AssertionError("subprocess.run darf im Test nicht direkt aufgerufen werden")

    monkeypatch.setattr(app_module, "_run_pactl_command", fake_run_pactl)
    monkeypatch.setattr(app_module.subprocess, "run", fail_run)

    changed = app_module._enforce_bluetooth_volume_cap_for_sink("bluez_sink.test", cap)
    assert changed is True
    set_calls = [call for call in calls if call[0] == "set-sink-volume"]
    assert len(set_calls) == 2
    assert set_calls[0][1] == "bluez_sink.test"
    assert set_calls[0][2] == "-0.25dB"
    assert set_calls[1] == ("set-sink-volume", "bluez_sink.test", "99%")
    assert not volume_responses


def test_bluetooth_volume_cap_leaves_low_volume_untouched(monkeypatch, app_module):
    monkeypatch.setattr(app_module, "get_normalization_headroom_db", lambda: 6.0)
    cap = app_module.get_bluetooth_volume_cap_percent()
    assert cap.percent == 79
    assert cap.headroom_db == pytest.approx(6.0)

    calls = []

    def fake_run_pactl(*args):
        calls.append(args)
        if args[0] == "get-sink-volume":
            return (
                "Volume: front-left: 65536 / 45% / -15.00 dB, "
                "front-right: 65536 / 46% / -14.50 dB"
            )
        if args[0] == "set-sink-volume":  # pragma: no cover - sollte nicht erreicht werden
            raise AssertionError("Lautstärke darf nicht erhöht oder erneut gesetzt werden")
        if args[:3] == ("list", "short", "sinks"):
            return "2\tbluez_sink.test\tmodule-bluetooth-device.c"  # pragma: no cover
        raise AssertionError(f"Unbekannter pactl-Befehl: {args}")

    def fail_run(*_args, **_kwargs):  # pragma: no cover - Absicherung gegen echte Aufrufe
        raise AssertionError("subprocess.run darf im Test nicht direkt aufgerufen werden")

    monkeypatch.setattr(app_module, "_run_pactl_command", fake_run_pactl)
    monkeypatch.setattr(app_module.subprocess, "run", fail_run)

    changed = app_module._enforce_bluetooth_volume_cap_for_sink("bluez_sink.test", cap)
    assert changed is False
    assert calls.count(("get-sink-volume", "bluez_sink.test")) == 1


def test_bluetooth_volume_cap_respects_db_over_percent(monkeypatch, app_module):
    monkeypatch.setattr(app_module, "get_normalization_headroom_db", lambda: 3.0)
    cap = app_module.get_bluetooth_volume_cap_percent()
    assert cap.percent == 89
    assert cap.headroom_db == pytest.approx(3.0)

    calls = []
    volume_responses = [
        (
            "Volume: front-left: 65536 / 89% / -1.00 dB, "
            "front-right: 65536 / 90% / -0.80 dB"
        ),
        (
            "Volume: front-left: 65536 / 88% / -3.00 dB, "
            "front-right: 65536 / 88% / -3.05 dB"
        ),
    ]

    def fake_run_pactl(*args):
        calls.append(args)
        if args[0] == "get-sink-volume":
            if not volume_responses:
                raise AssertionError("Zu viele get-sink-volume Aufrufe")
            return volume_responses.pop(0)
        if args[0] == "set-sink-volume":
            return "OK"
        if args[:3] == ("list", "short", "sinks"):
            return "2\tbluez_sink.test\tmodule-bluetooth-device.c"  # pragma: no cover
        raise AssertionError(f"Unbekannter pactl-Befehl: {args}")

    def fail_run(*_args, **_kwargs):  # pragma: no cover - Absicherung gegen echte Aufrufe
        raise AssertionError("subprocess.run darf im Test nicht direkt aufgerufen werden")

    monkeypatch.setattr(app_module, "_run_pactl_command", fake_run_pactl)
    monkeypatch.setattr(app_module.subprocess, "run", fail_run)

    changed = app_module._enforce_bluetooth_volume_cap_for_sink("bluez_sink.test", cap)
    assert changed is True
    set_calls = [call for call in calls if call[0] == "set-sink-volume"]
    assert len(set_calls) == 1
    assert set_calls[0] == ("set-sink-volume", "bluez_sink.test", "-2.2dB")
    assert not volume_responses
