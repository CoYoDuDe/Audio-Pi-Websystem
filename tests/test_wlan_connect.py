import importlib
import sys
from pathlib import Path

import pytest
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
        assert args[0:4] == ["sudo", "wpa_cli", "-i", "wlan0"]
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

    ssid_call = next(call for call in calls if len(call) > 6 and call[6] == "ssid")
    password_call = next(call for call in calls if len(call) > 6 and call[6] == "psk")

    assert ssid_call[7] == '"My Wifi"'
    assert password_call[7] == '"secretpass"'


def test_wlan_connect_preserves_leading_space(client, monkeypatch):
    flask_client, app_module = client
    calls = []

    def fake_run(args, **kwargs):
        assert args[0:4] == ["sudo", "wpa_cli", "-i", "wlan0"]
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

    password_call = next(call for call in calls if len(call) > 6 and call[6] == "psk")
    assert password_call[7] == '" secretpass"'


def test_wlan_connect_hex_unicode_ssid(client, monkeypatch):
    flask_client, app_module = client
    calls = []

    def fake_run(args, **kwargs):
        assert args[0:4] == ["sudo", "wpa_cli", "-i", "wlan0"]
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

    ssid_call = next(call for call in calls if len(call) > 6 and call[6] == "ssid")
    password_call = next(call for call in calls if len(call) > 6 and call[6] == "psk")

    assert ssid_call[7] == "0x436166c3a9204e65747a7765726b"
    assert password_call[7] == '"secretpass"'


def test_wlan_connect_open_network(client, monkeypatch):
    flask_client, app_module = client
    calls = []

    def fake_run(args, **kwargs):
        assert args[0:4] == ["sudo", "wpa_cli", "-i", "wlan0"]
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

    set_network_calls = [call for call in calls if len(call) > 6 and call[4] == "set_network"]

    key_mgmt_call = next(call for call in set_network_calls if call[6] == "key_mgmt")
    assert key_mgmt_call[7] == "NONE"

    auth_alg_call = next(call for call in set_network_calls if call[6] == "auth_alg")
    assert auth_alg_call[7] == "OPEN"

    assert all(call[6] != "psk" for call in set_network_calls)


def test_wlan_connect_all_space_passphrase(client, monkeypatch):
    flask_client, app_module = client
    calls = []

    def fake_run(args, **kwargs):
        assert args[0:4] == ["sudo", "wpa_cli", "-i", "wlan0"]
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

    set_network_calls = [call for call in calls if len(call) > 6 and call[4] == "set_network"]

    password_call = next(call for call in set_network_calls if call[6] == "psk")
    assert password_call[7] == '"        "'



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
        assert args[0:4] == ["sudo", "wpa_cli", "-i", "wlan0"]
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

    password_call = next(call for call in calls if len(call) > 6 and call[6] == "psk")
    assert password_call[7] == hex_psk


def test_wlan_connect_fail_stops_sequence(client, monkeypatch):
    flask_client, app_module = client
    calls = []

    def fake_run(args, **kwargs):
        assert args[0:4] == ["sudo", "wpa_cli", "-i", "wlan0"]
        calls.append(args)
        if args[-1] == "add_network":
            return CompletedProcess(args, 0, stdout="6\n", stderr="")
        if args[4:7] == ["set_network", "6", "ssid"]:
            return CompletedProcess(args, 0, stdout="OK\n", stderr="")
        if args[4:7] == ["set_network", "6", "psk"]:
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
        call for call in calls if len(call) > 4 and call[4] == "enable_network"
    ]
    assert enable_calls == []
