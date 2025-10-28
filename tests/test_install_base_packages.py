"""Tests für die Basispaketliste des Installationsskripts."""

import re
from pathlib import Path


def test_install_script_installs_git_alongside_python_basics():
    """Stellt sicher, dass git in der APT-Basispaketliste enthalten ist."""

    install_script = Path(__file__).resolve().parents[1] / "install.sh"
    content = install_script.read_text(encoding="utf-8")

    pattern = re.compile(
        r"apt_get install -y python3 python3-pip python3-venv sqlite3 git"
    )

    assert pattern.search(content), "git fehlt in der Basispaketliste"
