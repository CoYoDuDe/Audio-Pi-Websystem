import importlib
import sys
from collections import namedtuple
from pathlib import Path

import pytest

from tests.csrf_utils import csrf_post


def _load_app(tmp_path, monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "testkey")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DB_FILE", str(tmp_path / "test.db"))
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "password")

    repo_root = Path(__file__).resolve().parents[1]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)
    if "app" in sys.modules:
        del sys.modules["app"]
    app_module = importlib.import_module("app")
    importlib.reload(app_module)
    return app_module


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    module = _load_app(tmp_path, monkeypatch)
    module.pygame.mixer.music.get_busy = lambda: False
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    module.app.config["UPLOAD_FOLDER"] = str(upload_dir)
    yield module
    if "app" in sys.modules:
        del sys.modules["app"]


@pytest.fixture
def client(app_module):
    with app_module.app.test_client() as client:
        yield client, app_module


def _login(client):
    response = csrf_post(
        client,
        "/login",
        data={"username": "admin", "password": "password"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    change_response = csrf_post(
        client,
        "/change_password",
        data={"old_password": "password", "new_password": "password1234"},
        follow_redirects=True,
        source_url="/change_password",
    )
    assert b"Passwort ge\xc3\xa4ndert" in change_response.data


def test_system_metrics_include_safe_labels(tmp_path, monkeypatch):
    app_module = _load_app(tmp_path, monkeypatch)

    monkeypatch.setattr(app_module, "_read_cpu_temperature_c", lambda: 52.3)
    monkeypatch.setattr(app_module, "_read_uptime_seconds", lambda: 90061.0)
    monkeypatch.setattr(
        app_module,
        "_read_memory_snapshot",
        lambda: {
            "total": 1024 * 1024 * 1024,
            "available": 256 * 1024 * 1024,
            "used": 768 * 1024 * 1024,
        },
    )
    monkeypatch.setattr(app_module.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(app_module.os, "getloadavg", lambda: (1.0, 0.5, 0.25))
    DiskUsage = namedtuple("usage", "total used free")
    monkeypatch.setattr(
        app_module.shutil,
        "disk_usage",
        lambda _path: DiskUsage(
            total=1024 * 1024 * 1024,
            used=768 * 1024 * 1024,
            free=256 * 1024 * 1024,
        ),
    )

    metrics = app_module.gather_system_metrics()

    assert metrics["temperature_label"] == "52.3 °C"
    assert metrics["load_percent_label"] == "25%"
    assert metrics["memory_percent_label"] == "75%"
    assert metrics["disk_percent_label"] == "75%"
    assert metrics["uptime_label"] == "1d 1h 1m"


def test_status_page_renders_system_metrics(client):
    client, app_module = client

    _login(client)
    app_module.gather_system_metrics = lambda: {
        "temperature_label": "49.5 °C",
        "load_percent_label": "12%",
        "load_label": "0.48 / 0.40 / 0.30",
        "memory_percent_label": "34%",
        "memory_label": "512.0 MB / 1.5 GB",
        "disk_percent_label": "41%",
        "disk_label": "6.1 GB / 14.8 GB",
        "uptime_label": "2h 3m",
    }

    response = client.get("/")

    assert b"Temperatur" in response.data
    assert "49.5 °C".encode("utf-8") in response.data
    assert b"CPU-Last" in response.data
