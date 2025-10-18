import pytest

from tests.csrf_utils import csrf_post
from tests.test_wlan_connect import _login_admin, client as wlan_client_fixture


@pytest.fixture
def client(wlan_client_fixture):
    return wlan_client_fixture


def test_update_route_handles_missing_git(monkeypatch, client):
    flask_client, app_module = client
    _login_admin(flask_client)

    def fake_check_call(*args, **kwargs):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(app_module.subprocess, "check_call", fake_check_call)

    response = csrf_post(flask_client, "/update", follow_redirects=False)

    assert response.status_code == 302

    with flask_client.session_transaction() as session:
        flashes = session.get("_flashes", [])

    assert flashes
    assert flashes[-1][1] == "git nicht verfügbar"
