from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
INSTALL = ROOT / "install.sh"


def _extract_default_mode(variable: str, content: str) -> str:
    pattern = rf'{variable}="\$\{{[^:]+:-([0-7]{{3,4}})\}}"'
    match = re.search(pattern, content)
    if match is None:
        raise AssertionError(f"Konnte Standardwert für {variable} nicht ermitteln")
    return match.group(1)


def test_readme_matches_installer_permission_defaults() -> None:
    readme_text = README.read_text(encoding="utf-8")
    install_text = INSTALL.read_text(encoding="utf-8")

    upload_mode = _extract_default_mode("UPLOAD_DIR_MODE", install_text)
    log_mode = _extract_default_mode("DEFAULT_LOG_FILE_MODE", install_text)

    assert f"`uploads/`: `chmod {upload_mode}`" in readme_text
    assert f"`app.log`: `chmod {log_mode}`" in readme_text


def test_logrotate_template_documented() -> None:
    readme_text = README.read_text(encoding="utf-8")
    assert "scripts/logrotate/audio-pi" in readme_text
    assert "create <MODE" in readme_text
    assert "INSTALL_LOG_FILE_MODE" in readme_text
