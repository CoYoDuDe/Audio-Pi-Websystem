import logging

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
