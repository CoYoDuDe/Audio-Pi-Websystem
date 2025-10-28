"""Sicherheitsregressionen rund um audio-pi.service verhindern."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "audio-pi.service"
INSTALLER = ROOT / "install.sh"
POLKIT_RULE = ROOT / "scripts" / "polkit" / "49-audio-pi.rules"

ONLY_REQUIRED_CAPABILITY = "CAP_NET_BIND_SERVICE"
REMOVED_CAPABILITIES = (
    "CAP_SYS_ADMIN",
    "CAP_SYS_BOOT",
    "CAP_SYS_TIME",
    "CAP_NET_ADMIN",
    "CAP_NET_RAW",
)
REQUIRED_READWRITE_PATHS = (
    "/opt/Audio-Pi-Websystem",
    "/etc/dhcpcd.conf",
    "/etc/hosts",
    "/etc/hostname",
    "/etc/wpa_supplicant",
    "/var/lib/dhcpcd",
)


def _collect_readwrite_paths(unit_content: str) -> set[str]:
    paths: list[str] = []
    for line in unit_content.splitlines():
        if line.startswith("ReadWritePaths="):
            _, value = line.split("=", 1)
            paths.extend(value.split())
    return set(paths)


def test_service_limits_capabilities_to_bind_service_only() -> None:
    """Die Unit darf ausschließlich CAP_NET_BIND_SERVICE behalten."""

    content = SERVICE.read_text(encoding="utf-8")
    assert f"CapabilityBoundingSet={ONLY_REQUIRED_CAPABILITY}" in content
    assert f"AmbientCapabilities={ONLY_REQUIRED_CAPABILITY}" in content
    for capability in REMOVED_CAPABILITIES:
        assert capability not in content


def test_installer_does_not_reintroduce_removed_capabilities() -> None:
    """Das Installationsskript darf die entfernten Capabilities nicht enthalten."""

    script = INSTALLER.read_text(encoding="utf-8")
    for capability in REMOVED_CAPABILITIES:
        assert capability not in script


def test_service_allows_expected_writable_paths_only() -> None:
    """Die Unit muss exakt die freigegebenen Schreibpfade enthalten."""

    content = SERVICE.read_text(encoding="utf-8")
    readwrite_paths = _collect_readwrite_paths(content)
    assert readwrite_paths == set(REQUIRED_READWRITE_PATHS)
    assert "ProtectSystem=strict" in content


def test_installer_advises_daemon_reload_after_unit_updates() -> None:
    """Die Abschlussmeldungen sollen systemctl daemon-reload empfehlen."""

    script = INSTALLER.read_text(encoding="utf-8")
    assert "Empfehlung nach Unit-Updates: sudo systemctl daemon-reload" in script


def test_installer_updates_readwrite_paths_list() -> None:
    """Das Installationsskript muss die vollständige ReadWritePaths-Liste setzen."""

    script = INSTALLER.read_text(encoding="utf-8")
    expected = (
        'UPDATED_READWRITE_PATHS="$INSTALL_ABS_PATH /etc/dhcpcd.conf /etc/hosts '
        '/etc/hostname /etc/wpa_supplicant /var/lib/dhcpcd"'
    )
    assert expected in script


def test_polkit_rule_allows_hostname_changes() -> None:
    """Die Polkit-Regel muss hostnamectl-Änderungen ohne sudo erlauben."""

    rule_content = POLKIT_RULE.read_text(encoding="utf-8")
    required_actions = (
        "org.freedesktop.hostname1.set-static-hostname",
        "org.freedesktop.hostname1.set-hostname",
    )
    for action in required_actions:
        assert action in rule_content
