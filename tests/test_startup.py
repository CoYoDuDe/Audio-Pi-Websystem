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

