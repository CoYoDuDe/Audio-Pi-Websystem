import importlib
import sys
from pathlib import Path

import pytest
import subprocess
from subprocess import CompletedProcess

from tests.csrf_utils import csrf_post


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_SECRET_KEY", "testkey")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DB_FILE", str(tmp_path / "test.db"))
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "password")

    repo_root = Path(__file__).resolve().parents[1]
    repo_path = str(repo_root)
    if repo_path not in sys.path:
        sys.path.insert(0, repo_path)
    if "app" in sys.modules:
        del sys.modules["app"]
    app_module = importlib.import_module("app")
    app_module.pygame_available = False

    with app_module.app.test_client() as test_client:
        yield test_client, app_module

    if hasattr(app_module, "conn") and app_module.conn is not None:
        try:
            app_module.conn.close()
        except Exception:
            pass


def _login_admin(flask_client):
    login_data = {"username": "admin", "password": "password"}
    response = csrf_post(
        flask_client,
        "/login",
        data=login_data,
        follow_redirects=True,
    )
    assert response.status_code == 200
    change_response = csrf_post(
        flask_client,
        "/change_password",
        data={"old_password": "password", "new_password": "password1234"},
        follow_redirects=True,
        source_url="/change_password",
    )
    assert b"Passwort ge\xc3\xa4ndert" in change_response.data


def test_wlan_connect_quotes_ascii_ssid(client, monkeypatch):
    flask_client, app_module = client
    calls = []

    def fake_run(args, **kwargs):
        assert args[0:3] == ["wpa_cli", "-i", "wlan0"]
        calls.append(args)
        if args[-1] == "add_network":
            return CompletedProcess(args, 0, stdout="1\n", stderr="")
        return CompletedProcess(args, 0, stdout="OK\n", stderr="")

    _login_admin(flask_client)
    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    response = csrf_post(
        flask_client,
        "/wlan_connect",
        data={"ssid": "My Wifi", "password": "secretpass"},
        follow_redirects=False,
        source_url="/change_password",
    )

    assert response.status_code == 302

    ssid_call = next(
        call
        for call in calls
        if len(call) > 6 and call[3] == "set_network" and call[5] == "ssid"
    )
    password_call = next(
        call
        for call in calls
        if len(call) > 6 and call[3] == "set_network" and call[5] == "psk"
    )

    assert ssid_call[6] == '"My Wifi"'
    assert password_call[6] == '"secretpass"'


def test_wlan_connect_preserves_leading_space(client, monkeypatch):
    flask_client, app_module = client
    calls = []

    def fake_run(args, **kwargs):
        assert args[0:3] == ["wpa_cli", "-i", "wlan0"]
        calls.append(args)
        if args[-1] == "add_network":
            return CompletedProcess(args, 0, stdout="7\n", stderr="")
        return CompletedProcess(args, 0, stdout="OK\n", stderr="")

    _login_admin(flask_client)
    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    response = csrf_post(
        flask_client,
        "/wlan_connect",
        data={"ssid": "LeadingSpace", "password": " secretpass"},
        follow_redirects=False,
        source_url="/change_password",
    )

    assert response.status_code == 302

    password_call = next(
        call
        for call in calls
        if len(call) > 6 and call[3] == "set_network" and call[5] == "psk"
    )
    assert password_call[6] == '" secretpass"'


def test_wlan_connect_hex_unicode_ssid(client, monkeypatch):
    flask_client, app_module = client
    calls = []

    def fake_run(args, **kwargs):
        assert args[0:3] == ["wpa_cli", "-i", "wlan0"]
        calls.append(args)
        if args[-1] == "add_network":
            return CompletedProcess(args, 0, stdout="2\n", stderr="")
        return CompletedProcess(args, 0, stdout="OK\n", stderr="")

    _login_admin(flask_client)
    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    response = csrf_post(
        flask_client,
        "/wlan_connect",
        data={"ssid": "Caf\u00e9 Netzwerk", "password": "secretpass"},
        follow_redirects=False,
        source_url="/change_password",
    )

    assert response.status_code == 302

    ssid_call = next(
        call
        for call in calls
        if len(call) > 6 and call[3] == "set_network" and call[5] == "ssid"
    )
    password_call = next(
        call
        for call in calls
        if len(call) > 6 and call[3] == "set_network" and call[5] == "psk"
    )

    assert ssid_call[6] == "0x436166c3a9204e65747a7765726b"
    assert password_call[6] == '"secretpass"'


