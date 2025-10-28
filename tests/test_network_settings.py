"""Tests für Netzwerk-Helfer und das Netzwerk-Formular."""

from __future__ import annotations

import importlib
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import contextmanager
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


def test_load_network_settings_inline_comments(network_module, tmp_path: Path, monkeypatch):
    conf = tmp_path / "dhcpcd.conf"
    _write_conf(
        conf,
        [
            "interface wlan0",
            "static ip_address=192.168.50.20/24   # primäre Adresse",
            "static routers=192.168.50.1    # Gateway",
            "static domain_name_servers=9.9.9.9  1.1.1.1   # bevorzugte DNS",
            "static domain_name=lan.example   # Kommentar",
        ],
    )

    monkeypatch.setattr(
        network_module,
        "get_current_hostname",
        lambda hostname_path=Path("/etc/hostname"): "studio-pi",
    )

    settings = network_module.load_network_settings("wlan0", conf)

    assert settings == {
        "mode": "manual",
        "ipv4_address": "192.168.50.20",
        "ipv4_prefix": "24",
        "ipv4_gateway": "192.168.50.1",
        "dns_servers": "9.9.9.9, 1.1.1.1",
        "local_domain": "lan.example",
        "hostname": "studio-pi",
    }

    written = network_module.write_network_settings("wlan0", settings, conf)

    assert written == {
        "mode": "manual",
        "ipv4_address": "192.168.50.20",
        "ipv4_prefix": "24",
        "ipv4_gateway": "192.168.50.1",
        "dns_servers": "9.9.9.9, 1.1.1.1",
        "local_domain": "lan.example",
    }

    lines = conf.read_text(encoding="utf-8").splitlines()
    assert "static domain_name_servers=9.9.9.9 1.1.1.1" in lines
    assert "static domain_name=lan.example" in lines or all(
        not line.strip().startswith("static domain_name=") for line in lines
    )


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


