import importlib
import importlib.util
import multiprocessing
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from apscheduler.schedulers.background import BackgroundScheduler

os.environ.setdefault('FLASK_SECRET_KEY', 'test')
os.environ.setdefault('TESTING', '1')

app = importlib.import_module('app')


def test_scheduler_shutdown_without_start():
    env = os.environ.copy()
    env['FLASK_SECRET_KEY'] = 'test'
    env['TESTING'] = '1'
    proc = subprocess.Popen([sys.executable, 'app.py'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(1)
    proc.send_signal(signal.SIGINT)
    stdout, stderr = proc.communicate(timeout=5)
    assert b'SchedulerNotRunningError' not in stderr


def test_start_stop_helpers_idempotent(monkeypatch):
    scheduler = BackgroundScheduler()
    monkeypatch.setattr(app, "scheduler", scheduler)
    monkeypatch.setattr(app, "_BACKGROUND_SERVICES_STARTED", False, raising=False)

    try:
        assert app.start_background_services() is True
        assert app.start_background_services() is False
        assert scheduler.running is True
        assert app.stop_background_services(wait=False) is True
        assert app.stop_background_services(wait=False) is False
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)


def test_gunicorn_hooks_coordinate_background_services(monkeypatch):
    monkeypatch.delenv('AUDIO_PI_SUPPRESS_AUTOSTART', raising=False)
    spec = importlib.util.spec_from_file_location(
        "gunicorn_conf_test",
        Path(__file__).resolve().parents[1] / "gunicorn.conf.py",
    )
    gunicorn_conf = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(gunicorn_conf)

    owner_value = multiprocessing.Value('i', 0)
    monkeypatch.setattr(gunicorn_conf, "_BACKGROUND_SERVICE_OWNER", owner_value)

    start_calls = []
    stop_calls = []

    def fake_start_background_services():
        start_calls.append("start")
        return True

    def fake_stop_background_services():
        stop_calls.append("stop")
        return True

    monkeypatch.setattr(app, "start_background_services", fake_start_background_services)
    monkeypatch.setattr(app, "stop_background_services", fake_stop_background_services)

    worker_one = SimpleNamespace(pid=1111)
    worker_two = SimpleNamespace(pid=2222)

    gunicorn_conf.post_fork(None, worker_one)
    assert len(start_calls) == 1
    assert owner_value.value == worker_one.pid

    gunicorn_conf.post_fork(None, worker_two)
    assert len(start_calls) == 1
    assert owner_value.value == worker_one.pid

    gunicorn_conf.worker_exit(None, worker_two)
    assert not stop_calls
    assert owner_value.value == worker_one.pid

    gunicorn_conf.worker_exit(None, worker_one)
    assert len(stop_calls) == 1
    assert owner_value.value == 0

    gunicorn_conf.post_fork(None, worker_two)
    assert len(start_calls) == 2
    assert owner_value.value == worker_two.pid
    monkeypatch.delenv('AUDIO_PI_SUPPRESS_AUTOSTART', raising=False)
