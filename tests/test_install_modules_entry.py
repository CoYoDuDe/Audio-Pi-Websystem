"""Tests für das Installationsskript bezogen auf den I²C-Modul-Eintrag."""

from pathlib import Path


def test_install_script_adds_i2c_dev_idempotent():
    """Stellt sicher, dass i2c-dev nur einmal in /etc/modules geschrieben wird."""

    install_script = Path(__file__).resolve().parents[1] / "install.sh"
    content = install_script.read_text(encoding="utf-8")

    assert "grep -q '^i2c-dev$' /etc/modules" in content
    assert "tee -a /etc/modules" in content

    guard_index = content.index("grep -q '^i2c-dev$' /etc/modules")
    tee_index = content.index("tee -a /etc/modules", guard_index)

    assert tee_index > guard_index
    assert "ist bereits in /etc/modules" in content

