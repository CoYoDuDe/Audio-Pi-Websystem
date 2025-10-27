"""Tests für die Secret-Validierung im Installationsskript."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .test_install_i2c_fallback import _prepare_fake_path


def test_install_rejects_too_short_secret(tmp_path: Path) -> None:
    """Zu kurze Secrets müssen mit Fehler abbrechen."""

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "install.sh"

    env = os.environ.copy()
    env.update(
        {
            "INSTALL_FLASK_SECRET_KEY": "short-secret",
            "INSTALL_ENV_DIR": str(tmp_path / "etc" / "audio-pi"),
            "INSTALL_TARGET_HOME": str(tmp_path / "home"),
            "INSTALL_EXIT_AFTER_SECRET": "1",
            "PATH": _prepare_fake_path(tmp_path),
        }
    )
    env.setdefault("INSTALL_TARGET_USER", "root")

    result = subprocess.run(
        ["/bin/bash", str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        cwd=repo_root,
        env=env,
    )

    combined_output = f"{result.stdout}{result.stderr}"

    assert result.returncode != 0, combined_output
    assert "Ungültiger Secret-Key" in combined_output
    assert "mindestens 32 Zeichen" in combined_output


def test_install_generates_secret_into_env_file(tmp_path: Path) -> None:
    """Bei aktivierter Auto-Generierung muss ein Secret im Env-File landen."""

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "install.sh"

    env_dir = tmp_path / "etc" / "audio-pi"
    profile_home = tmp_path / "home" / "testuser"
    profile_home.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "INSTALL_GENERATE_SECRET": "1",
            "INSTALL_EXIT_AFTER_SECRET": "1",
            "INSTALL_ENV_DIR": str(env_dir),
            "INSTALL_TARGET_HOME": str(profile_home),
            "PATH": _prepare_fake_path(tmp_path),
        }
    )
    env.setdefault("INSTALL_TARGET_USER", "root")

    result = subprocess.run(
        ["/bin/bash", str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        cwd=repo_root,
        env=env,
    )

    combined_output = f"{result.stdout}{result.stderr}"

    assert result.returncode == 0, combined_output
    assert "Generiere automatischen Secret-Key" in combined_output

    env_file = env_dir / "audio-pi.env"
    assert env_file.exists(), combined_output

    content = env_file.read_text(encoding="utf-8").strip()
    assert content.startswith("FLASK_SECRET_KEY="), content
    secret = content.split("=", 1)[1]
    assert len(secret) >= 32

    classes = 0
    if re.search(r"[a-z]", secret):
        classes += 1
    if re.search(r"[A-Z]", secret):
        classes += 1
    if re.search(r"[0-9]", secret):
        classes += 1
    if re.search(r"[^A-Za-z0-9]", secret):
        classes += 1

    assert classes >= 3, secret
