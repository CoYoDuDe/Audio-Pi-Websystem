"""Tests für das Secret-Key-Fallback-Verhalten."""

import importlib
import sys
from pathlib import Path


def _reload_app_module():
    if "app" in sys.modules:
        del sys.modules["app"]

    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    return importlib.import_module("app")


def test_secret_key_is_generated_and_persisted(monkeypatch, tmp_path):
    """Stellt sicher, dass ohne FLASK_SECRET_KEY ein persistenter Fallback erzeugt wird."""
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    monkeypatch.setenv("FLASK_SECRET_KEY_FILE", str(tmp_path / "secret_key"))
    monkeypatch.setenv("TESTING", "1")

    module = _reload_app_module()

    assert module.app.secret_key
    assert module.SECRET_KEY_GENERATED is True
    assert module.app.config.get("SECRET_KEY_GENERATED") is True

    generated_key = module.app.secret_key
    secret_key_file = Path(module.app.config.get("SECRET_KEY_FILE"))
    assert secret_key_file.exists()
    assert secret_key_file.read_text(encoding="utf-8").strip() == generated_key

    module = _reload_app_module()

    assert module.app.secret_key == generated_key
    assert Path(module.app.config.get("SECRET_KEY_FILE")) == secret_key_file
    assert module.SECRET_KEY_GENERATED is False
    assert module.app.config.get("SECRET_KEY_GENERATED") is False