def test_wlan_connect_open_network(client, monkeypatch):
    flask_client, app_module = client
    calls = []

    def fake_run(args, **kwargs):
        assert args[0:3] == ["wpa_cli", "-i", "wlan0"]
        calls.append(args)
        if args[-1] == "add_network":
            return CompletedProcess(args, 0, stdout="3\n", stderr="")
        return CompletedProcess(args, 0, stdout="OK\n", stderr="")

    _login_admin(flask_client)
    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    response = csrf_post(
        flask_client,
        "/wlan_connect",
        data={"ssid": "OpenNetwork", "password": ""},
        follow_redirects=False,
        source_url="/change_password",
    )

    assert response.status_code == 302

    set_network_calls = [
        call for call in calls if len(call) > 6 and call[3] == "set_network"
    ]

    key_mgmt_call = next(call for call in set_network_calls if call[5] == "key_mgmt")
    assert key_mgmt_call[6] == "NONE"

    auth_alg_call = next(call for call in set_network_calls if call[5] == "auth_alg")
    assert auth_alg_call[6] == "OPEN"

    assert all(call[5] != "psk" for call in set_network_calls)


def test_wlan_connect_missing_ssid_redirects_with_flash(client, monkeypatch):
    flask_client, app_module = client

    def fail_run(*args, **kwargs):
        pytest.fail("wpa_cli darf bei fehlender SSID nicht aufgerufen werden")

    _login_admin(flask_client)
    monkeypatch.setattr(app_module.subprocess, "run", fail_run)

    response = csrf_post(
        flask_client,
        "/wlan_connect",
        data={"password": "secretpass"},
        follow_redirects=False,
        source_url="/change_password",
    )

    assert response.status_code == 302
    assert "Location" in response.headers

    with flask_client.session_transaction() as session:
        flashes = session.get("_flashes", [])

    assert ("warning", "SSID darf nicht leer sein.") in flashes


def test_wlan_connect_all_space_passphrase(client, monkeypatch):
    flask_client, app_module = client
    calls = []

    def fake_run(args, **kwargs):
        assert args[0:3] == ["wpa_cli", "-i", "wlan0"]
        calls.append(args)
        if args[-1] == "add_network":
            return CompletedProcess(args, 0, stdout="4\n", stderr="")
        return CompletedProcess(args, 0, stdout="OK\n", stderr="")

    _login_admin(flask_client)
    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    response = csrf_post(
        flask_client,
        "/wlan_connect",
        data={"ssid": "SpacesOnly", "password": " " * 8},
        follow_redirects=False,
        source_url="/change_password",
    )

    assert response.status_code == 302

    set_network_calls = [
        call for call in calls if len(call) > 6 and call[3] == "set_network"
    ]

    password_call = next(call for call in set_network_calls if call[5] == "psk")
    assert password_call[6] == '"        "'


def test_wlan_connect_rejects_empty_ssid(client, monkeypatch):
    flask_client, app_module = client
    calls = []

    def fake_run_wpa_cli(*args, **kwargs):
        calls.append(args)
        return "OK"

    _login_admin(flask_client)
    monkeypatch.setattr(app_module, "_run_wpa_cli", fake_run_wpa_cli)

    response = csrf_post(
        flask_client,
        "/wlan_connect",
        data={"ssid": "   ", "password": "secretpass"},
        follow_redirects=True,
        source_url="/change_password",
    )

    assert b"SSID darf nicht leer sein" in response.data
    assert calls == []


