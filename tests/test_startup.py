import os
import subprocess
import sys
from pathlib import Path

def test_upload_dir_created_on_import(tmp_path):
    env = os.environ.copy()
    env['FLASK_SECRET_KEY'] = 'test'
    env['TESTING'] = '1'
    env['DB_FILE'] = str(tmp_path / 'test.db')
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
    subprocess.run([sys.executable, '-c', 'import app'], env=env, cwd=tmp_path, check=True)
    assert (tmp_path / 'uploads').is_dir()


def test_future_once_schedule_not_marked_executed(tmp_path):
    env = os.environ.copy()
    env['FLASK_SECRET_KEY'] = 'test'
    env['TESTING'] = '1'
    env['DB_FILE'] = str(tmp_path / 'test.db')
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
    script = '''
import sqlite3
from datetime import datetime, timedelta

import app

future_time = datetime.now() + timedelta(seconds=5)
with app.get_db_connection() as (conn, cursor):
    cursor.execute("DELETE FROM schedules")
    cursor.execute(
        "INSERT INTO schedules (item_id, item_type, time, repeat, executed) VALUES (?, ?, ?, ?, 0)",
        (1, "file", future_time.isoformat(), "once"),
    )
    conn.commit()

app.skip_past_once_schedules()

with app.get_db_connection() as (conn, cursor):
    executed = cursor.execute(
        "SELECT executed FROM schedules WHERE repeat='once'"
    ).fetchone()[0]
    assert executed == 0, "Zeitplan in der Zukunft wurde fälschlicherweise übersprungen"
'''
    subprocess.run([sys.executable, '-c', script], env=env, cwd=tmp_path, check=True)

