"""Tests für das Secret-Key-Fallback-Verhalten."""

import importlib
import sys
from pathlib import Path


def test_secret_key_is_generated_when_missing(monkeypatch):
    """Stellt sicher, dass ohne FLASK_SECRET_KEY ein Fallback erzeugt wird."""
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    monkeypatch.setenv("TESTING", "1")

    if "app" in sys.modules:
        del sys.modules["app"]

    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    module = importlib.import_module("app")

    assert module.app.secret_key
    assert module.SECRET_KEY_GENERATED is True
    assert module.app.config.get("SECRET_KEY_GENERATED") is True
