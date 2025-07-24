import os
import sys
import sqlite3
import types
import importlib
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Fake modules required for app import
sys.modules["lgpio"] = types.SimpleNamespace(
    gpiochip_open=lambda *a, **k: 1,
    gpio_claim_output=lambda *a, **k: None,
    gpio_write=lambda *a, **k: None,
    gpio_free=lambda *a, **k: None,
    error=Exception,
)

sys.modules["pygame"] = types.SimpleNamespace(
    mixer=types.SimpleNamespace(
        init=lambda *a, **k: None,
        music=types.SimpleNamespace(set_volume=lambda *a, **k: None),
    )
)

sys.modules["pydub"] = types.SimpleNamespace(AudioSegment=types.SimpleNamespace())

sys.modules["smbus"] = types.SimpleNamespace(
    SMBus=lambda *a, **k: types.SimpleNamespace(
        read_i2c_block_data=lambda *a, **k: [0] * 7,
        write_i2c_block_data=lambda *a, **k: None,
    )
)

os.environ["FLASK_SECRET_KEY"] = "test"
os.environ["TESTING"] = "1"

# Use in-memory SQLite during tests
_orig_connect = sqlite3.connect

def connect_memory(*args, **kwargs):
    return _orig_connect(":memory:", check_same_thread=False)


def dummy_popen(*args, **kwargs):
    mock_proc = MagicMock()
    mock_proc.communicate.return_value = ("", "")
    return mock_proc


with patch("sqlite3.connect", side_effect=connect_memory), patch(
    "subprocess.getoutput", return_value="volume: 50%"
), patch("subprocess.call"), patch("subprocess.Popen", dummy_popen):
    import app
    importlib.reload(app)


class BluetoothToggleTests(unittest.TestCase):
    def test_bluetooth_on_route(self):
        with patch("app.subprocess.check_call") as call_mock, patch(
            "app.bluetooth_auto_accept"
        ) as auto_mock, patch("app.flash") as flash_mock, patch("app.redirect") as red_mock, patch(
            "app.url_for", return_value="/"
        ), patch(
            "flask_login.utils._get_user", return_value=type("U", (), {"is_authenticated": True})()
        ):
            with app.app.test_request_context("/bluetooth_on", method="POST"):
                app.bluetooth_on()
        call_mock.assert_any_call(["sudo", "bluetoothctl", "power", "on"])
        auto_mock.assert_called_once()
        red_mock.assert_called_with("/")
        flash_mock.assert_called()

    def test_bluetooth_off_route(self):
        with patch("app.subprocess.check_call") as call_mock, patch(
            "app.flash"
        ) as flash_mock, patch("app.redirect") as red_mock, patch(
            "app.url_for", return_value="/"
        ), patch(
            "flask_login.utils._get_user", return_value=type("U", (), {"is_authenticated": True})()
        ):
            with app.app.test_request_context("/bluetooth_off", method="POST"):
                app.bluetooth_off()
        call_mock.assert_any_call(["sudo", "bluetoothctl", "power", "off"])
        red_mock.assert_called_with("/")
        flash_mock.assert_called()


if __name__ == "__main__":
    unittest.main()
