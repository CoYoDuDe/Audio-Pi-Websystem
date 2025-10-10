import importlib
import sys
from pathlib import Path

import pytest


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "testkey")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DB_FILE", str(tmp_path / "test.db"))

    repo_root = Path(__file__).resolve().parents[1]
    repo_root_str = str(repo_root)
    sys.path.insert(0, repo_root_str)
    if "app" in sys.modules:
        del sys.modules["app"]
    app_module = importlib.import_module("app")
    importlib.reload(app_module)
    app_module.gpio_handle = object()

    yield app_module

    if hasattr(app_module, "conn"):
        app_module.conn.close()
    if "app" in sys.modules:
        del sys.modules["app"]
    if repo_root_str in sys.path:
        sys.path.remove(repo_root_str)


def _login_dummy_user(app_module):
    user = app_module.User(1, "admin")
    app_module.login_user(user)


def test_set_relay_invert_updates_gpio_level(monkeypatch, app_module):
    writes = []

    def fake_claim(handle, pin, lFlags=0, level=0):
        writes.append(("claim", level))

    def fake_write(handle, pin, level):
        writes.append(("write", level))

    def fake_free(handle, pin):
        writes.append(("free", None))

    monkeypatch.setattr(app_module.GPIO, "gpio_claim_output", fake_claim)
    monkeypatch.setattr(app_module.GPIO, "gpio_write", fake_write)
    monkeypatch.setattr(app_module.GPIO, "gpio_free", fake_free)

    app_module.RELAY_INVERT = False
    app_module.update_amp_levels()
    app_module.amplifier_claimed = True

    with app_module.app.test_request_context(
        "/set_relay_invert", method="POST", data={"invert": "1"}
    ):
        _login_dummy_user(app_module)
        app_module.set_relay_invert()

    assert writes[-1] == ("write", app_module.AMP_ON_LEVEL)

    writes.clear()
    app_module.amplifier_claimed = False

    with app_module.app.test_request_context(
        "/set_relay_invert", method="POST", data={}
    ):
        _login_dummy_user(app_module)
        app_module.set_relay_invert()

    assert ("write", app_module.AMP_OFF_LEVEL) in writes
    assert ("free", None) in writes
    assert app_module.amplifier_claimed is False
