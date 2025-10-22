import logging
import threading

import pytest

from tests.csrf_utils import csrf_post
from tests.test_wlan_connect import _login_admin, client as wlan_client_fixture


@pytest.fixture
def client(wlan_client_fixture):
    return wlan_client_fixture


class _DummyProcess:
    def __init__(self, returncode: int = 1, stderr: str = "Simulierter Fehler im Auto-Accept"):
        self._returncode = returncode
        self.returncode = None
        self._stderr = stderr

    def communicate(self, *_args, **_kwargs):
        self.returncode = self._returncode
        return "", self._stderr


def test_bluetooth_auto_accept_failure(monkeypatch, client, caplog):
    flask_client, app_module = client
    _login_admin(flask_client)

    real_popen = app_module.subprocess.Popen

    def fake_check_call(*_args, **_kwargs):
        return None

    def fake_popen(args, *popen_args, **popen_kwargs):
        if isinstance(args, (list, tuple)) and args[:2] == ["sudo", "bluetoothctl"]:
            return _DummyProcess()
        return real_popen(args, *popen_args, **popen_kwargs)

    monkeypatch.setattr(app_module.subprocess, "check_call", fake_check_call)
    monkeypatch.setattr(app_module.subprocess, "Popen", fake_popen)

    with app_module.app.test_request_context("/"):
        with caplog.at_level(logging.ERROR):
            result = app_module.bluetooth_auto_accept()

    assert result == "error"
    assert any(
        "Bluetooth auto-accept beendete sich mit Code" in record.getMessage()
        for record in caplog.records
    )

    caplog.clear()
    with caplog.at_level(logging.ERROR):
        response = csrf_post(flask_client, "/bluetooth_on", follow_redirects=False)

    assert response.status_code == 302

    with flask_client.session_transaction() as session:
        flashes = session.get("_flashes", [])

    assert flashes
    assert (
        flashes[-1][1]
        == "Bluetooth konnte nicht aktiviert werden (Auto-Accept fehlgeschlagen)"
    )

    assert any(
        "Bluetooth konnte nach dem Einschalten nicht vollständig eingerichtet werden"
        in record.getMessage()
        for record in caplog.records
    )


def test_start_background_services_triggers_bluetooth_helpers(monkeypatch, client):
    _flask_client, app_module = client

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

    auto_accept_called = threading.Event()

    def fake_auto_accept():
        auto_accept_called.set()
        return "success"

    monitor_calls = []
    bt_monitor_state = {"running": False}
    button_state = {"running": False}

    monkeypatch.setattr(app_module, "bluetooth_auto_accept", fake_auto_accept)

    def fake_start_bt_monitor():
        bt_monitor_state["running"] = True
        monitor_calls.append("start")

    def fake_stop_bt_monitor():
        if bt_monitor_state["running"]:
            bt_monitor_state["running"] = False
            monitor_calls.append("stop")

    def fake_start_button_monitor():
        button_state["running"] = True
        monitor_calls.append("button_start")

    def fake_stop_button_monitor():
        if button_state["running"]:
            button_state["running"] = False
            monitor_calls.append("button_stop")

    monkeypatch.setattr(app_module, "_start_bt_audio_monitor_thread", fake_start_bt_monitor)
    monkeypatch.setattr(app_module, "_stop_bt_audio_monitor_thread", fake_stop_bt_monitor)
    monkeypatch.setattr(app_module, "_start_button_monitor", fake_start_button_monitor)
    monkeypatch.setattr(app_module, "_stop_button_monitor", fake_stop_button_monitor)

    app_module._BACKGROUND_SERVICES_STARTED = False

    assert app_module.start_background_services(force=True) is True
    assert auto_accept_called.wait(1.0)
    assert bt_monitor_state["running"] is True
    assert button_state["running"] is True
    assert "start" in monitor_calls
    assert "button_start" in monitor_calls

    assert app_module.stop_background_services() is True
    assert bt_monitor_state["running"] is False
    assert button_state["running"] is False
    assert "stop" in monitor_calls
    assert "button_stop" in monitor_calls
