import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import types
import importlib
import unittest
from unittest.mock import patch, MagicMock

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


def dummy_popen(*args, **kwargs):
    mock_proc = MagicMock()
    mock_proc.communicate.return_value = ("", "")
    return mock_proc


with patch("subprocess.getoutput", return_value="volume: 50%"), patch(
    "subprocess.call"
), patch("subprocess.Popen", dummy_popen):
    import app

    importlib.reload(app)


class WlanConnectTest(unittest.TestCase):
    def test_password_with_special_chars(self):
        special_password = 'pa$$"w0rd\\path'
        special_ssid = 'Test"Net\\1'
        with patch("app.subprocess.check_output", return_value=b"0") as out_mock, patch(
            "app.subprocess.check_call"
        ) as call_mock, patch("app.flash"), patch("app.redirect"), patch(
            "app.url_for", return_value="/"
        ), patch(
            "flask_login.utils._get_user",
            return_value=type("U", (), {"is_authenticated": True})(),
        ):
            with app.app.test_request_context(
                "/wlan_connect",
                method="POST",
                data={"ssid": special_ssid, "password": special_password},
            ):
                app.wlan_connect()

        escaped_pw = special_password.encode("unicode_escape").decode()
        escaped_ssid = special_ssid.encode("unicode_escape").decode()
        out_mock.assert_called_with(["sudo", "wpa_cli", "-i", "wlan0", "add_network"])
        call_mock.assert_any_call(
            ["sudo", "wpa_cli", "-i", "wlan0", "set_network", "0", "ssid", f'"{escaped_ssid}"']
        )
        call_mock.assert_any_call(
            [
                "sudo",
                "wpa_cli",
                "-i",
                "wlan0",
                "set_network",
                "0",
                "psk",
                f'"{escaped_pw}"',
            ]
        )


if __name__ == "__main__":
    unittest.main()
