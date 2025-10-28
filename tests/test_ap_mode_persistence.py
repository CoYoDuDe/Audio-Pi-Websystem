"""Tests rund um die Persistenz-Einrichtung des AP-Modus."""

from pathlib import Path

INSTALL_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = INSTALL_ROOT / "install.sh"
SERVICE_TEMPLATE = INSTALL_ROOT / "scripts" / "systemd" / "audio-pi-iptables-restore.service"


def test_fallback_unit_template_contains_required_directives():
    """Die mitgelieferte systemd-Unit muss alle wichtigen Direktiven enthalten."""

    content = SERVICE_TEMPLATE.read_text(encoding="utf-8")

    assert "ConditionPathExists=/etc/iptables.ipv4.nat" in content
    assert "ExecStart=/usr/bin/env iptables-restore /etc/iptables.ipv4.nat" in content
    assert "Before=network-pre.target" in content


def test_fallback_unit_is_configured_before_rc_local_block():
    """Stellt sicher, dass der Fallback greift, auch wenn /etc/rc.local fehlt."""

    content = INSTALL_SH.read_text(encoding="utf-8")

    fallback_index = content.index(
        'sudo install -m 644 "$AUDIO_PI_IPTABLES_UNIT_TEMPLATE" "$AUDIO_PI_IPTABLES_UNIT_TARGET"'
    )
    rc_local_index = content.index("if [ -f /etc/rc.local ]; then")

    assert fallback_index < rc_local_index, \
        "Der Fallback muss vor dem optionalen /etc/rc.local-Block eingerichtet werden."

