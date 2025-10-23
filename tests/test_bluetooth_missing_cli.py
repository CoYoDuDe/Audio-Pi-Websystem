import pytest

from tests.csrf_utils import csrf_post
from tests.test_wlan_connect import _login_admin, client as wlan_client_fixture


@pytest.fixture
def client(wlan_client_fixture):
    return wlan_client_fixture


def test_bluetooth_on_missing_cli(monkeypatch, client):
    flask_client, app_module = client
    _login_admin(flask_client)

    real_popen = app_module.subprocess.Popen

    def fake_check_call(args, **kwargs):
        if args[:2] == ["bluetoothctl", "power"]:
            raise FileNotFoundError("bluetoothctl not found")
        return 0

    def fake_popen(args, *popen_args, **kwargs):
        if isinstance(args, (list, tuple)) and args[:1] == ["bluetoothctl"]:
            raise FileNotFoundError("bluetoothctl not found")
        return real_popen(args, *popen_args, **kwargs)

    monkeypatch.setattr(app_module.subprocess, "check_call", fake_check_call)
    monkeypatch.setattr(app_module.subprocess, "Popen", fake_popen)

    response = csrf_post(flask_client, "/bluetooth_on", follow_redirects=False)

    assert response.status_code == 302

    with flask_client.session_transaction() as session:
        flashes = session.get("_flashes", [])

    assert flashes
    assert (
        flashes[-1][1]
        == "bluetoothctl nicht gefunden oder keine Berechtigung. Bitte Installation überprüfen."
    )

    # Sicherstellen, dass der Auto-Accept-Aufruf selbst keine Ausnahme wirft
    with app_module.app.test_request_context("/"):
        result = app_module.bluetooth_auto_accept()
        assert result == "missing_cli"
