"""Tests für die Dienstbenutzer-Ermittlung im Installationsskript."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .test_install_i2c_fallback import _prepare_fake_path


def _script_path() -> Path:
    return Path(__file__).resolve().parents[1] / "install.sh"


def _base_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = _prepare_fake_path(tmp_path)
    env["INSTALL_FLASK_SECRET_KEY"] = "Example-Secret-Key_mit-Genug-Zeichen-1234567890"
    env["INSTALL_DRY_RUN"] = "1"
    env["USER"] = "root"
    env.pop("SUDO_USER", None)
    return env


def test_install_without_target_user_fails_for_root(tmp_path: Path) -> None:
    """Ohne Override darf der Installer als Root nicht durchlaufen."""

    script = _script_path()
    env = _base_env(tmp_path)

    result = subprocess.run(
        ["/bin/bash", str(script), "--dry-run"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        cwd=script.parent,
        env=env,
    )

    combined_output = f"{result.stdout}{result.stderr}"
    if "Dienstbenutzer wäre: pi" in combined_output:
        assert result.returncode == 0, combined_output
        assert "pi-Fallback" in combined_output
    else:
        assert result.returncode != 0, combined_output
        assert "Root-Dienstbenutzer" in combined_output
        assert "--target-user" in combined_output


def test_install_honours_target_user_override(tmp_path: Path) -> None:
    """Mit Override soll der Dry-Run den explizit gesetzten Benutzer verwenden."""

    script = _script_path()
    env = _base_env(tmp_path)
    env["INSTALL_TARGET_USER"] = "nobody"

    result = subprocess.run(
        ["/bin/bash", str(script), "--dry-run"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        cwd=script.parent,
        env=env,
    )

    combined_output = f"{result.stdout}{result.stderr}"

    assert result.returncode == 0, combined_output
    assert "[Dry-Run] Dienstbenutzer wäre: nobody" in combined_output

    target_group = subprocess.check_output(["id", "-gn", "nobody"], text=True).strip()
    assert f"[Dry-Run] Primäre Gruppe wäre: {target_group} (GID" in combined_output
    assert "sudo usermod -aG netdev \"nobody\"" in combined_output
