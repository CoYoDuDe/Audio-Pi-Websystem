"""Tests für die Persistenz der NAT-Regeln im Installationsskript."""

import re
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[1] / "install.sh"


def test_ap_package_list_contains_persistence_packages():
    """Stellt sicher, dass die Persistenz-Pakete automatisch installiert werden."""

    content = INSTALL_SH.read_text(encoding="utf-8")

    assert (
        "apt_get install -y hostapd dnsmasq wireless-tools iw wpasupplicant netfilter-persistent iptables-persistent"
        in content
    ), "netfilter-persistent und iptables-persistent fehlen in der APT-Paketliste für den AP-Modus"


def test_nat_persistence_fallback_without_rc_local():
    """Prüft, dass auch ohne /etc/rc.local ein Boot-Mechanismus aktiv wird."""

    content = INSTALL_SH.read_text(encoding="utf-8")

    netfilter_block = re.search(
        r"if command -v netfilter-persistent >/dev/null 2>&1; then[\s\S]+?netfilter-persistent save",
        content,
    )
    fallback_block = re.search(
        r"sudo install -m 644 \"\$AUDIO_PI_IPTABLES_UNIT_TEMPLATE\" \"\$AUDIO_PI_IPTABLES_UNIT_TARGET\"",
        content,
    )
    systemctl_enable = re.search(
        r"systemctl enable \"\$fallback_service_name\"", content
    )

    assert netfilter_block, "netfilter-persistent save fehlt in configure_ap_networking"
    assert (
        fallback_block
    ), "Fallback-Installation der systemd-Unit audio-pi-iptables-restore.service fehlt"
    assert systemctl_enable, "Fallback-Unit wird nicht für den automatischen Start aktiviert"
