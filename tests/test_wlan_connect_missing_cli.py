import subprocess

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
    assert (
        flashes[-1][1]
        == "wpa_cli nicht gefunden oder keine Berechtigung. Bitte Installation prüfen."
    )


def test_wlan_connect_missing_cli_called_process_error(client, monkeypatch):
    flask_client, app_module = client
    monkeypatch.setenv("AUDIO_PI_DISABLE_SUDO", "0")
    monkeypatch.setattr(app_module, "_SUDO_DISABLED", False, raising=False)
    _login_admin(flask_client)

    def fake_run_wpa_cli(*args, **kwargs):
        raise subprocess.CalledProcessError(
            returncode=127,
            cmd=["sudo", "wpa_cli", "-i", "wlan0", "add_network"],
            stderr="sudo: wpa_cli: command not found",
            output="",
        )

    monkeypatch.setattr(app_module, "_run_wpa_cli", fake_run_wpa_cli)

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
    messages = [message for _category, message in flashes]
    not_found_message = (
        "wpa_cli nicht gefunden oder keine Berechtigung. Bitte Installation prüfen."
    )
    generic_error = "Fehler beim WLAN-Verbindungsaufbau. Details im Log einsehbar."

    assert not_found_message in messages
    assert generic_error in messages
    assert messages.index(not_found_message) < messages.index(generic_error)
