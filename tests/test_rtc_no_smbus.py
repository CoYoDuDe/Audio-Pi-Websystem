import builtins
import importlib
import sys
from pathlib import Path


def test_rtc_initialization_without_smbus(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "testkey")
    monkeypatch.setenv("TESTING", "1")

    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "smbus":
            raise ImportError("Test: smbus nicht verfügbar")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    sys.modules.pop("app", None)
    sys.modules.pop("smbus", None)

    repo_root = Path(__file__).resolve().parents[1]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)

    app_module = importlib.import_module("app")

    assert app_module.SMBUS_AVAILABLE is False
    assert app_module.bus is None
    assert app_module.RTC_AVAILABLE is False
    assert app_module.RTC_MISSING_FLAG is True
    assert app_module.RTC_DETECTED_ADDRESS is None

    sys.modules.pop("app", None)
