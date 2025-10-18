import pytest

from tests.csrf_utils import csrf_post
from tests.test_wlan_connect import _login_admin, client as wlan_client_fixture


@pytest.fixture
def client(wlan_client_fixture):
    return wlan_client_fixture


def test_wlan_connect_missing_cli(client, monkeypatch):
    flask_client, app_module = client
    _login_admin(flask_client)

    def fake_run(args, **kwargs):
        raise FileNotFoundError("wpa_cli not found")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    response = csrf_post(
        flask_client,
        "/wlan_connect",
        data={"ssid": "MissingCLI", "password": "secretpass"},
        follow_redirects=False,
        source_url="/change_password",
    )

    assert response.status_code == 302
    with flask_client.session_transaction() as session:
        flashes = session.get("_flashes", [])

    assert flashes
    assert flashes[-1][1] == "wpa_cli oder sudo nicht gefunden. Bitte Installation überprüfen."
