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
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    if "app" in sys.modules:
        del sys.modules["app"]
    import app as app_module
    importlib.reload(app_module)

    app_module.pygame.mixer.music.get_busy = lambda: False

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    app_module.app.config["UPLOAD_FOLDER"] = str(upload_dir)
    return app_module


@pytest.fixture
def client(app_module):
    with app_module.app.test_client() as client:
        yield client, app_module


def test_scan_detects_first_available_address(app_module):
    class DummyBus:
        def __init__(self):
            self.calls = []

        def read_byte_data(self, address, register):
            self.calls.append((address, register))
            if address == 0x68:
                return 0x00
            raise OSError("kein Gerät")

    bus = DummyBus()
    detected = app_module.scan_i2c_addresses_for_rtc(bus, (0x51, 0x68))
    assert detected == 0x68
    assert (0x68, 0x00) in bus.calls


def test_scan_returns_none_when_no_device(app_module):
    class DummyBus:
        def read_byte_data(self, address, register):
            raise OSError("kein Gerät")

    bus = DummyBus()
    detected = app_module.scan_i2c_addresses_for_rtc(bus, (0x51, 0x68))
    assert detected is None


def _login(client):
    return client.post(
        "/login",
        data={"username": "admin", "password": "password"},
        follow_redirects=True,
    )


def test_index_shows_warning_without_rtc(client):
    client, app_module = client
    _login(client)
    app_module.RTC_AVAILABLE = False
    app_module.RTC_DETECTED_ADDRESS = None
    app_module.RTC_MISSING_FLAG = True

    response = client.get("/")
    assert b"Keine RTC erkannt" in response.data
    assert b"Nicht verf\xc3\xbcgbar" in response.data


def test_index_shows_address_when_rtc_available(client):
    client, app_module = client
    _login(client)
    app_module.RTC_AVAILABLE = True
    app_module.RTC_DETECTED_ADDRESS = 0x68
    app_module.RTC_MISSING_FLAG = False

    response = client.get("/")
    assert b"0x68" in response.data
    assert b"Keine RTC erkannt" not in response.data
