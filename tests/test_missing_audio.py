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
    AudioSegment=types.SimpleNamespace(from_file=lambda *a, **k: types.SimpleNamespace(normalize=lambda *a, **k: types.SimpleNamespace(export=lambda *a, **k: None)))
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


class MissingAudioTests(unittest.TestCase):
    def setUp(self):
        app.cursor.execute("DELETE FROM audio_files")
        app.conn.commit()
        app.cursor.execute(
            "INSERT INTO audio_files (filename) VALUES (?)", ("missing.mp3",)
        )
        self.file_id = app.cursor.lastrowid
        app.conn.commit()
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
                    normalize=lambda *a, **k: types.SimpleNamespace(
                        export=lambda *a, **k: None
                    )
                )
            )
        )
        importlib.reload(app)

    def test_play_item_missing_file(self):
        with patch.object(app.AudioSegment, "from_file") as from_mock, patch(
            "app.pygame.mixer.music.load"
        ) as load_mock, patch("app.pygame.mixer.music.play") as play_mock, patch(
            "app.os.path.exists", return_value=False
        ), patch("app.flash") as flash_mock, patch("app.logging.warning") as warn_mock, patch(
            "app.subprocess.call"
        ):
            with app.app.test_request_context("/"):
                app.play_item(self.file_id, "file", 0, False)
        warn_mock.assert_called()
        from_mock.assert_not_called()
        load_mock.assert_not_called()
        play_mock.assert_not_called()
        flash_mock.assert_called_with("Audiodatei net g'fundet")


if __name__ == "__main__":
    unittest.main()
