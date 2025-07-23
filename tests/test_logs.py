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

# Use in-memory SQLite during tests
_original_connect = sqlite3.connect

def connect_memory(*args, **kwargs):
    return _original_connect(":memory:", check_same_thread=False)


def dummy_popen(*args, **kwargs):
    mock_proc = MagicMock()
    mock_proc.communicate.return_value = ("", "")
    return mock_proc


with patch("sqlite3.connect", side_effect=connect_memory), patch(
    "subprocess.getoutput", return_value="volume: 50%"
), patch("subprocess.call"), patch("subprocess.Popen", dummy_popen):
    import app
    importlib.reload(app)


class LogsTests(unittest.TestCase):
    def test_missing_logfile(self):
        def fake_open(path, mode="r", *args, **kwargs):
            if path == "app.log":
                raise FileNotFoundError
            return open_orig(path, mode, *args, **kwargs)

        open_orig = open
        with patch("builtins.open", side_effect=fake_open):
            with patch(
                "flask_login.utils._get_user",
                return_value=type("U", (), {"is_authenticated": True})(),
            ):
                with app.app.test_client() as client:
                    resp = client.get("/logs")
        self.assertIn("Keine Logdatei vorhanden", resp.get_data(as_text=True))

    def test_change_password_empty(self):
        app.cursor.execute("SELECT password FROM users WHERE id=?", (1,))
        before = app.cursor.fetchone()[0]
        with patch("app.flash") as flash_mock, patch(
            "flask_login.utils._get_user",
            return_value=type("U", (), {"is_authenticated": True, "id": 1})(),
        ), patch("app.render_template", return_value="form"):
            with app.app.test_request_context(
                "/change_password",
                method="POST",
                data={"old_password": "password", "new_password": ""},
            ):
                app.change_password()

        flash_mock.assert_called_with("Neues Passwort zu kurz")
        app.cursor.execute("SELECT password FROM users WHERE id=?", (1,))
        after = app.cursor.fetchone()[0]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