def test_write_network_settings_backup_fallback_directory(
    network_module, tmp_path: Path, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    conf_dir = tmp_path / "etc"
    conf_dir.mkdir()
    conf = conf_dir / "dhcpcd.conf"
    original_lines = [
        "interface wlan0",
        "static ip_address=10.0.0.5/24",
        "static routers=10.0.0.1",
    ]
    _write_conf(conf, original_lines)

    real_copy2 = shutil.copy2

    def guarded_copy2(src, dst, *args, **kwargs):
        src_path = Path(src)
        dst_path = Path(dst)
        if src_path == conf and dst_path.parent == conf.parent:
            raise PermissionError("Zielverzeichnis ist schreibgeschützt")
        return real_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr(network_module.shutil, "copy2", guarded_copy2)

    normalized_result = network_module.normalize_network_settings(
        "wlan0",
        {
            "mode": "manual",
            "ipv4_address": "192.168.1.20",
            "ipv4_prefix": "24",
            "ipv4_gateway": "192.168.1.1",
            "dns_servers": "1.1.1.1 9.9.9.9",
            "local_domain": "lan.local",
        },
        conf,
    )

    backup_path = normalized_result.backup_path
    assert backup_path is not None
    assert backup_path.parent != conf.parent
    assert backup_path.exists()

    written = network_module.write_network_settings(
        "wlan0",
        {
            "mode": "manual",
            "ipv4_address": "192.168.1.20",
            "ipv4_prefix": "24",
            "ipv4_gateway": "192.168.1.1",
            "dns_servers": "1.1.1.1 9.9.9.9",
            "local_domain": "lan.local",
        },
        conf,
        normalized_result=normalized_result,
    )

    assert written == {
        "mode": "manual",
        "ipv4_address": "192.168.1.20",
        "ipv4_prefix": "24",
        "ipv4_gateway": "192.168.1.1",
        "dns_servers": "1.1.1.1, 9.9.9.9",
        "local_domain": "lan.local",
    }

    assert conf.read_text(encoding="utf-8").splitlines() != original_lines
    assert backup_path.exists()

    network_module.restore_network_backup(normalized_result)

    assert conf.read_text(encoding="utf-8").splitlines() == original_lines
    assert normalized_result.backup_path is None
    assert not backup_path.exists()


def test_write_network_settings_invalid_ipv4(network_module, tmp_path: Path):
    conf = tmp_path / "dhcpcd.conf"
    original_lines = ["interface wlan0", "static ip_address=10.0.0.5/24"]
    _write_conf(conf, original_lines)

    with pytest.raises(network_module.NetworkConfigError) as excinfo:
        network_module.write_network_settings(
            "wlan0",
            {
                "mode": "manual",
                "ipv4_address": "300.1.1.1",
                "ipv4_prefix": "24",
                "ipv4_gateway": "192.168.1.1",
                "dns_servers": "8.8.8.8",
                "local_domain": "",
            },
            conf,
        )

    assert "IPv4-Adresse" in str(excinfo.value)
    assert conf.read_text(encoding="utf-8").splitlines() == original_lines
    assert list(conf.parent.glob("dhcpcd.conf.bak.*")) == []


def test_write_network_settings_invalid_gateway(network_module, tmp_path: Path):
    conf = tmp_path / "dhcpcd.conf"
    original_lines = ["interface wlan0", "static ip_address=10.0.0.5/24"]
    _write_conf(conf, original_lines)

    with pytest.raises(network_module.NetworkConfigError) as excinfo:
        network_module.write_network_settings(
            "wlan0",
            {
                "mode": "manual",
                "ipv4_address": "192.168.1.5",
                "ipv4_prefix": "24",
                "ipv4_gateway": "192.168.2.1",
                "dns_servers": "8.8.8.8",
                "local_domain": "",
            },
            conf,
        )

    assert "Gateway" in str(excinfo.value)
    assert conf.read_text(encoding="utf-8").splitlines() == original_lines
    assert list(conf.parent.glob("dhcpcd.conf.bak.*")) == []


def test_write_network_settings_invalid_dns(network_module, tmp_path: Path):
    conf = tmp_path / "dhcpcd.conf"
    original_lines = ["interface wlan0", "static ip_address=10.0.0.5/24"]
    _write_conf(conf, original_lines)

    with pytest.raises(network_module.NetworkConfigError) as excinfo:
        network_module.write_network_settings(
            "wlan0",
            {
                "mode": "manual",
                "ipv4_address": "192.168.1.5",
                "ipv4_prefix": "24",
                "ipv4_gateway": "192.168.1.1",
                "dns_servers": "1.1.1.1,invalid",  # Kommagetrennte Eingabe wie im Formular
                "local_domain": "",
            },
            conf,
        )

    assert "DNS-Server" in str(excinfo.value)
    assert conf.read_text(encoding="utf-8").splitlines() == original_lines
    assert list(conf.parent.glob("dhcpcd.conf.bak.*")) == []


def test_write_network_settings_invalid_domain(network_module, tmp_path: Path):
    conf = tmp_path / "dhcpcd.conf"
    original_lines = ["interface wlan0", "static ip_address=10.0.0.5/24"]
    _write_conf(conf, original_lines)

    with pytest.raises(network_module.NetworkConfigError) as excinfo:
        network_module.write_network_settings(
            "wlan0",
            {
                "mode": "manual",
                "ipv4_address": "192.168.1.5",
                "ipv4_prefix": "24",
                "ipv4_gateway": "192.168.1.1",
                "dns_servers": "1.1.1.1",
                "local_domain": "bad_domain",  # Unterstrich ist ungültig
            },
            conf,
        )

    assert "Domain" in str(excinfo.value)
    assert conf.read_text(encoding="utf-8").splitlines() == original_lines
    assert list(conf.parent.glob("dhcpcd.conf.bak.*")) == []


def test_write_network_settings_permission_error_hint(
    network_module, tmp_path: Path, monkeypatch
):
    su_binary = shutil.which("su")
    if su_binary is None:
        pytest.skip("'su' steht nicht zur Verfügung")

    shared_dir = Path(tempfile.mkdtemp(prefix="network-permission-", dir="/tmp"))
    conf_dir = shared_dir / "restricted"
    conf_dir.mkdir()
    conf = conf_dir / "dhcpcd.conf"
    _write_conf(
        conf,
        [
            "interface wlan0",
            "static ip_address=10.0.0.5/24",
            "static routers=10.0.0.1",
        ],
    )

    os.chmod(conf_dir, 0o555)
    os.chmod(conf, 0o444)

    script_path = shared_dir / "permission_check.py"
    script_path.write_text(
        "from __future__ import annotations\n"
        "import sys\n"
        "from pathlib import Path\n"
        "import network_config\n"
        "conf_path = Path(sys.argv[1])\n"
        "try:\n"
        "    network_config.write_network_settings(\n"
        "        'wlan0',\n"
        "        {\n"
        "            'mode': 'manual',\n"
        "            'ipv4_address': '192.168.1.20',\n"
        "            'ipv4_prefix': '24',\n"
        "            'ipv4_gateway': '192.168.1.1',\n"
        "            'dns_servers': '1.1.1.1 9.9.9.9',\n"
        "            'local_domain': 'lan.local',\n"
        "        },\n"
        "        conf_path,\n"
        "    )\n"
        "except network_config.NetworkConfigError as exc:\n"
        "    print(exc)\n"
        "    sys.exit(0)\n"
        "except Exception as exc:\n"
        "    print(f'UNEXPECTED: {exc}', file=sys.stderr)\n"
        "    sys.exit(2)\n"
        "else:\n"
        "    print('NO_ERROR', file=sys.stderr)\n"
        "    sys.exit(1)\n",
        encoding="utf-8",
    )
    os.chmod(script_path, 0o755)

    repo_root = Path(__file__).resolve().parents[1]
    try:
        os.chmod(shared_dir, 0o755)
        monkeypatch.chdir(shared_dir)
        command = (
            f"PYTHONPATH={shlex.quote(str(repo_root))} "
            f"python3 {shlex.quote(str(script_path))} {shlex.quote(str(conf))}"
        )
        result = subprocess.run(
            [su_binary, "nobody", "-s", "/bin/sh", "-c", command],
            capture_output=True,
            text=True,
            cwd=str(shared_dir),
            check=False,
        )
    finally:
        os.chmod(conf_dir, 0o755)
        os.chmod(conf, 0o644)
        os.chmod(shared_dir, 0o755)
        shutil.rmtree(shared_dir, ignore_errors=True)

    assert result.returncode == 0, result.stderr or result.stdout
    message = result.stdout.strip()
    assert message
    assert str(conf) in message or str(conf_dir) in message
    assert "Installationsskript" in message


def test_write_network_settings_restores_backup_on_failure(
    network_module, tmp_path: Path, monkeypatch
):
    conf = tmp_path / "dhcpcd.conf"
    original_lines = [
        "# Kommentar",
        "interface wlan0",
        "static ip_address=10.0.0.5/24",
        "static routers=10.0.0.1",
    ]
    _write_conf(conf, original_lines)

    backups_before = list(conf.parent.glob("dhcpcd.conf.bak.*"))
    assert backups_before == []

    def failing_chmod(path, mode, **kwargs):
        raise OSError("chmod fehlgeschlagen")

    monkeypatch.setattr(network_module.os, "chmod", failing_chmod)

    with pytest.raises(network_module.NetworkConfigError) as excinfo:
        network_module.write_network_settings(
            "wlan0",
            {
                "mode": "manual",
                "ipv4_address": "192.168.1.20",
                "ipv4_prefix": "24",
                "ipv4_gateway": "192.168.1.1",
                "dns_servers": "8.8.8.8 1.1.1.1",
                "local_domain": "lan.local",
            },
            conf,
        )

    assert "Fehler beim Schreiben der Netzwerkkonfiguration" in str(excinfo.value)
    assert conf.read_text(encoding="utf-8").splitlines() == original_lines

    backups = list(conf.parent.glob("dhcpcd.conf.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8").splitlines() == original_lines


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


def test_normalize_network_settings_prepares_without_write(network_module, tmp_path: Path):
    conf = tmp_path / "dhcpcd.conf"
    original_content = [
        "# Header",
        "interface wlan0",
        "static ip_address=10.0.0.10/24",
        "static routers=10.0.0.1",
    ]
    _write_conf(conf, original_content)

    result = network_module.normalize_network_settings(
        "wlan0",
        {
            "mode": "manual",
            "ipv4_address": "192.168.50.2",
            "ipv4_prefix": "24",
            "ipv4_gateway": "192.168.50.1",
            "dns_servers": "1.1.1.1 8.8.8.8",
            "local_domain": "lab.lan",
        },
        conf,
    )

    assert result.requires_update is True
    assert result.normalized == {
        "mode": "manual",
        "ipv4_address": "192.168.50.2",
        "ipv4_prefix": "24",
        "ipv4_gateway": "192.168.50.1",
        "dns_servers": "1.1.1.1, 8.8.8.8",
        "local_domain": "lab.lan",
    }
    assert conf.read_text(encoding="utf-8").splitlines() == original_content
    assert result.backup_path is not None
    assert result.backup_path.exists()


def test_write_network_settings_restores_on_failure(network_module, tmp_path: Path, monkeypatch):
    conf = tmp_path / "dhcpcd.conf"
    _write_conf(
        conf,
        [
            "interface wlan0",
            "static ip_address=10.0.0.10/24",
            "static routers=10.0.0.1",
        ],
    )

    settings = {
        "mode": "manual",
        "ipv4_address": "192.168.60.2",
        "ipv4_prefix": "24",
        "ipv4_gateway": "192.168.60.1",
        "dns_servers": "9.9.9.9",
        "local_domain": "studio.local",
    }

    result = network_module.normalize_network_settings("wlan0", settings, conf)
    original_text = conf.read_text(encoding="utf-8")

    original_write = network_module._write_lines

    def failing_write(path: Path, lines: Iterable[str], *, create_backup: bool = True):
        original_write(path, lines, create_backup=create_backup)
        raise OSError("simulierter Fehler")

    monkeypatch.setattr(network_module, "_write_lines", failing_write)

    with pytest.raises(OSError):
        network_module.write_network_settings(
            "wlan0",
            settings,
            conf,
            normalized_result=result,
        )

    assert conf.read_text(encoding="utf-8") == original_text


def test_write_network_settings_restores_when_file_missing(network_module, tmp_path: Path, monkeypatch):
    conf = tmp_path / "dhcpcd.conf"

    settings = {
        "mode": "manual",
        "ipv4_address": "192.168.70.2",
        "ipv4_prefix": "24",
        "ipv4_gateway": "192.168.70.1",
        "dns_servers": "4.4.4.4",
        "local_domain": "demo.lan",
    }

    result = network_module.normalize_network_settings("wlan0", settings, conf)

    original_write = network_module._write_lines

    def failing_write(path: Path, lines: Iterable[str], *, create_backup: bool = True):
        original_write(path, lines, create_backup=create_backup)
        raise OSError("simulierter Fehler")

    monkeypatch.setattr(network_module, "_write_lines", failing_write)

    with pytest.raises(OSError):
        network_module.write_network_settings(
            "wlan0",
            settings,
            conf,
            normalized_result=result,
        )

    assert not conf.exists()
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

    normalized_result = app_module.NormalizedNetworkSettings(
        interface="wlan0",
        normalized={
            "mode": "dhcp",
            "ipv4_address": "",
            "ipv4_prefix": "",
            "ipv4_gateway": "",
            "dns_servers": "",
            "local_domain": "",
        },
        original_lines=[],
        new_lines=[],
        dhcpcd_path=Path("/tmp/dhcpcd.conf"),
        backup_path=None,
        original_exists=False,
    )

    def fake_normalize(interface: str, payload: Mapping[str, str]):
        assert interface == "wlan0"
        captured["normalized_payload"] = dict(payload)
        return normalized_result

    def fake_write(
        interface: str,
        payload: Mapping[str, str],
        *,
        normalized_result: Any = None,
    ):
        captured["interface"] = interface
        captured["payload"] = dict(payload)
        assert normalized_result is normalized_result_obj
        return {
            "mode": "dhcp",
            "ipv4_address": "",
            "ipv4_prefix": "",
            "ipv4_gateway": "",
            "dns_servers": "",
            "local_domain": "",
        }

    normalized_result_obj = normalized_result
    monkeypatch.setattr(app_module, "_normalize_network_settings", fake_normalize)
    monkeypatch.setattr(app_module, "_write_network_settings", fake_write)
    monkeypatch.setattr(app_module, "_get_current_hostname", lambda: "audio-pi")

    host_updates: List[Tuple[str, str]] = []

    def fake_update_hosts(hostname: str, local_domain: str):
        host_updates.append((hostname, local_domain))
        return app_module.HostsUpdateResult(
            hosts_path=Path("/tmp/hosts"),
            changed=False,
            backup_path=None,
            original_exists=True,
            original_lines=[],
        )

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
    assert captured["normalized_payload"] == captured["payload"]
    assert host_updates == [("audio-pi", "")]
    assert command_calls == []


def test_network_settings_post_dhcp_keeps_local_domain(monkeypatch, client):
    test_client, app_module = client
    _login(test_client)

    normalized_result = app_module.NormalizedNetworkSettings(
        interface="wlan0",
        normalized={
            "mode": "dhcp",
            "ipv4_address": "",
            "ipv4_prefix": "",
            "ipv4_gateway": "",
            "dns_servers": "",
            "local_domain": "",
        },
        original_lines=[],
        new_lines=[],
        dhcpcd_path=Path("/tmp/dhcpcd.conf"),
        backup_path=None,
        original_exists=False,
    )

    payload_capture: Dict[str, Any] = {}

    def fake_normalize(interface: str, payload: Mapping[str, str]):
        assert interface == "wlan0"
        payload_capture["normalized_payload"] = dict(payload)
        return normalized_result

    def fake_write(
        interface: str,
        payload: Mapping[str, str],
        *,
        normalized_result: Any = None,
    ):
        assert interface == "wlan0"
        payload_capture["write_payload"] = dict(payload)
        assert normalized_result is normalized_result_obj
        return dict(normalized_result_obj.normalized)

    normalized_result_obj = normalized_result
    monkeypatch.setattr(app_module, "_normalize_network_settings", fake_normalize)
    monkeypatch.setattr(app_module, "_write_network_settings", fake_write)
    monkeypatch.setattr(app_module, "_get_current_hostname", lambda: "audio-pi")

    host_updates: List[Tuple[str, str]] = []

    def fake_update_hosts(hostname: str, local_domain: str):
        host_updates.append((hostname, local_domain))
        return app_module.HostsUpdateResult(
            hosts_path=Path("/tmp/hosts"),
            changed=True,
            backup_path=None,
            original_exists=True,
            original_lines=["127.0.1.1\taudio-pi"],
        )

    monkeypatch.setattr(app_module, "_update_hosts_file", fake_update_hosts)

    stored_settings: Dict[str, str] = {}

    original_get_db_connection = app_module.get_db_connection

    @contextmanager
    def capture_get_db_connection():
        with original_get_db_connection() as (conn, cursor):
            original_execute = cursor.execute

            def capturing_execute(sql: str, params=None):
                if params and sql.strip().lower().startswith(
                    "insert or replace into settings"
                ):
                    key, value = params
                    stored_settings[key] = value
                if params is None:
                    return original_execute(sql)
                return original_execute(sql, params)

            class _CursorProxy:
                def __init__(self, inner):
                    self._inner = inner

                def execute(self, sql: str, params=None):
                    return capturing_execute(sql, params)

                def __getattr__(self, item):
                    return getattr(self._inner, item)

            class _ConnectionProxy:
                def __init__(self, inner, cursor_proxy):
                    self._inner = inner
                    self._cursor_proxy = cursor_proxy

                def cursor(self):
                    return self._cursor_proxy

                def __getattr__(self, item):
                    return getattr(self._inner, item)

            cursor_proxy = _CursorProxy(cursor)
            conn_proxy = _ConnectionProxy(conn, cursor_proxy)
            yield conn_proxy, cursor_proxy

    monkeypatch.setattr(app_module, "get_db_connection", capture_get_db_connection)

    domain_input = "My-Lab.LAN"
    expected_domain = app_module._validate_local_domain(domain_input)

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
            "local_domain": domain_input,
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"DHCP-Konfiguration aktiviert." in response.data
    assert payload_capture["normalized_payload"]["local_domain"] == ""
    assert payload_capture["write_payload"]["local_domain"] == ""
    assert host_updates == [("audio-pi", expected_domain)]
    assert stored_settings.get("network_local_domain") == expected_domain


def test_network_settings_post_static_triggers_hostnamectl(monkeypatch, client):
    test_client, app_module = client
    _login(test_client)

    payloads: List[Mapping[str, str]] = []

    normalized_result = app_module.NormalizedNetworkSettings(
        interface="wlan0",
        normalized={
            "mode": "manual",
            "ipv4_address": "192.168.20.5",
            "ipv4_prefix": "24",
            "ipv4_gateway": "192.168.20.1",
            "dns_servers": "1.1.1.1, 8.8.4.4",
            "local_domain": "studio.lan",
        },
        original_lines=["# alt"],
        new_lines=["# alt", "interface wlan0"],
        dhcpcd_path=Path("/tmp/dhcpcd.conf"),
        backup_path=Path("/tmp/dhcpcd.conf.bak"),
        original_exists=True,
    )

    def fake_normalize(interface: str, payload: Mapping[str, str]):
        payloads.append(dict(payload))
        return normalized_result

    def fake_write(
        interface: str,
        payload: Mapping[str, str],
        *,
        normalized_result: Any = None,
    ):
        assert interface == "wlan0"
        assert normalized_result is normalized_result_obj
        return dict(normalized_result_obj.normalized)

    normalized_result_obj = normalized_result
    monkeypatch.setattr(app_module, "_normalize_network_settings", fake_normalize)
    monkeypatch.setattr(app_module, "_write_network_settings", fake_write)
    monkeypatch.setattr(app_module, "_get_current_hostname", lambda: "old-host")
    monkeypatch.setattr(app_module, "is_sudo_disabled", lambda: False)

    host_updates: List[Tuple[str, str]] = []

    def fake_update_hosts(hostname: str, local_domain: str):
        host_updates.append((hostname, local_domain))
        return app_module.HostsUpdateResult(
            hosts_path=Path("/tmp/hosts"),
            changed=True,
            backup_path=Path("/tmp/hosts.bak"),
            original_exists=True,
            original_lines=["127.0.1.1\told-host"],
        )

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
    restart_calls: List[List[str]] = []

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:2] == ["hostnamectl", "set-hostname"]:
            run_calls.append(list(cmd))
            return DummyResult()
        if isinstance(cmd, list) and cmd[:3] == ["systemctl", "restart", "dhcpcd"]:
            restart_calls.append(list(cmd))
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
    assert command_calls == [
        ("hostnamectl", "set-hostname", "studio-pi"),
        ("systemctl", "restart", "dhcpcd"),
    ]
    assert run_calls == [["hostnamectl", "set-hostname", "studio-pi"]]
    assert restart_calls == [["systemctl", "restart", "dhcpcd"]]
    assert host_updates == [("studio-pi", "studio.lan")]


def test_network_settings_triggers_dhcpcd_restart_on_change(monkeypatch, client):
    test_client, app_module = client
    _login(test_client)

    normalized_result = app_module.NormalizedNetworkSettings(
        interface="wlan0",
        normalized={
            "mode": "manual",
            "ipv4_address": "192.168.50.10",
            "ipv4_prefix": "24",
            "ipv4_gateway": "192.168.50.1",
            "dns_servers": "1.1.1.1, 8.8.8.8",
            "local_domain": "",
        },
        original_lines=["# alt"],
        new_lines=["# alt", "interface wlan0"],
        dhcpcd_path=Path("/tmp/dhcpcd.conf"),
        backup_path=None,
        original_exists=True,
    )

    def fake_normalize(interface: str, payload: Mapping[str, str]):
        assert interface == "wlan0"
        return normalized_result

    def fake_write(
        interface: str,
        payload: Mapping[str, str],
        *,
        normalized_result: Any = None,
    ):
        assert normalized_result is normalized_result_obj
        return dict(normalized_result_obj.normalized)

    normalized_result_obj = normalized_result
    monkeypatch.setattr(app_module, "_normalize_network_settings", fake_normalize)
    monkeypatch.setattr(app_module, "_write_network_settings", fake_write)
    monkeypatch.setattr(app_module, "_get_current_hostname", lambda: "audio-pi")
    monkeypatch.setattr(app_module, "is_sudo_disabled", lambda: False)

    host_updates: List[Tuple[str, str]] = []

    def fake_update_hosts(hostname: str, local_domain: str):
        host_updates.append((hostname, local_domain))
        return app_module.HostsUpdateResult(
            hosts_path=Path("/tmp/hosts"),
            changed=True,
            backup_path=None,
            original_exists=True,
            original_lines=["127.0.1.1\taudio-pi"],
        )

    monkeypatch.setattr(app_module, "_update_hosts_file", fake_update_hosts)

    command_calls: List[Tuple[str, ...]] = []

    def fake_privileged_command(*args: str) -> List[str]:
        command_calls.append(tuple(args))
        return list(args)

    monkeypatch.setattr(app_module, "privileged_command", fake_privileged_command)

    class DummyResult:
        def __init__(self):
            self.returncode = 0
            self.stdout = ""
            self.stderr = ""

    restart_calls: List[List[str]] = []
    original_run = app_module.subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:3] == ["systemctl", "restart", "dhcpcd"]:
            restart_calls.append(list(cmd))
            return DummyResult()
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    response = csrf_post(
        test_client,
        "/network_settings",
        data={
            "mode": "manual",
            "ipv4_address": "192.168.50.10",
            "ipv4_prefix": "24",
            "ipv4_gateway": "192.168.50.1",
            "dns_servers": "1.1.1.1 8.8.8.8",
            "hostname": "audio-pi",
            "local_domain": "",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Statische IPv4-Konfiguration gespeichert." in response.data
    assert b"Der Netzwerkdienst (dhcpcd) wurde neu gestartet." in response.data
    assert command_calls == [("systemctl", "restart", "dhcpcd")]
    assert restart_calls == [["systemctl", "restart", "dhcpcd"]]
    assert host_updates == [("audio-pi", "")]


def test_network_settings_skips_restart_when_not_needed(monkeypatch, client):
    test_client, app_module = client
    _login(test_client)

    normalized_result = app_module.NormalizedNetworkSettings(
        interface="wlan0",
        normalized={
            "mode": "manual",
            "ipv4_address": "192.168.60.10",
            "ipv4_prefix": "24",
            "ipv4_gateway": "192.168.60.1",
            "dns_servers": "9.9.9.9, 1.1.1.1",
            "local_domain": "",
        },
        original_lines=["interface wlan0"],
        new_lines=["interface wlan0"],
        dhcpcd_path=Path("/tmp/dhcpcd.conf"),
        backup_path=None,
        original_exists=True,
    )

    def fake_normalize(interface: str, payload: Mapping[str, str]):
        assert interface == "wlan0"
        return normalized_result

    def fake_write(
        interface: str,
        payload: Mapping[str, str],
        *,
        normalized_result: Any = None,
    ):
        assert normalized_result is normalized_result_obj
        return dict(normalized_result_obj.normalized)

    normalized_result_obj = normalized_result
    monkeypatch.setattr(app_module, "_normalize_network_settings", fake_normalize)
    monkeypatch.setattr(app_module, "_write_network_settings", fake_write)
    monkeypatch.setattr(app_module, "_get_current_hostname", lambda: "audio-pi")
    monkeypatch.setattr(app_module, "is_sudo_disabled", lambda: False)

    host_updates: List[Tuple[str, str]] = []

    def fake_update_hosts(hostname: str, local_domain: str):
        host_updates.append((hostname, local_domain))
        return app_module.HostsUpdateResult(
            hosts_path=Path("/tmp/hosts"),
            changed=True,
            backup_path=None,
            original_exists=True,
            original_lines=["127.0.1.1\taudio-pi"],
        )

    monkeypatch.setattr(app_module, "_update_hosts_file", fake_update_hosts)

    command_calls: List[Tuple[str, ...]] = []

    def fake_privileged_command(*args: str) -> List[str]:
        command_calls.append(tuple(args))
        return list(args)

    monkeypatch.setattr(app_module, "privileged_command", fake_privileged_command)

    original_run = app_module.subprocess.run

    def guard_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:3] == ["systemctl", "restart", "dhcpcd"]:
            raise AssertionError("systemctl sollte nicht aufgerufen werden")
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(app_module.subprocess, "run", guard_run)

    response = csrf_post(
        test_client,
        "/network_settings",
        data={
            "mode": "manual",
            "ipv4_address": "192.168.60.10",
            "ipv4_prefix": "24",
            "ipv4_gateway": "192.168.60.1",
            "dns_servers": "9.9.9.9 1.1.1.1",
            "hostname": "audio-pi",
            "local_domain": "",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Statische IPv4-Konfiguration gespeichert." in response.data
    assert b"Der Netzwerkdienst (dhcpcd) wurde neu gestartet." not in response.data
    assert ("systemctl", "restart", "dhcpcd") not in command_calls
    assert host_updates == [("audio-pi", "")]

def test_network_settings_post_static_invalid_ip(monkeypatch, client):
    test_client, app_module = client
    _login(test_client)

    def fake_normalize(interface: str, payload: Mapping[str, str]):
        raise app_module.NetworkConfigError("Ungültige IPv4-Adresse oder Präfix.")

    monkeypatch.setattr(app_module, "_normalize_network_settings", fake_normalize)

    def fail_write(*args, **kwargs):
        raise AssertionError("write sollte nicht aufgerufen werden")

    monkeypatch.setattr(app_module, "_write_network_settings", fail_write)
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


def test_network_settings_write_failure_keeps_hostname_and_hosts(monkeypatch, client, tmp_path: Path):
    test_client, app_module = client
    _login(test_client)

    normalized_result = app_module.NormalizedNetworkSettings(
        interface="wlan0",
        normalized={
            "mode": "manual",
            "ipv4_address": "192.168.40.2",
            "ipv4_prefix": "24",
            "ipv4_gateway": "192.168.40.1",
            "dns_servers": "1.1.1.1, 8.8.8.8",
            "local_domain": "",
        },
        original_lines=[],
        new_lines=["interface wlan0"],
        dhcpcd_path=tmp_path / "dhcpcd.conf",
        backup_path=None,
        original_exists=False,
    )

    def fake_normalize(interface: str, payload: Mapping[str, str]):
        assert interface == "wlan0"
        return normalized_result

    def failing_write(interface: str, payload: Mapping[str, str], *, normalized_result=None):
        raise RuntimeError("write failed")

    monkeypatch.setattr(app_module, "_normalize_network_settings", fake_normalize)
    monkeypatch.setattr(app_module, "_write_network_settings", failing_write)
    monkeypatch.setattr(app_module, "_get_current_hostname", lambda: "current-host")

    hosts_path = tmp_path / "hosts"
    original_hosts_content = "127.0.1.1\tcurrent-host\n"
    hosts_path.write_text(original_hosts_content, encoding="utf-8")

    host_updates: List[Tuple[str, str]] = []

    def fake_update_hosts(hostname: str, local_domain: str):
        host_updates.append((hostname, local_domain))
        hosts_path.write_text("127.0.1.1\tmodified\n", encoding="utf-8")
        return app_module.HostsUpdateResult(
            hosts_path=hosts_path,
            changed=True,
            backup_path=None,
            original_exists=True,
            original_lines=["127.0.1.1\tcurrent-host"],
        )

    monkeypatch.setattr(app_module, "_update_hosts_file", fake_update_hosts)
    monkeypatch.setattr(app_module, "privileged_command", lambda *args: list(args))

    original_run = app_module.subprocess.run

    def guard_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:2] == ["hostnamectl", "set-hostname"]:
            raise AssertionError("hostnamectl darf bei Schreibfehlern nicht ausgeführt werden")
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(app_module.subprocess, "run", guard_run)

    response = csrf_post(
        test_client,
        "/network_settings",
        data={
            "mode": "manual",
            "ipv4_address": "192.168.40.2",
            "ipv4_prefix": "24",
            "ipv4_gateway": "192.168.40.1",
            "dns_servers": "1.1.1.1 8.8.8.8",
            "hostname": "new-host",
            "local_domain": "",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert (
        b"Beim Speichern der Netzwerkeinstellungen ist ein Fehler aufgetreten." in response.data
    )
    assert hosts_path.read_text(encoding="utf-8") == original_hosts_content
    assert host_updates == []


def test_network_settings_rollback_on_setting_failure(monkeypatch, client, tmp_path: Path):
    test_client, app_module = client
    _login(test_client)

    conf = tmp_path / "dhcpcd.conf"
    _write_conf(
        conf,
        [
            "# Kommentar",
            "interface wlan0",
            "static ip_address=10.0.0.5/24",
            "static routers=10.0.0.1",
            "static domain_name_servers=1.1.1.1",
        ],
    )
    original_content = conf.read_text(encoding="utf-8")

    import importlib

    netmod = importlib.import_module("network_config")

    def real_normalize(interface: str, payload: Mapping[str, str]):
        return netmod.normalize_network_settings(interface, dict(payload), conf)

    def real_write(
        interface: str,
        payload: Mapping[str, str],
        *,
        normalized_result=None,
    ):
        return netmod.write_network_settings(
            interface,
            dict(payload),
            conf,
            normalized_result=normalized_result,
        )

    def real_restore(normalized_result):
        return netmod.restore_network_backup(normalized_result)

    monkeypatch.setattr(app_module, "_normalize_network_settings", real_normalize)
    monkeypatch.setattr(app_module, "_write_network_settings", real_write)
    monkeypatch.setattr(app_module, "_restore_network_backup", real_restore)
    monkeypatch.setattr(app_module, "_get_current_hostname", lambda: "audio-pi")

    host_updates: List[Tuple[str, str]] = []

    def fake_update_hosts(hostname: str, local_domain: str):
        host_updates.append((hostname, local_domain))
        return app_module.HostsUpdateResult(
            hosts_path=Path("/tmp/hosts"),
            changed=True,
            backup_path=None,
            original_exists=True,
            original_lines=["127.0.1.1\taudio-pi"],
        )

    monkeypatch.setattr(app_module, "_update_hosts_file", fake_update_hosts)

    class DummyResult:
        def __init__(self):
            self.returncode = 0
            self.stdout = ""
            self.stderr = ""

    monkeypatch.setattr(app_module, "privileged_command", lambda *args: list(args))
    monkeypatch.setattr(
        app_module.subprocess,
        "run",
        lambda *args, **kwargs: DummyResult(),
    )

    rollback_calls: List[bool] = []
    original_get_db_connection = app_module.get_db_connection

    @contextmanager
    def failing_get_db_connection():
        with original_get_db_connection() as (conn, cursor):
            original_execute = cursor.execute
            original_rollback = conn.rollback

            def failing_execute(sql: str, params=None):
                if sql.strip().lower().startswith("insert or replace into settings"):
                    raise RuntimeError("db failure")
                if params is None:
                    return original_execute(sql)
                return original_execute(sql, params)

            class _CursorProxy:
                def __init__(self, inner):
                    self._inner = inner

                def execute(self, sql: str, params=None):
                    return failing_execute(sql, params)

                def __getattr__(self, item):
                    return getattr(self._inner, item)

            class _ConnectionProxy:
                def __init__(self, inner, cursor_proxy):
                    self._inner = inner
                    self._cursor_proxy = cursor_proxy

                def cursor(self):
                    return self._cursor_proxy

                def rollback(self):
                    rollback_calls.append(True)
                    return original_rollback()

                def __getattr__(self, item):
                    return getattr(self._inner, item)

            cursor_proxy = _CursorProxy(cursor)
            conn_proxy = _ConnectionProxy(conn, cursor_proxy)
            yield conn_proxy, cursor_proxy

    monkeypatch.setattr(app_module, "get_db_connection", failing_get_db_connection)

    response = csrf_post(
        test_client,
        "/network_settings",
        data={
            "mode": "manual",
            "ipv4_address": "192.168.80.5",
            "ipv4_prefix": "24",
            "ipv4_gateway": "192.168.80.1",
            "dns_servers": "1.1.1.1 8.8.8.8",
            "hostname": "studio-pi",
            "local_domain": "studio.lan",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert (
        b"Beim Aktualisieren der Einstellungen ist ein Fehler aufgetreten. \xc3\x84nderungen wurden zur\xc3\xbcckgesetzt."
        in response.data
    )
    assert conf.read_text(encoding="utf-8") == original_content
    assert host_updates == [("studio-pi", "studio.lan")]
    assert rollback_calls == [True]


def test_network_settings_commit_failure_rolls_back(monkeypatch, client, tmp_path: Path):
    test_client, app_module = client
    _login(test_client)

    initial_values = {
        "network_mode": "dhcp",
        "network_ipv4_address": "10.0.0.5",
        "network_ipv4_prefix": "24",
        "network_ipv4_gateway": "10.0.0.1",
        "network_dns_servers": "8.8.8.8",
        "network_hostname": "audio-pi",
        "network_local_domain": "initial.lan",
    }
    for key, value in initial_values.items():
        app_module.set_setting(key, value)

    normalized_payload = {
        "mode": "manual",
        "ipv4_address": "192.168.40.5",
        "ipv4_prefix": "24",
        "ipv4_gateway": "192.168.40.1",
        "dns_servers": "9.9.9.9, 1.1.1.1",
        "local_domain": "studio.lan",
    }
    normalized_result = app_module.NormalizedNetworkSettings(
        interface="wlan0",
        normalized=dict(normalized_payload),
        original_lines=[],
        new_lines=[],
        dhcpcd_path=tmp_path / "dhcpcd.conf",
        backup_path=None,
        original_exists=False,
    )

    def fake_normalize(interface: str, payload: Mapping[str, str]):
        assert interface == "wlan0"
        return normalized_result

    def fake_write(
        interface: str,
        payload: Mapping[str, str],
        *,
        normalized_result: Any = None,
    ):
        assert interface == "wlan0"
        assert normalized_result is normalized_result_obj
        return dict(normalized_payload)

    normalized_result_obj = normalized_result
    monkeypatch.setattr(app_module, "_normalize_network_settings", fake_normalize)
    monkeypatch.setattr(app_module, "_write_network_settings", fake_write)
    monkeypatch.setattr(app_module, "_get_current_hostname", lambda: "audio-pi")

    hosts_result = app_module.HostsUpdateResult(
        hosts_path=tmp_path / "hosts",
        changed=True,
        backup_path=None,
        original_exists=True,
        original_lines=["127.0.1.1\taudio-pi"],
    )

    host_updates: List[Tuple[str, str]] = []

    def fake_update_hosts(hostname: str, local_domain: str):
        host_updates.append((hostname, local_domain))
        return hosts_result

    monkeypatch.setattr(app_module, "_update_hosts_file", fake_update_hosts)

    restored_hosts: List[app_module.HostsUpdateResult] = []

    def fake_restore_hosts(result: app_module.HostsUpdateResult) -> None:
        restored_hosts.append(result)

    monkeypatch.setattr(app_module, "_restore_hosts_state", fake_restore_hosts)

    backup_restores: List[app_module.NormalizedNetworkSettings] = []

    def fake_restore_backup(result: app_module.NormalizedNetworkSettings) -> None:
        backup_restores.append(result)

    monkeypatch.setattr(app_module, "_restore_network_backup", fake_restore_backup)

    rollback_calls: List[bool] = []
    original_get_db_connection = app_module.get_db_connection

    @contextmanager
    def patched_get_db_connection():
        with original_get_db_connection() as (conn, cursor):
            original_rollback = conn.rollback

            class _ConnectionProxy:
                def __init__(self, inner_conn):
                    self._inner = inner_conn

                def commit(self):
                    raise sqlite3.OperationalError("commit failed")

                def rollback(self):
                    rollback_calls.append(True)
                    return original_rollback()

                def __getattr__(self, item):
                    return getattr(self._inner, item)

            yield _ConnectionProxy(conn), cursor

    monkeypatch.setattr(app_module, "get_db_connection", patched_get_db_connection)

    response = csrf_post(
        test_client,
        "/network_settings",
        data={
            "mode": "manual",
            "ipv4_address": "192.168.40.5",
            "ipv4_prefix": "24",
            "ipv4_gateway": "192.168.40.1",
            "dns_servers": "9.9.9.9 1.1.1.1",
            "hostname": "audio-pi",
            "local_domain": "studio.lan",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert (
        b"Beim Aktualisieren der Einstellungen ist ein Fehler aufgetreten. \xc3\x84nderungen wurden zur\xc3\xbcckgesetzt."
        in response.data
    )
    assert host_updates == [("audio-pi", "studio.lan")]
    assert restored_hosts == [hosts_result]
    assert backup_restores == []
    assert rollback_calls, "rollback sollte bei Commit-Fehlern ausgelöst werden"

    with original_get_db_connection() as (conn, cursor):
        rows = cursor.execute(
            "SELECT key, value FROM settings WHERE key IN ({})".format(
                ",".join("?" for _ in initial_values)
            ),
            tuple(initial_values.keys()),
        ).fetchall()

    stored_values = {row["key"]: row["value"] for row in rows}
    assert stored_values == initial_values
