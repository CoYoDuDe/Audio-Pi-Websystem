"""Hilfsfunktionen für das Verwalten der Netzwerkkonfiguration.

Dieses Modul liest und schreibt dhcpcd-Konfigurationen, ohne dabei die
Access-Point-Markierungen aus ``install.sh`` zu verändern. Zusätzlich stehen
Validierungs- und Host-Datei-Helfer bereit, damit die Weboberfläche die
Einstellungen konsistent pflegen kann.
"""
from __future__ import annotations

import ipaddress
import os
import re
import shutil
import socket
import stat
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ACCESS_POINT_START_MARKER = "# Audio-Pi Access Point configuration"
ACCESS_POINT_END_MARKER = "# Audio-Pi Access Point configuration end"
CLIENT_START_MARKER = "# Audio-Pi Client configuration"
CLIENT_END_MARKER = "# Audio-Pi Client configuration end"
STATIC_DIRECTIVES = {
    "static ip_address",
    "static routers",
    "static domain_name_servers",
    "static domain_name",
}
DNS_VALUE_SPLIT_RE = re.compile(r"[\s,]+")
HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class NetworkConfigError(Exception):
    """Allgemeiner Fehler für Netzwerkoperationen."""

    def __init__(self, message: str):
        super().__init__(message)
        self.user_message = message


def _read_lines(path: Path) -> List[str]:
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return content.splitlines()


def _backup_file(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_name = f"{path.name}.bak.{timestamp}"
    backup_path = path.with_name(backup_name)
    shutil.copy2(path, backup_path)
    return backup_path


def _write_lines(path: Path, lines: Sequence[str], *, create_backup: bool = True) -> bool:
    original_lines = _read_lines(path)
    if list(original_lines) == list(lines):
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    mode: Optional[int] = None
    if path.exists():
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            mode = None

    backup_path: Optional[Path] = None
    if create_backup:
        backup_path = _backup_file(path)

    text = "\n".join(lines)
    if lines:
        text += "\n"

    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", delete=False, encoding="utf-8", dir=str(path.parent)
        ) as tmp:
            tmp.write(text)
            tmp_path = Path(tmp.name)

        try:
            os.replace(tmp_path, path)
            tmp_path = None
            if mode is not None:
                os.chmod(path, mode)
            else:
                os.chmod(path, 0o644)
        except Exception:
            if create_backup and backup_path and backup_path.exists():
                shutil.copy2(backup_path, path)
            raise
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
    return True


def _remove_client_block(lines: Iterable[str]) -> List[str]:
    cleaned: List[str] = []
    skip = False
    for line in lines:
        stripped = line.strip()
        if stripped == CLIENT_START_MARKER:
            skip = True
            continue
        if stripped == CLIENT_END_MARKER:
            skip = False
            continue
        if skip:
            continue
        cleaned.append(line)
    return cleaned


def _strip_static_directives(lines: Iterable[str], interface: str) -> List[str]:
    result: List[str] = []
    inside_interface = False
    inside_ap_block = False
    for line in lines:
        stripped = line.strip()
        if stripped == ACCESS_POINT_START_MARKER:
            inside_ap_block = True
            result.append(line)
            continue
        if stripped == ACCESS_POINT_END_MARKER:
            inside_ap_block = False
            result.append(line)
            continue
        if stripped == CLIENT_START_MARKER:
            # Dieser Block wurde bereits entfernt.
            result.append(line)
            inside_interface = False
            continue
        if stripped.startswith("interface "):
            current_interface = stripped.split(None, 1)[1].strip()
            inside_interface = current_interface == interface and not inside_ap_block
            result.append(line)
            continue
        if inside_interface and not inside_ap_block:
            if not stripped or stripped.startswith("#"):
                result.append(line)
                continue
            key = stripped.split("=", 1)[0].strip()
            if key in STATIC_DIRECTIVES:
                continue
        result.append(line)
    return result


def _split_dns_values(raw: str) -> List[str]:
    values = [part for part in DNS_VALUE_SPLIT_RE.split(raw) if part]
    return values


def _validate_ipv4_interface(address: str, prefix: str) -> ipaddress.IPv4Interface:
    if not address:
        raise NetworkConfigError("IPv4-Adresse darf nicht leer sein.")
    if not prefix:
        raise NetworkConfigError("IPv4-Präfix darf nicht leer sein.")
    try:
        prefix_int = int(prefix, 10)
    except ValueError as exc:  # pragma: no cover - defensive fallback
        raise NetworkConfigError("IPv4-Präfix muss eine Ganzzahl sein.") from exc
    if prefix_int < 0 or prefix_int > 32:
        raise NetworkConfigError("IPv4-Präfix muss zwischen 0 und 32 liegen.")
    try:
        return ipaddress.IPv4Interface(f"{address}/{prefix_int}")
    except (ipaddress.AddressValueError, ValueError) as exc:
        raise NetworkConfigError("Ungültige IPv4-Adresse oder Präfix.") from exc


