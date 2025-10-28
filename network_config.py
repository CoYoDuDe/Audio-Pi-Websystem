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
from dataclasses import dataclass
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


@dataclass
class HostsUpdateResult:
    """Repräsentiert das Ergebnis einer ``/etc/hosts``-Aktualisierung."""

    hosts_path: Path
    changed: bool
    backup_path: Optional[Path]
    original_exists: bool
    original_lines: List[str]


def _read_lines(path: Path) -> List[str]:
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return content.splitlines()


def _candidate_backup_bases() -> List[Path]:
    """Liefert mögliche Basisverzeichnisse für Backups."""

    local_dir = Path.cwd() / ".audio-pi" / "network-backups"
    system_dir = Path("/var/lib/dhcpcd/audio-pi")
    candidates: List[Path] = []
    for candidate in (local_dir, system_dir):
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _sanitize_parent_for_backup(path: Path) -> str:
    """Erstellt einen eindeutigen Namen für das Elternverzeichnis."""

    parent = path.parent.as_posix().lstrip("/")
    if not parent:
        return ""
    return parent.replace("/", "_")


def _cleanup_backup_artifact(backup_path: Path) -> None:
    """Entfernt ein temporär benötigtes Backup."""

    try:
        backup_path.unlink()
    except (FileNotFoundError, PermissionError, OSError):
        return


