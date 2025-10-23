"""Tests für den I²C-Fallback im Installationsskript."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _prepare_fake_path(base_dir: Path) -> str:
    fake_bin = base_dir / "fake-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)

    for command in (
        "grep",
        "mkdir",
        "touch",
        "tee",
        "cat",
        "dirname",
        "id",
        "python3",
        "install",
        "mktemp",
        "rm",
    ):
        source = shutil.which(command)
        if source is None:
            raise AssertionError(f"Benötigtes Kommando '{command}' wurde nicht gefunden.")
        target = fake_bin / command
        if not target.exists():
            target.symlink_to(source)

    sudo_stub = fake_bin / "sudo"
    sudo_stub.write_text(
        """#!/bin/sh
cmd="$1"
shift

case "$cmd" in
  grep)
    if [ "$1" = "-Eq" ] && [ "$3" = "/boot/firmware/config.txt" -o "$3" = "/boot/config.txt" ]; then
      exit 1
    fi
    if [ "$1" = "-q" ] && [ "$2" = "^i2c-dev$" ] && [ "$3" = "/etc/modules" ]; then
      exit 1
    fi
    ;;
  tee)
    cat >/dev/null
    exit 0
    ;;
esac

exec "$cmd" "$@"
""",
        encoding="utf-8",
    )
    sudo_stub.chmod(0o755)

    return str(fake_bin)


def test_install_dry_run_uses_device_tree_fallback(tmp_path: Path) -> None:
    """`install.sh --dry-run` darf ohne raspi-config nicht fehlschlagen."""

    script = Path(__file__).resolve().parents[1] / "install.sh"
    env = os.environ.copy()
    env["PATH"] = _prepare_fake_path(tmp_path)
    env["INSTALL_FLASK_SECRET_KEY"] = "Fallback-SecretKey_Example-1234567890"

    result = subprocess.run(
        ["/bin/bash", str(script), "--dry-run"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env=env,
    )

    combined_output = f"{result.stdout}{result.stderr}"

    assert result.returncode == 0, combined_output
    assert "raspi-config nicht gefunden" in combined_output
    assert "Würde i2c-dev zu /etc/modules hinzufügen" in combined_output
