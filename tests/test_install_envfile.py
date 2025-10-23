"""Tests für das Environment-File im Installationsskript."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .test_install_i2c_fallback import _prepare_fake_path


def test_install_dry_run_uses_env_file(tmp_path: Path) -> None:
    """`install.sh --dry-run` muss das Secret in eine Environment-Datei verlagern."""

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "install.sh"
    env = os.environ.copy()
    env["INSTALL_FLASK_SECRET_KEY"] = "dry-run-secret"
    env["PATH"] = _prepare_fake_path(tmp_path)

    result = subprocess.run(
        ["/bin/bash", str(script), "--dry-run"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        cwd=repo_root,
        env=env,
    )

    combined_output = f"{result.stdout}{result.stderr}"

    assert result.returncode == 0, combined_output
    assert "dry-run-secret" not in combined_output
    assert "[Dry-Run] Würde /etc/audio-pi" in combined_output
    assert "[Dry-Run] Würde Secret in /etc/audio-pi/audio-pi.env" in combined_output
    assert "Environment=\"FLASK_SECRET_KEY" not in combined_output
    assert "if [ -f /etc/audio-pi/audio-pi.env ]; then . /etc/audio-pi/audio-pi.env; fi" in combined_output

    service_content = (repo_root / "audio-pi.service").read_text(encoding="utf-8")
    assert "Environment=\"FLASK_SECRET_KEY" not in service_content
    assert "EnvironmentFile=/etc/audio-pi/audio-pi.env" in service_content
