"""Tests für Netzwerk-Helfer und das Netzwerk-Formular."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

import pytest

from tests.csrf_utils import csrf_post


@pytest.fixture
def network_module(monkeypatch, tmp_path: Path):
    """Isoliert ``network_config`` mit einem temporären Arbeitsverzeichnis."""

    repo_root = Path(__file__).resolve().parents[1]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)
    if "network_config" in sys.modules:
        del sys.modules["network_config"]

    module = importlib.import_module("network_config")

    yield module

    if "network_config" in sys.modules:
        del sys.modules["network_config"]
    if repo_root_str in sys.path:
        sys.path.remove(repo_root_str)


def _write_conf(path: Path, lines: Iterable[str]) -> None:
    content = "\n".join(lines)
    if content and not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")


def test_load_network_settings_static_block(network_module, tmp_path: Path, monkeypatch):
    conf = tmp_path / "dhcpcd.conf"
    _write_conf(
        conf,
        [
            "# Bestehende Konfiguration",
            network_module.CLIENT_START_MARKER,
            "interface wlan0",
            "static ip_address=192.168.10.5/24",
            "static routers=192.168.10.1",
            "static domain_name_servers=1.1.1.1 8.8.8.8",
            "static domain_name=lan.local",
            network_module.CLIENT_END_MARKER,
        ],
    )

    monkeypatch.setattr(
        network_module,
        "get_current_hostname",
        lambda hostname_path=Path("/etc/hostname"): "audio-pi",
    )

    result = network_module.load_network_settings("wlan0", conf)

    assert result == {
        "mode": "manual",
        "ipv4_address": "192.168.10.5",
        "ipv4_prefix": "24",
        "ipv4_gateway": "192.168.10.1",
        "dns_servers": "1.1.1.1, 8.8.8.8",
        "local_domain": "lan.local",
        "hostname": "audio-pi",
    }


def test_load_network_settings_defaults_for_missing_block(
    network_module, tmp_path: Path, monkeypatch
):
    conf = tmp_path / "dhcpcd.conf"
    _write_conf(
        conf,
        [
            network_module.ACCESS_POINT_START_MARKER,
            "interface wlan0",
            "static ip_address=192.168.4.1/24",
            network_module.ACCESS_POINT_END_MARKER,
            "interface eth0",
            "static ip_address=10.0.0.5/24",
        ],
    )

    monkeypatch.setattr(
        network_module,
        "get_current_hostname",
        lambda hostname_path=Path("/etc/hostname"): "edge-device",
    )

    result = network_module.load_network_settings("wlan0", conf)

    assert result == {
        "mode": "dhcp",
        "ipv4_address": "",
        "ipv4_prefix": "",
        "ipv4_gateway": "",
        "dns_servers": "",
        "local_domain": "",
        "hostname": "edge-device",
    }


def test_write_network_settings_manual_appends_client_block(network_module, tmp_path: Path):
    conf = tmp_path / "dhcpcd.conf"
    _write_conf(
        conf,
        [
            "# Basis",
            "interface wlan0",
            "static ip_address=10.0.0.10/24",
            "static routers=10.0.0.1",
            "static domain_name_servers=9.9.9.9",
            "",
            network_module.ACCESS_POINT_START_MARKER,
            "interface wlan0",
            "static ip_address=192.168.4.1/24",
            network_module.ACCESS_POINT_END_MARKER,
        ],
    )

    normalized = network_module.write_network_settings(
        "wlan0",
        {
            "mode": "manual",
            "ipv4_address": "192.168.1.20",
            "ipv4_prefix": "24",
            "ipv4_gateway": "192.168.1.1",
            "dns_servers": "8.8.8.8 1.1.1.1",
            "local_domain": "example.lan",
        },
        conf,
    )

    assert normalized == {
        "mode": "manual",
        "ipv4_address": "192.168.1.20",
        "ipv4_prefix": "24",
        "ipv4_gateway": "192.168.1.1",
        "dns_servers": "8.8.8.8, 1.1.1.1",
        "local_domain": "example.lan",
    }

    rendered = conf.read_text(encoding="utf-8").splitlines()
    assert rendered == [
        "# Basis",
        "interface wlan0",
        "",
        network_module.ACCESS_POINT_START_MARKER,
        "interface wlan0",
        "static ip_address=192.168.4.1/24",
        network_module.ACCESS_POINT_END_MARKER,
        "",
        network_module.CLIENT_START_MARKER,
        "interface wlan0",
        "static ip_address=192.168.1.20/24",
        "static routers=192.168.1.1",
        "static domain_name_servers=8.8.8.8 1.1.1.1",
        "static domain_name=example.lan",
        network_module.CLIENT_END_MARKER,
    ]

    backups = list(conf.parent.glob("dhcpcd.conf.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8").startswith("# Basis\ninterface wlan0")


def test_write_network_settings_dhcp_removes_client_block(network_module, tmp_path: Path):
    conf = tmp_path / "dhcpcd.conf"
    _write_conf(
        conf,
        [
            "# Header",
            network_module.CLIENT_START_MARKER,
            "interface wlan0",
            "static ip_address=192.168.1.2/24",
            "static routers=192.168.1.1",
            network_module.CLIENT_END_MARKER,
            "interface eth0",
            "static ip_address=10.0.0.2/24",
        ],
    )

    normalized = network_module.write_network_settings(
        "wlan0",
        {
            "mode": "dhcp",
            "ipv4_address": "",
            "ipv4_prefix": "",
            "ipv4_gateway": "",
            "dns_servers": "",
            "local_domain": "",
        },
        conf,
    )

    assert normalized == {
        "mode": "dhcp",
        "ipv4_address": "",
        "ipv4_prefix": "",
        "ipv4_gateway": "",
        "dns_servers": "",
        "local_domain": "",
    }

    rendered = conf.read_text(encoding="utf-8").splitlines()
    assert rendered == [
        "# Header",
        "interface eth0",
        "static ip_address=10.0.0.2/24",
    ]

    backups = list(conf.parent.glob("dhcpcd.conf.bak.*"))
    assert len(backups) == 1


# ---------------------------------------------------------------------------
# Flask-Client-Tests


@pytest.fixture
def app_module(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("FLASK_SECRET_KEY", "testkey")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DB_FILE", str(tmp_path / "test.db"))
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "password")

    repo_root = Path(__file__).resolve().parents[1]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)
    if "app" in sys.modules:
        del sys.modules["app"]

    module = importlib.import_module("app")
    importlib.reload(module)

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    module.app.config["UPLOAD_FOLDER"] = str(upload_dir)
    if hasattr(module, "pygame_available"):
        module.pygame_available = False
    if hasattr(module, "pygame"):
        module.pygame = None

    yield module

    if hasattr(module, "conn") and module.conn is not None:
        module.conn.close()
    if "app" in sys.modules:
        del sys.modules["app"]
    if repo_root_str in sys.path:
        sys.path.remove(repo_root_str)


@pytest.fixture
def client(app_module):
    with app_module.app.test_client() as test_client:
        yield test_client, app_module


def _login(client):
    response = csrf_post(
        client,
        "/login",
        data={"username": "admin", "password": "password"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    change_response = csrf_post(
        client,
        "/change_password",
        data={"old_password": "password", "new_password": "password1234"},
        follow_redirects=True,
        source_url="/change_password",
    )
    assert change_response.status_code == 200
    assert b"Passwort ge\xc3\xa4ndert" in change_response.data


def test_network_settings_post_dhcp(monkeypatch, client):
    test_client, app_module = client
    _login(test_client)

    captured: Dict[str, Any] = {}

    def fake_write(interface: str, payload: Mapping[str, str]):
        captured["interface"] = interface
        captured["payload"] = dict(payload)
        return {
            "mode": "dhcp",
            "ipv4_address": "",
            "ipv4_prefix": "",
            "ipv4_gateway": "",
            "dns_servers": "",
            "local_domain": "",
        }

    monkeypatch.setattr(app_module, "_write_network_settings", fake_write)
    monkeypatch.setattr(app_module, "_get_current_hostname", lambda: "audio-pi")

    host_updates: List[Tuple[str, str]] = []

    def fake_update_hosts(hostname: str, local_domain: str):
        host_updates.append((hostname, local_domain))
        return True

    monkeypatch.setattr(app_module, "_update_hosts_file", fake_update_hosts)

    command_calls: List[Tuple[str, ...]] = []

    def fake_privileged_command(*args: str) -> List[str]:
        command_calls.append(tuple(args))
        return list(args)

    monkeypatch.setattr(app_module, "privileged_command", fake_privileged_command)

    original_run = app_module.subprocess.run

    def guard_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:2] == ["hostnamectl", "set-hostname"]:
            raise AssertionError("hostnamectl sollte bei DHCP nicht aufgerufen werden")
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(app_module.subprocess, "run", guard_run)

    response = csrf_post(
        test_client,
        "/network_settings",
        data={
            "mode": "dhcp",
            "hostname": "audio-pi",
            "ipv4_address": "",
            "ipv4_prefix": "",
            "ipv4_gateway": "",
            "dns_servers": "",
            "local_domain": "",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"DHCP-Konfiguration aktiviert." in response.data
    assert captured["interface"] == "wlan0"
    assert captured["payload"] == {
        "mode": "dhcp",
        "ipv4_address": "",
        "ipv4_prefix": "",
        "ipv4_gateway": "",
        "dns_servers": "",
        "local_domain": "",
        "hostname": "audio-pi",
    }
    assert host_updates == [("audio-pi", "")]
    assert command_calls == []


def test_network_settings_post_static_triggers_hostnamectl(monkeypatch, client):
    test_client, app_module = client
    _login(test_client)

    payloads: List[Mapping[str, str]] = []

    def fake_write(interface: str, payload: Mapping[str, str]):
        payloads.append(dict(payload))
        assert interface == "wlan0"
        return {
            "mode": "manual",
            "ipv4_address": "192.168.20.5",
            "ipv4_prefix": "24",
            "ipv4_gateway": "192.168.20.1",
            "dns_servers": "1.1.1.1, 8.8.4.4",
            "local_domain": "studio.lan",
        }

    monkeypatch.setattr(app_module, "_write_network_settings", fake_write)
    monkeypatch.setattr(app_module, "_get_current_hostname", lambda: "old-host")

    host_updates: List[Tuple[str, str]] = []

    def fake_update_hosts(hostname: str, local_domain: str):
        host_updates.append((hostname, local_domain))
        return True

    monkeypatch.setattr(app_module, "_update_hosts_file", fake_update_hosts)

    command_calls: List[Tuple[str, ...]] = []

    def fake_privileged_command(*args: str) -> List[str]:
        command_calls.append(tuple(args))
        return list(args)

    monkeypatch.setattr(app_module, "privileged_command", fake_privileged_command)

    original_run = app_module.subprocess.run

    class DummyResult:
        def __init__(self):
            self.returncode = 0
            self.stdout = ""
            self.stderr = ""

    run_calls: List[List[str]] = []

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:2] == ["hostnamectl", "set-hostname"]:
            run_calls.append(list(cmd))
            return DummyResult()
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    response = csrf_post(
        test_client,
        "/network_settings",
        data={
            "mode": "manual",
            "ipv4_address": "192.168.20.5",
            "ipv4_prefix": "24",
            "ipv4_gateway": "192.168.20.1",
            "dns_servers": "1.1.1.1,8.8.4.4",
            "hostname": "studio-pi",
            "local_domain": "studio.lan",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Statische IPv4-Konfiguration gespeichert." in response.data
    assert len(payloads) == 1
    assert payloads[0] == {
        "mode": "manual",
        "ipv4_address": "192.168.20.5",
        "ipv4_prefix": "24",
        "ipv4_gateway": "192.168.20.1",
        "dns_servers": "1.1.1.1,8.8.4.4",
        "hostname": "studio-pi",
        "local_domain": "studio.lan",
    }
    assert command_calls == [("hostnamectl", "set-hostname", "studio-pi")]
    assert run_calls == [["hostnamectl", "set-hostname", "studio-pi"]]
    assert host_updates == [("studio-pi", "studio.lan")]


def test_network_settings_post_static_invalid_ip(monkeypatch, client):
    test_client, app_module = client
    _login(test_client)

    def fake_write(interface: str, payload: Mapping[str, str]):
        raise app_module.NetworkConfigError("Ungültige IPv4-Adresse oder Präfix.")

    monkeypatch.setattr(app_module, "_write_network_settings", fake_write)
    monkeypatch.setattr(app_module, "privileged_command", lambda *args: list(args))

    response = csrf_post(
        test_client,
        "/network_settings",
        data={
            "mode": "manual",
            "ipv4_address": "",
            "ipv4_prefix": "24",
            "ipv4_gateway": "192.168.30.1",
            "dns_servers": "1.1.1.1",
            "hostname": "faulty",
            "local_domain": "lab.local",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Ung\xc3\xbcltige IPv4-Adresse oder Pr\xc3\xa4fix." in response.data