def _validate_gateway(
    gateway: str, iface: ipaddress.IPv4Interface
) -> ipaddress.IPv4Address:
    if not gateway:
        raise NetworkConfigError("Gateway darf nicht leer sein.")
    try:
        candidate = ipaddress.IPv4Address(gateway)
    except ipaddress.AddressValueError as exc:
        raise NetworkConfigError("Ungültige IPv4-Gateway-Adresse.") from exc
    if candidate not in iface.network:
        raise NetworkConfigError(
            "Gateway liegt nicht im gleichen Netzwerk wie die IPv4-Adresse."
        )
    return candidate


def _validate_dns_servers(raw: str) -> Tuple[List[ipaddress.IPv4Address], str]:
    values = _split_dns_values(raw)
    if not values:
        raise NetworkConfigError("Mindestens ein DNS-Server ist erforderlich.")
    servers: List[ipaddress.IPv4Address] = []
    for value in values:
        try:
            servers.append(ipaddress.IPv4Address(value))
        except ipaddress.AddressValueError as exc:
            raise NetworkConfigError("Ungültiger DNS-Server: %s" % value) from exc
    normalized = ", ".join(str(item) for item in servers)
    return servers, normalized


def _validate_domain(value: str) -> str:
    value = value.strip().lower()
    if not value:
        return ""
    if len(value) > 253:
        raise NetworkConfigError("Lokale Domain ist zu lang (maximal 253 Zeichen).")
    labels = value.split(".")
    for label in labels:
        if not label:
            raise NetworkConfigError("Lokale Domain enthält leere Labels.")
        if len(label) > 63:
            raise NetworkConfigError("Domain-Label überschreitet 63 Zeichen.")
        if label.startswith("-") or label.endswith("-"):
            raise NetworkConfigError("Domain-Label darf nicht mit Bindestrich beginnen oder enden.")
        if not HOST_LABEL_RE.fullmatch(label):
            raise NetworkConfigError(
                "Domain-Label enthält unzulässige Zeichen (nur a-z, 0-9 und Bindestriche erlaubt)."
            )
    return value


def validate_hostname(hostname: str) -> str:
    hostname = hostname.strip().lower()
    if not hostname:
        raise NetworkConfigError("Hostname darf nicht leer sein.")
    if len(hostname) > 253:
        raise NetworkConfigError("Hostname ist zu lang (maximal 253 Zeichen).")
    labels = hostname.split(".")
    for label in labels:
        if not label:
            raise NetworkConfigError("Hostname enthält leere Labels.")
        if len(label) > 63:
            raise NetworkConfigError("Hostname-Label überschreitet 63 Zeichen.")
        if label.startswith("-") or label.endswith("-"):
            raise NetworkConfigError(
                "Hostname-Label darf nicht mit Bindestrich beginnen oder enden."
            )
        if not HOST_LABEL_RE.fullmatch(label):
            raise NetworkConfigError(
                "Hostname enthält unzulässige Zeichen (nur a-z, 0-9 und Bindestriche erlaubt)."
            )
    return hostname


def _build_client_block(
    interface: str,
    iface: ipaddress.IPv4Interface,
    gateway: ipaddress.IPv4Address,
    dns_servers: Sequence[ipaddress.IPv4Address],
    domain: str,
) -> List[str]:
    block = [
        CLIENT_START_MARKER,
        f"interface {interface}",
        f"static ip_address={iface.ip.exploded}/{iface.network.prefixlen}",
        f"static routers={gateway.exploded}",
        "static domain_name_servers="
        + " ".join(server.exploded for server in dns_servers),
    ]
    if domain:
        block.append(f"static domain_name={domain}")
    block.append(CLIENT_END_MARKER)
    return block


def _iter_interface_blocks(
    lines: Sequence[str],
) -> Iterable[Tuple[str, List[str], bool]]:
    inside_ap = False
    inside_client = False
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == ACCESS_POINT_START_MARKER:
            inside_ap = True
            i += 1
            continue
        if stripped == ACCESS_POINT_END_MARKER:
            inside_ap = False
            i += 1
            continue
        if stripped == CLIENT_START_MARKER:
            inside_client = True
            i += 1
            continue
        if stripped == CLIENT_END_MARKER:
            inside_client = False
            i += 1
            continue
        if inside_ap:
            i += 1
            continue
        if stripped.startswith("interface "):
            current_interface = stripped.split(None, 1)[1].strip()
            block: List[str] = [lines[i]]
            i += 1
            while i < len(lines):
                candidate = lines[i]
                candidate_stripped = candidate.strip()
                if candidate_stripped in {
                    ACCESS_POINT_START_MARKER,
                    CLIENT_START_MARKER,
                    CLIENT_END_MARKER,
                }:
                    break
                if candidate_stripped.startswith("interface "):
                    break
                block.append(candidate)
                i += 1
            yield current_interface, block, inside_client
            continue
        i += 1


