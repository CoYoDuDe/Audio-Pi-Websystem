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
        music=types.SimpleNamespace(
            set_volume=lambda *a, **k: None,
            load=lambda *a, **k: None,
            play=lambda *a, **k: None,
            get_busy=lambda *a, **k: False,
        ),
    )
)

sys.modules["pydub"] = types.SimpleNamespace(
    AudioSegment=types.SimpleNamespace(
        from_file=lambda *a, **k: types.SimpleNamespace(
            normalize=lambda *a, **k: types.SimpleNamespace(export=lambda *a, **k: None)
        )
    )
)

sys.modules["smbus"] = types.SimpleNamespace(
    SMBus=lambda *a, **k: types.SimpleNamespace(
        read_i2c_block_data=lambda *a, **k: [0] * 7,
        write_i2c_block_data=lambda *a, **k: None,
    )
)


os.environ["FLASK_SECRET_KEY"] = "test"
os.environ["TESTING"] = "1"

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


class ScheduleValidationTests(unittest.TestCase):
    def setUp(self):
        app.cursor.execute("DELETE FROM schedules")
        app.conn.commit()

    def test_missing_item_id(self):
        with patch("app.flash") as flash_mock, patch("app.redirect") as red_mock, patch(
            "app.url_for", return_value="/"
        ), patch(
            "flask_login.utils._get_user", return_value=type("U", (), {"is_authenticated": True})()
        ):
            with app.app.test_request_context(
                "/schedule",
                method="POST",
                data={
                    "item_type": "file",
                    "file_id": "",
                    "playlist_id": "",
                    "time": "2024-01-01T10:00",
                    "repeat": "once",
                    "delay": "0",
                },
            ):
                app.add_schedule()

        flash_mock.assert_called_with("Kein Element gewählt")
        red_mock.assert_called_with("/")
        app.cursor.execute("SELECT COUNT(*) FROM schedules")
        self.assertEqual(app.cursor.fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
