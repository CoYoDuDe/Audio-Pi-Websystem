import importlib
import sys
import types

import pytest


class DummyThread:
    def __init__(self, target=None, args=(), kwargs=None, name=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self.name = name
        self.daemon = daemon
        self._started = False

    def start(self):
        self._started = True

    def is_alive(self):
        return self._started

    def join(self, timeout=None):
        self._started = False


@pytest.fixture
def dummy_lgpio(monkeypatch):
    call_log = []

    dummy_module = types.ModuleType("lgpio")

    class DummyError(Exception):
        pass

    dummy_module.error = DummyError
    dummy_module.SET_PULL_UP = 0x1
    dummy_module.SET_PULL_DOWN = 0x2

    def gpiochip_open(chip):
        if chip != 0:
            raise DummyError(f"gpiochip{chip} unavailable")
        return 123

    def gpiochip_close(handle):
        call_log.append(("gpiochip_close", handle))

    def gpio_claim_input(handle, line, flags):
        call_log.append(("gpio_claim_input", (handle, line, flags)))

    def gpio_read(handle, line):
        call_log.append(("gpio_read", (handle, line)))
        return 1

    def gpio_free(handle, line):
        call_log.append(("gpio_free", (handle, line)))

    dummy_module.gpiochip_open = gpiochip_open
    dummy_module.gpiochip_close = gpiochip_close
    dummy_module.gpio_claim_input = gpio_claim_input
    dummy_module.gpio_read = gpio_read
    dummy_module.gpio_free = gpio_free

    monkeypatch.setitem(sys.modules, "lgpio", dummy_module)

    return dummy_module, call_log


def test_button_monitor_claims_input_with_pin_before_flags(monkeypatch, dummy_lgpio):
    dummy_module, call_log = dummy_lgpio

    import hardware.buttons as buttons

    buttons = importlib.reload(buttons)

    monkeypatch.setattr(buttons.threading, "Thread", DummyThread)
    monkeypatch.setattr(buttons.glob, "glob", lambda pattern: [])

    assignment = buttons.ButtonAssignment(
        name="TestButton",
        pin=17,
        callback=lambda: None,
        pull="up",
    )

    monitor = buttons.ButtonMonitor([assignment], chip_id=0)

    try:
        assert monitor.start() is True
        assert monitor.running is True
    finally:
        monitor.stop(timeout=0)

    claim_calls = [entry for entry in call_log if entry[0] == "gpio_claim_input"]
    assert claim_calls, "gpio_claim_input wurde nicht aufgerufen"

    handle, pin, flags = claim_calls[0][1]
    assert handle == 123
    assert pin == assignment.pin
    assert flags == dummy_module.SET_PULL_UP

    # Sicherstellen, dass das Starten nicht vorzeitig abgebrochen hat und ein Read stattfand
    read_calls = [entry for entry in call_log if entry[0] == "gpio_read"]
    assert read_calls
