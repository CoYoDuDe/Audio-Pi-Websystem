import os
import signal
import subprocess
import sys
import time


def test_scheduler_shutdown_without_start():
    env = os.environ.copy()
    env['FLASK_SECRET_KEY'] = 'test'
    env['TESTING'] = '1'
    proc = subprocess.Popen([sys.executable, 'app.py'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(1)
    proc.send_signal(signal.SIGINT)
    stdout, stderr = proc.communicate(timeout=5)
    assert b'SchedulerNotRunningError' not in stderr