def _backup_file(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_name = f"{path.name}.bak.{timestamp}"
    backup_path = path.with_name(backup_name)
    try:
        shutil.copy2(path, backup_path)
    except Exception as exc:
        if not isinstance(exc, PermissionError):
            raise NetworkConfigError(
                "Fehler beim Schreiben der Netzwerkkonfiguration: Backup konnte nicht erstellt werden."
            ) from exc

        fallback_error: Optional[BaseException] = exc
        sanitized_parent = _sanitize_parent_for_backup(path)
        for base in _candidate_backup_bases():
            try:
                base.mkdir(parents=True, exist_ok=True)
            except (PermissionError, OSError) as mkdir_exc:
                fallback_error = mkdir_exc
                continue
            target_dir = base
            if sanitized_parent:
                target_dir = base / sanitized_parent
                try:
                    target_dir.mkdir(parents=True, exist_ok=True)
                except (PermissionError, OSError) as mkdir_exc:
                    fallback_error = mkdir_exc
                    continue
            fallback_path = target_dir / backup_name
            try:
                shutil.copy2(path, fallback_path)
            except PermissionError as copy_exc:
                fallback_error = copy_exc
                continue
            except Exception as copy_exc:
                raise NetworkConfigError(
                    "Fehler beim Schreiben der Netzwerkkonfiguration: Backup konnte nicht erstellt werden."
                ) from copy_exc
            else:
                return fallback_path

        raise NetworkConfigError(
            "Fehler beim Schreiben der Netzwerkkonfiguration: Backup konnte nicht erstellt werden."
        ) from fallback_error
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
        except NetworkConfigError:
            raise
        except Exception as exc:
            restore_error: Optional[BaseException] = None
            if create_backup and backup_path and backup_path.exists():
                try:
                    shutil.copy2(backup_path, path)
                except Exception as restore_exc:  # pragma: no cover - defensive fallback
                    restore_error = restore_exc
                finally:
                    if backup_path.parent != path.parent:
                        _cleanup_backup_artifact(backup_path)
            if restore_error is not None:
                raise NetworkConfigError(
                    "Fehler beim Wiederherstellen der ursprünglichen Netzwerkkonfiguration."
                ) from restore_error
            raise NetworkConfigError(
                "Fehler beim Schreiben der Netzwerkkonfiguration."
            ) from exc
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


def _strip_inline_comment(value: str) -> str:
    """Entfernt optionale Inline-Kommentare ("# …") aus einem Wert."""

    comment_pos = value.find("#")
    if comment_pos == -1:
        return value.strip()
    return value[:comment_pos].strip()


def _split_dns_values(raw: str) -> List[str]:
    values = [part for part in DNS_VALUE_SPLIT_RE.split(raw) if part]
    return values


@dataclass
class NormalizedNetworkSettings:
    """Enthält die Ergebnisse der Normalisierung einer Netzwerkkonfiguration."""

    interface: str
    normalized: Dict[str, str]
    original_lines: List[str]
    new_lines: List[str]
    dhcpcd_path: Path
    backup_path: Optional[Path]
    original_exists: bool

    @property
    def requires_update(self) -> bool:
        return self.new_lines != self.original_lines


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


def validate_local_domain(value: str) -> str:
    """Validiert und normalisiert einen lokalen Domain-Namen."""

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
            raise NetworkConfigError(
                "Domain-Label darf nicht mit Bindestrich beginnen oder enden."
            )
        if not HOST_LABEL_RE.fullmatch(label):
            raise NetworkConfigError(
                "Domain-Label enthält unzulässige Zeichen (nur a-z, 0-9 und Bindestriche erlaubt)."
            )
    return value


def _validate_domain(value: str) -> str:
    return validate_local_domain(value)


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
            clean_value = _strip_inline_comment(value)
            if not clean_value:
                continue
            result["mode"] = "manual"
            if "/" in clean_value:
                address, prefix = clean_value.split("/", 1)
                result["ipv4_address"] = address.strip()
                result["ipv4_prefix"] = prefix.strip()
            else:
                result["ipv4_address"] = clean_value
        elif key == "static routers":
            clean_value = _strip_inline_comment(value)
            if not clean_value:
                continue
            result["ipv4_gateway"] = clean_value.split()[0]
        elif key == "static domain_name_servers":
            clean_value = _strip_inline_comment(value)
            dns_values = _split_dns_values(clean_value)
            result["dns_servers"] = ", ".join(dns_values)
        elif key == "static domain_name":
            result["local_domain"] = _strip_inline_comment(value)
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


def normalize_network_settings(
    interface: str,
    settings: Dict[str, str],
    dhcpcd_path: Path = Path("/etc/dhcpcd.conf"),
) -> NormalizedNetworkSettings:
    interface = interface.strip()
    if not interface:
        raise NetworkConfigError("Netzwerkschnittstelle darf nicht leer sein.")

    mode_raw = str(settings.get("mode", "dhcp")).strip().lower()
    manual = mode_raw in {"manual", "static", "static_ipv4"}

    original_lines = _read_lines(dhcpcd_path)
    lines = _remove_client_block(original_lines)
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
        local_domain = validate_local_domain(local_domain_raw)

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

    new_lines = list(lines)
    original_lines_copy = list(original_lines)
    backup_path: Optional[Path] = None
    if new_lines != original_lines_copy:
        backup_path = _backup_file(dhcpcd_path)

    return NormalizedNetworkSettings(
        interface=interface,
        normalized=dict(normalized),
        original_lines=original_lines_copy,
        new_lines=new_lines,
        dhcpcd_path=dhcpcd_path,
        backup_path=backup_path,
        original_exists=dhcpcd_path.exists(),
    )


def write_network_settings(
    interface: str,
    settings: Dict[str, str],
    dhcpcd_path: Path = Path("/etc/dhcpcd.conf"),
    *,
    normalized_result: Optional[NormalizedNetworkSettings] = None,
) -> Dict[str, str]:
    result = normalized_result
    if result is None:
        result = normalize_network_settings(interface, settings, dhcpcd_path)
    else:
        if result.interface != interface:
            raise NetworkConfigError(
                "Normalisierte Einstellungen gehören zu einer anderen Schnittstelle."
            )
        if result.dhcpcd_path != dhcpcd_path:
            raise NetworkConfigError(
                "Normalisierte Einstellungen wurden für eine andere Konfigurationsdatei erstellt."
            )

    if not result.requires_update:
        return dict(result.normalized)

    backup_path = result.backup_path
    if backup_path is None and result.original_exists:
        backup_path = _backup_file(dhcpcd_path)
        result.backup_path = backup_path

    try:
        _write_lines(dhcpcd_path, result.new_lines, create_backup=False)
    except Exception:
        if backup_path and backup_path.exists():
            shutil.copy2(backup_path, dhcpcd_path)
        elif not result.original_exists:
            try:
                dhcpcd_path.unlink()
            except FileNotFoundError:
                pass
        else:
            _write_lines(dhcpcd_path, result.original_lines, create_backup=False)
        raise

    return dict(result.normalized)


def restore_network_backup(result: NormalizedNetworkSettings) -> None:
    """Stellt die ursprüngliche ``dhcpcd.conf`` auf Basis eines Backups wieder her."""

    if not result.requires_update:
        return

    path = result.dhcpcd_path
    if result.backup_path and result.backup_path.exists():
        shutil.copy2(result.backup_path, path)
        if result.backup_path.parent != path.parent:
            _cleanup_backup_artifact(result.backup_path)
        result.backup_path = None
        return

    if result.original_exists:
        _write_lines(path, result.original_lines, create_backup=False)
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            return


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
) -> HostsUpdateResult:
    hostname = validate_hostname(hostname)
    local_domain = validate_local_domain(local_domain)

    lines = _read_lines(hosts_path)
    if not lines:
        lines = []
    original_lines = list(lines)
    original_exists = hosts_path.exists()

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

    if list(lines) == original_lines:
        return HostsUpdateResult(
            hosts_path=hosts_path,
            changed=False,
            backup_path=None,
            original_exists=original_exists,
            original_lines=original_lines,
        )

    backup_path: Optional[Path] = None
    if original_exists:
        backup_path = _backup_file(hosts_path)

    _write_lines(hosts_path, lines, create_backup=False)

    return HostsUpdateResult(
        hosts_path=hosts_path,
        changed=True,
        backup_path=backup_path,
        original_exists=original_exists,
        original_lines=original_lines,
    )


def restore_hosts_state(result: HostsUpdateResult) -> None:
    """Setzt ``/etc/hosts`` auf den ursprünglichen Zustand zurück."""

    if not result.changed:
        return

    hosts_path = result.hosts_path

    if result.backup_path and result.backup_path.exists():
        shutil.copy2(result.backup_path, hosts_path)
        return

    if result.original_exists:
        _write_lines(hosts_path, result.original_lines, create_backup=False)
        return

    try:
        hosts_path.unlink()
    except FileNotFoundError:
        return
