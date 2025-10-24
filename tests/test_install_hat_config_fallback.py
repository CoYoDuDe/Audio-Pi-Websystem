"""Tests für die Fallback-Behandlung der HAT-/Audio-Konfiguration."""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path


def test_hat_overlay_prefers_firmware_config_when_standard_missing() -> None:
    """Die HAT-Helfer sollen /boot/firmware/config.txt verwenden, wenn /boot/config.txt fehlt."""

    script = Path(__file__).resolve().parents[1] / "install.sh"

    boot_dir = Path("/boot")
    boot_config = boot_dir / "config.txt"
    firmware_dir = boot_dir / "firmware"
    firmware_config = firmware_dir / "config.txt"

    original_boot_config = None
    if boot_config.exists():
        original_boot_config = boot_config.read_text(encoding="utf-8")
        boot_config.unlink()

    firmware_dir.mkdir(parents=True, exist_ok=True)

    original_firmware_config = None
    if firmware_config.exists():
        original_firmware_config = firmware_config.read_text(encoding="utf-8")

    firmware_config.write_text("dtparam=audio=on\n", encoding="utf-8")

    shell_script = textwrap.dedent(
        """
        set -e
        INSTALL_DRY_RUN=1
        INSTALL_LIBRARY_ONLY=1 source "{script}"
        config_path=$(resolve_config_txt_path "Testlauf")
        echo "CONFIG_PATH:${{config_path}}"
        apply_hat_overlay "test-overlay" "foo=bar"
        ensure_audio_dtparam 1
        ensure_audio_dtparam 0
        HAT_SELECTED_LABEL="Testprofil"
        HAT_SELECTED_OVERLAY=""
        HAT_SELECTED_OPTIONS=""
        HAT_SELECTED_SINK_HINT="alsa_output.test"
        HAT_SELECTED_NOTES=""
        HAT_SELECTED_KEY="manual"
        print_audio_hat_summary
        """
    ).format(script=script)

    env = os.environ.copy()
    result = subprocess.run(
        ["/bin/bash", "-lc", shell_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        check=False,
    )

    if original_boot_config is not None:
        boot_config.write_text(original_boot_config, encoding="utf-8")
    if original_firmware_config is not None:
        firmware_config.write_text(original_firmware_config, encoding="utf-8")

    assert result.returncode == 0, f"{result.stdout}{result.stderr}"

    stdout = result.stdout
    assert "CONFIG_PATH:/boot/firmware/config.txt" in stdout
    assert "/boot/firmware/config.txt.hat.bak." in stdout
    assert "[Dry-Run] Würde vorhandene dtoverlay=test-overlay Einträge aus /boot/firmware/config.txt entfernen." in stdout
    assert "[Dry-Run] Würde 'dtoverlay=test-overlay,foo=bar' an /boot/firmware/config.txt anhängen." in stdout
    assert "[Dry-Run] Würde 'dtparam=audio=off' an /boot/firmware/config.txt anhängen." in stdout
    assert "[Dry-Run] Würde 'dtparam=audio=on' an /boot/firmware/config.txt anhängen." in stdout
    assert "--- Zusammenfassung Audio-HAT ---" in stdout
    assert "Anpassung später: Konfigurationsdatei: /boot/firmware/config.txt und sqlite3 audio.db 'UPDATE settings SET value=... WHERE key=\\'dac_sink_name\\';'" in stdout



def test_hat_overlay_removes_existing_entry_with_options(tmp_path: Path) -> None:
    """Einträge mit Optionen dürfen beim erneuten Anwenden nicht dupliziert werden."""

    script = Path(__file__).resolve().parents[1] / "install.sh"

    firmware_dir = Path("/boot/firmware")
    firmware_dir.mkdir(parents=True, exist_ok=True)
    firmware_config = firmware_dir / "config.txt"

    original_content = None
    if firmware_config.exists():
        original_content = firmware_config.read_text(encoding="utf-8")

    overlay_line = "dtoverlay=test-overlay,foo=bar"
    firmware_config.write_text(
        "\n".join(
            (
                "# Testkonfiguration für Audio-HAT",
                "dtparam=audio=on",
                overlay_line,
                "# Ende der Testkonfiguration",
                "",
            )
        ),
        encoding="utf-8",
    )

    sudo_stub = tmp_path / "sudo"
    sudo_stub.write_text("#!/bin/sh\nexec \"$@\"\n", encoding="utf-8")
    sudo_stub.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{sudo_stub.parent}:{env.get('PATH', '')}"

    shell_script = textwrap.dedent(
        f"""
        set -e
        INSTALL_LIBRARY_ONLY=1 source "{script}"
        INSTALL_DRY_RUN=0
        apply_hat_overlay "test-overlay" "foo=bar"
        apply_hat_overlay "test-overlay" "foo=bar"
        """
    )

    existing_backup_names = {
        path.name for path in firmware_dir.glob("config.txt.hat.bak.*")
    }

    try:
        result = subprocess.run(
            ["/bin/bash", "-lc", shell_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            check=False,
        )

        assert result.returncode == 0, f"{result.stdout}{result.stderr}"

        content_after = firmware_config.read_text(encoding="utf-8")
        overlay_lines = [
            line for line in content_after.splitlines() if line.startswith("dtoverlay=test-overlay")
        ]
        assert overlay_lines == [overlay_line]
    finally:
        if original_content is None:
            firmware_config.unlink(missing_ok=True)
        else:
            firmware_config.write_text(original_content, encoding="utf-8")

        for backup_file in firmware_dir.glob("config.txt.hat.bak.*"):
            if backup_file.name not in existing_backup_names:
                backup_file.unlink(missing_ok=True)
