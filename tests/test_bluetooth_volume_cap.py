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
    limit = app_module.get_bluetooth_volume_cap_percent()
    assert limit == 89

    calls = []

    def fake_run_pactl(*args):
        calls.append(args)
        if args[0] == "get-sink-volume":
            return (
                "Volume: front-left: 65536 / 120% / 5.00 dB, "
                "front-right: 65536 / 118% / 4.50 dB"
            )
        if args[0] == "set-sink-volume":
            return "OK"
        if args[:3] == ("list", "short", "sinks"):
            return "2\tbluez_sink.test\tmodule-bluetooth-device.c"  # pragma: no cover - Fallback
        raise AssertionError(f"Unbekannter pactl-Befehl: {args}")

    def fail_run(*_args, **_kwargs):  # pragma: no cover - Absicherung gegen echte Aufrufe
        raise AssertionError("subprocess.run darf im Test nicht direkt aufgerufen werden")

    monkeypatch.setattr(app_module, "_run_pactl_command", fake_run_pactl)
    monkeypatch.setattr(app_module.subprocess, "run", fail_run)

    changed = app_module._enforce_bluetooth_volume_cap_for_sink("bluez_sink.test", limit)
    assert changed is True
    assert ("set-sink-volume", "bluez_sink.test", f"{limit}%") in calls


def test_bluetooth_volume_cap_triggers_for_small_headroom(monkeypatch, app_module):
    monkeypatch.setattr(app_module, "get_normalization_headroom_db", lambda: 0.1)
    limit = app_module.get_bluetooth_volume_cap_percent()
    assert limit < 100
    assert limit == 99

    calls = []

    def fake_run_pactl(*args):
        calls.append(args)
        if args[0] == "get-sink-volume":
            return (
                "Volume: front-left: 65536 / 101% / 0.10 dB, "
                "front-right: 65536 / 100% / 0.00 dB"
            )
        if args[0] == "set-sink-volume":
            return "OK"
        if args[:3] == ("list", "short", "sinks"):
            return "2\tbluez_sink.test\tmodule-bluetooth-device.c"  # pragma: no cover - Fallback
        raise AssertionError(f"Unbekannter pactl-Befehl: {args}")

    def fail_run(*_args, **_kwargs):  # pragma: no cover - Absicherung gegen echte Aufrufe
        raise AssertionError("subprocess.run darf im Test nicht direkt aufgerufen werden")

    monkeypatch.setattr(app_module, "_run_pactl_command", fake_run_pactl)
    monkeypatch.setattr(app_module.subprocess, "run", fail_run)

    changed = app_module._enforce_bluetooth_volume_cap_for_sink("bluez_sink.test", limit)
    assert changed is True
    assert ("set-sink-volume", "bluez_sink.test", f"{limit}%") in calls


def test_bluetooth_volume_cap_leaves_low_volume_untouched(monkeypatch, app_module):
    monkeypatch.setattr(app_module, "get_normalization_headroom_db", lambda: 6.0)
    limit = app_module.get_bluetooth_volume_cap_percent()
    assert limit == 79

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

    changed = app_module._enforce_bluetooth_volume_cap_for_sink("bluez_sink.test", limit)
    assert changed is False
    assert calls.count(("get-sink-volume", "bluez_sink.test")) == 1