def test_wlan_connect_rejects_short_passphrase(client, monkeypatch):
    flask_client, app_module = client
    calls = []

    def fake_run_wpa_cli(args, expect_ok=True):
        calls.append(args)
        return "OK"

    _login_admin(flask_client)
    monkeypatch.setattr(app_module, "_run_wpa_cli", fake_run_wpa_cli)

    response = csrf_post(
        flask_client,
        "/wlan_connect",
        data={"ssid": "TooShort", "password": "abc"},
        follow_redirects=True,
        source_url="/change_password",
    )

    assert b"Ung\xc3\xbcltiges WLAN-Passwort" in response.data
    assert calls == []


def test_wlan_connect_hex_psk_unquoted(client, monkeypatch):
    flask_client, app_module = client
    calls = []
    hex_psk = "0123456789abcdef" * 4

    def fake_run(args, **kwargs):
        assert args[0:3] == ["wpa_cli", "-i", "wlan0"]
        calls.append(args)
        if args[-1] == "add_network":
            return CompletedProcess(args, 0, stdout="5\n", stderr="")
        return CompletedProcess(args, 0, stdout="OK\n", stderr="")

    _login_admin(flask_client)
    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    response = csrf_post(
        flask_client,
        "/wlan_connect",
        data={"ssid": "HexNetwork", "password": hex_psk},
        follow_redirects=False,
        source_url="/change_password",
    )

    assert response.status_code == 302

    password_call = next(
        call
        for call in calls
        if len(call) > 6 and call[3] == "set_network" and call[5] == "psk"
    )
    assert password_call[6] == hex_psk


def test_wlan_connect_fail_stops_sequence(client, monkeypatch):
    flask_client, app_module = client
    calls = []

    def fake_run(args, **kwargs):
        assert args[0:3] == ["wpa_cli", "-i", "wlan0"]
        calls.append(args)
        if args[-1] == "add_network":
            return CompletedProcess(args, 0, stdout="6\n", stderr="")
        if args[3:6] == ["set_network", "6", "ssid"]:
            return CompletedProcess(args, 0, stdout="OK\n", stderr="")
        if args[3:6] == ["set_network", "6", "psk"]:
            return CompletedProcess(args, 0, stdout="FAIL invalid", stderr="")
        return CompletedProcess(args, 0, stdout="OK\n", stderr="")

    _login_admin(flask_client)
    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    response = csrf_post(
        flask_client,
        "/wlan_connect",
        data={"ssid": "FailNet", "password": "secretpass"},
        follow_redirects=False,
        source_url="/change_password",
    )

    assert response.status_code == 302
    enable_calls = [
        call for call in calls if len(call) > 3 and call[3] == "enable_network"
    ]
    assert enable_calls == []


def test_wlan_connect_removes_network_on_failure(client, monkeypatch):
    flask_client, app_module = client
    calls = []

    def fake_run_wpa_cli(args, expect_ok=True):
        calls.append((args, expect_ok))
        if args[-1] == "add_network":
            return "9"
        if args[3:6] == ["set_network", "9", "ssid"]:
            return "OK"
        if args[3:6] == ["set_network", "9", "psk"]:
            raise subprocess.CalledProcessError(
                1, args, output="FAIL invalid", stderr=""
            )
        if args[-2:] == ["remove_network", "9"]:
            return "OK"
        return "OK"

    _login_admin(flask_client)
    monkeypatch.setattr(app_module, "_run_wpa_cli", fake_run_wpa_cli)

    response = csrf_post(
        flask_client,
        "/wlan_connect",
        data={"ssid": "CleanupNet", "password": "secretpass"},
        follow_redirects=False,
        source_url="/change_password",
    )

    assert response.status_code == 302

    remove_calls = [
        entry for entry in calls if entry[0][-2:] == ["remove_network", "9"]
    ]
    assert len(remove_calls) == 1
    assert remove_calls[0][1] is False

    psk_call_index = next(
        index
        for index, (args, _expect_ok) in enumerate(calls)
        if args[3:6] == ["set_network", "9", "psk"]
    )
    remove_call_index = calls.index(remove_calls[0])
    assert remove_call_index > psk_call_index
