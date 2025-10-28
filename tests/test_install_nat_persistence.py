"""Tests für die Persistenz der NAT-Regeln im Installationsskript."""

import re
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[1] / "install.sh"


def test_ap_package_list_contains_netfilter_persistent():
    """Stellt sicher, dass netfilter-persistent automatisch installiert wird."""

    content = INSTALL_SH.read_text(encoding="utf-8")

    assert (
        "apt_get install -y hostapd dnsmasq wireless-tools iw wpasupplicant netfilter-persistent"
        in content
    ), "netfilter-persistent fehlt in der APT-Paketliste für den AP-Modus"


def test_nat_persistence_fallback_without_rc_local():
    """Prüft, dass auch ohne /etc/rc.local ein Boot-Mechanismus aktiv wird."""

    content = INSTALL_SH.read_text(encoding="utf-8")

    netfilter_block = re.search(
        r"if command -v netfilter-persistent >/dev/null 2>&1; then[\s\S]+?netfilter-persistent save",
        content,
    )
    fallback_block = re.search(
        r"audio-pi-iptables-restore\.service", content
    )

    assert netfilter_block, "netfilter-persistent save fehlt in configure_ap_networking"
    assert (
        fallback_block
    ), "Fallback-Systemd-Unit audio-pi-iptables-restore.service fehlt für Systeme ohne /etc/rc.local"
