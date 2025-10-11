import importlib
import sys
from pathlib import Path

import pytest


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_SECRET_KEY", "testkey")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DB_FILE", str(tmp_path / "test.db"))

    repo_root = Path(__file__).resolve().parents[1]
    repo_path = str(repo_root)
    if repo_path not in sys.path:
        sys.path.insert(0, repo_path)
    if "app" in sys.modules:
        del sys.modules["app"]
    app_module = importlib.import_module("app")

    with app_module.app.test_client() as test_client:
        yield test_client, app_module

    if hasattr(app_module, "conn") and app_module.conn is not None:
        try:
            app_module.conn.close()
        except Exception:
            pass


def _login_admin(flask_client):
    login_data = {"username": "admin", "password": "password"}
    response = flask_client.post("/login", data=login_data, follow_redirects=False)
    assert response.status_code == 302


def test_wlan_connect_quotes_ascii_ssid(client, monkeypatch):
    flask_client, app_module = client
    calls = []

    def fake_check_output(args, **kwargs):
        assert args == ["sudo", "wpa_cli", "-i", "wlan0", "add_network"]
        return b"1\n"

    def fake_check_call(args, **kwargs):
        calls.append(args)
        return 0

    _login_admin(flask_client)
    monkeypatch.setattr(app_module.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(app_module.subprocess, "check_call", fake_check_call)
    response = flask_client.post(
        "/wlan_connect",
        data={"ssid": "My Wifi", "password": "secretpass"},
        follow_redirects=False,
    )

    assert response.status_code == 302

    ssid_call = next(call for call in calls if call[6] == "ssid")
    password_call = next(call for call in calls if call[6] == "psk")

    assert ssid_call[7] == '"My Wifi"'
    assert password_call[7] == '"secretpass"'


def test_wlan_connect_hex_unicode_ssid(client, monkeypatch):
    flask_client, app_module = client
    calls = []

    def fake_check_output(args, **kwargs):
        assert args == ["sudo", "wpa_cli", "-i", "wlan0", "add_network"]
        return b"2\n"

    def fake_check_call(args, **kwargs):
        calls.append(args)
        return 0

    _login_admin(flask_client)
    monkeypatch.setattr(app_module.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(app_module.subprocess, "check_call", fake_check_call)
    response = flask_client.post(
        "/wlan_connect",
        data={"ssid": "Caf\u00e9 Netzwerk", "password": "secretpass"},
        follow_redirects=False,
    )

    assert response.status_code == 302

    ssid_call = next(call for call in calls if call[6] == "ssid")
    password_call = next(call for call in calls if call[6] == "psk")

    assert ssid_call[7] == "0x436166c3a9204e65747a7765726b"
    assert password_call[7] == '"secretpass"'


def test_wlan_connect_open_network(client, monkeypatch):
    flask_client, app_module = client
    calls = []

    def fake_check_output(args, **kwargs):
        assert args == ["sudo", "wpa_cli", "-i", "wlan0", "add_network"]
        return b"3\n"

    def fake_check_call(args, **kwargs):
        calls.append(args)
        return 0

    _login_admin(flask_client)
    monkeypatch.setattr(app_module.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(app_module.subprocess, "check_call", fake_check_call)

    response = flask_client.post(
        "/wlan_connect",
        data={"ssid": "OpenNetwork", "password": "   "},
        follow_redirects=False,
    )

    assert response.status_code == 302

    set_network_calls = [call for call in calls if len(call) > 6 and call[4] == "set_network"]

    key_mgmt_call = next(call for call in set_network_calls if call[6] == "key_mgmt")
    assert key_mgmt_call[7] == "NONE"

    auth_alg_call = next(call for call in set_network_calls if call[6] == "auth_alg")
    assert auth_alg_call[7] == "OPEN"

    assert all(call[6] != "psk" for call in set_network_calls)
