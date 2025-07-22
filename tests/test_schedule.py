import os
import sys
import sqlite3
import types
import importlib
import unittest
from unittest.mock import patch, MagicMock
from freezegun import freeze_time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Fake modules to satisfy imports in app
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
            normalize=lambda *a, **k: types.SimpleNamespace(
                export=lambda *a, **k: None
            )
        )
    )
)

sys.modules["smbus"] = types.SimpleNamespace(
    SMBus=lambda *a, **k: types.SimpleNamespace(
        read_i2c_block_data=lambda *a, **k: [0] * 7,
        write_i2c_block_data=lambda *a, **k: None,
    )
)

sys.modules["schedule"] = types.SimpleNamespace(
    every=lambda *a, **k: types.SimpleNamespace(do=lambda *a, **k: None),
    run_pending=lambda *a, **k: None,
    clear=lambda *a, **k: None,
)

os.environ["FLASK_SECRET_KEY"] = "test"

# Use in-memory SQLite during tests
original_connect = sqlite3.connect

def connect_memory(*args, **kwargs):
    return original_connect(":memory:", check_same_thread=False)


def dummy_popen(*args, **kwargs):
    mock_proc = MagicMock()
    mock_proc.communicate.return_value = ("", "")
    return mock_proc


with patch("sqlite3.connect", side_effect=connect_memory), patch(
    "subprocess.getoutput", return_value="volume: 50%"
), patch("subprocess.call"), patch("subprocess.Popen", dummy_popen):
    import app
    importlib.reload(app)


class ScheduleOnceTests(unittest.TestCase):
    def setUp(self):
        app.cursor.execute("DELETE FROM schedules")
        app.conn.commit()

    def test_skip_past_once_schedules(self):
        past_time = "2024-01-01 10:00:00"
        future_time = "2025-01-01 10:00:00"
        app.cursor.execute(
            "INSERT INTO schedules (item_id, item_type, time, repeat, delay, executed) VALUES (?, ?, ?, ?, ?, 0)",
            (1, "file", past_time, "once", 0),
        )
        past_id = app.cursor.lastrowid
        app.cursor.execute(
            "INSERT INTO schedules (item_id, item_type, time, repeat, delay, executed) VALUES (?, ?, ?, ?, ?, 0)",
            (1, "file", future_time, "once", 0),
        )
        future_id = app.cursor.lastrowid
        app.conn.commit()

        with freeze_time("2024-01-02 00:00:00"):
            app.skip_past_once_schedules()

        app.cursor.execute("SELECT executed FROM schedules WHERE id=?", (past_id,))
        executed_past = app.cursor.fetchone()[0]
        app.cursor.execute("SELECT executed FROM schedules WHERE id=?", (future_id,))
        executed_future = app.cursor.fetchone()[0]
        self.assertEqual(executed_past, 1)
        self.assertEqual(executed_future, 0)

    def test_schedule_job_marks_executed(self):
        app.cursor.execute(
            "INSERT INTO schedules (item_id, item_type, time, repeat, delay, executed) VALUES (?, ?, ?, ?, ?, 0)",
            (1, "file", "2024-02-01 10:00:00", "once", 0),
        )
        sch_id = app.cursor.lastrowid
        app.conn.commit()

        with patch.object(app, "play_item"), patch.object(app, "load_schedules"):
            app.schedule_job(sch_id)

        app.cursor.execute("SELECT executed FROM schedules WHERE id=?", (sch_id,))
        self.assertEqual(app.cursor.fetchone()[0], 1)

    def test_schedule_job_missing_schedule(self):
        with patch.object(app, "play_item") as play_mock, patch.object(
            app.logging,
            "warning",
        ) as warn_mock:
            app.schedule_job(9999)
            play_mock.assert_not_called()
            warn_mock.assert_called()


if __name__ == "__main__":
    unittest.main()