def _parse_interface_block(block: Sequence[str]) -> Dict[str, str]:
    result = {
        "mode": "dhcp",
        "ipv4_address": "",
        "ipv4_prefix": "",
        "ipv4_gateway": "",
        "dns_servers": "",
        "local_domain": "",
    }
    for line in block:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("interface "):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key == "static ip_address":
            result["mode"] = "manual"
            if "/" in value:
                address, prefix = value.split("/", 1)
                result["ipv4_address"] = address.strip()
                result["ipv4_prefix"] = prefix.strip()
            else:
                result["ipv4_address"] = value
        elif key == "static routers":
            result["ipv4_gateway"] = value.split()[0]
        elif key == "static domain_name_servers":
            dns_values = _split_dns_values(value)
            result["dns_servers"] = ", ".join(dns_values)
        elif key == "static domain_name":
            result["local_domain"] = value
    return result


def load_network_settings(
    interface: str, dhcpcd_path: Path = Path("/etc/dhcpcd.conf")
) -> Dict[str, str]:
    interface = interface.strip()
    if not interface:
        raise NetworkConfigError("Netzwerkschnittstelle darf nicht leer sein.")

    lines = _read_lines(dhcpcd_path)
    selected_block: Optional[List[str]] = None
    for name, block, inside_client in _iter_interface_blocks(lines):
        if name != interface:
            continue
        if inside_client:
            selected_block = block
            break
        if selected_block is None:
            selected_block = block
    result = _parse_interface_block(selected_block or [])
    result["hostname"] = get_current_hostname()
    return result


def write_network_settings(
    interface: str,
    settings: Dict[str, str],
    dhcpcd_path: Path = Path("/etc/dhcpcd.conf"),
) -> Dict[str, str]:
    interface = interface.strip()
    if not interface:
        raise NetworkConfigError("Netzwerkschnittstelle darf nicht leer sein.")

    mode_raw = str(settings.get("mode", "dhcp")).strip().lower()
    manual = mode_raw in {"manual", "static", "static_ipv4"}

    lines = _read_lines(dhcpcd_path)
    original_lines = list(lines)
    lines = _remove_client_block(lines)
    lines = _strip_static_directives(lines, interface)

    normalized: Dict[str, str]
    if manual:
        ipv4_address = str(settings.get("ipv4_address", "")).strip()
        ipv4_prefix = str(settings.get("ipv4_prefix", "")).strip()
        ipv4_gateway = str(settings.get("ipv4_gateway", "")).strip()
        dns_servers_raw = str(settings.get("dns_servers", "")).strip()
        local_domain_raw = str(settings.get("local_domain", "")).strip()

        iface = _validate_ipv4_interface(ipv4_address, ipv4_prefix)
        gateway = _validate_gateway(ipv4_gateway, iface)
        dns_servers, dns_normalized = _validate_dns_servers(dns_servers_raw)
        local_domain = _validate_domain(local_domain_raw)

        block = _build_client_block(interface, iface, gateway, dns_servers, local_domain)
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(block)
        normalized = {
            "mode": "manual",
            "ipv4_address": iface.ip.exploded,
            "ipv4_prefix": str(iface.network.prefixlen),
            "ipv4_gateway": gateway.exploded,
            "dns_servers": dns_normalized,
            "local_domain": local_domain,
        }
    else:
        normalized = {
            "mode": "dhcp",
            "ipv4_address": "",
            "ipv4_prefix": "",
            "ipv4_gateway": "",
            "dns_servers": "",
            "local_domain": "",
        }

    if lines != original_lines:
        try:
            _write_lines(dhcpcd_path, lines, create_backup=True)
        except NetworkConfigError:
            raise
        except Exception as exc:
            raise NetworkConfigError(
                "Fehler beim Schreiben der Netzwerkkonfiguration: %s" % exc
            ) from exc

    return normalized


def get_current_hostname(hostname_path: Path = Path("/etc/hostname")) -> str:
    try:
        content = hostname_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return socket.gethostname()
    value = content.strip()
    return value or socket.gethostname()


def update_hosts_file(
    hostname: str,
    local_domain: str = "",
    hosts_path: Path = Path("/etc/hosts"),
) -> bool:
    hostname = validate_hostname(hostname)
    local_domain = _validate_domain(local_domain)

    lines = _read_lines(hosts_path)
    if not lines:
        lines = []
    new_entry_parts = ["127.0.1.1", hostname]
    if local_domain:
        new_entry_parts.append(f"{hostname}.{local_domain}")
    new_entry = "\t".join(new_entry_parts)

    replaced = False
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        comment_split = line.split("#", 1)
        head = comment_split[0].strip()
        if not head:
            continue
        parts = head.split()
        if parts and parts[0] == "127.0.1.1":
            suffix = ""
            if len(comment_split) == 2:
                suffix = " #" + comment_split[1].rstrip()
            lines[idx] = new_entry + suffix
            replaced = True
            break
    if not replaced:
        lines.append(new_entry)
    return _write_lines(hosts_path, lines, create_backup=True)
