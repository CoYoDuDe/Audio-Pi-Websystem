import importlib
import re
import sys
from pathlib import Path

import pytest

from tests.csrf_utils import csrf_post


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "testkey")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DB_FILE", str(tmp_path / "test.db"))
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "password")
    monkeypatch.setenv("AUDIO_PI_DISABLE_SUDO", "1")
    monkeypatch.setenv("AUDIO_PI_LOG_FILE", str(tmp_path / "logs" / "app.log"))
    monkeypatch.setenv("AUDIO_PI_LOG_VIEW_MAX_LINES", "50")
    monkeypatch.setenv("AUDIO_PI_LOG_VIEW_MAX_BYTES", "1500")

    repo_root = Path(__file__).resolve().parents[1]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)
    if "app" in sys.modules:
        del sys.modules["app"]

    app_module = importlib.import_module("app")
    importlib.reload(app_module)

    app_module.pygame_available = False
    if hasattr(app_module.pygame, "mixer") and hasattr(app_module.pygame.mixer, "music"):
        try:
            app_module.pygame.mixer.music.get_busy  # type: ignore[attr-defined]
        except Exception:
            pass
        else:
            app_module.pygame.mixer.music.get_busy = lambda: False  # type: ignore[assignment]

    log_path = Path(app_module.app.config["LOG_VIEW_FILE"])
    log_path.parent.mkdir(parents=True, exist_ok=True)

    yield app_module

    if hasattr(app_module, "conn") and app_module.conn is not None:
        app_module.conn.close()
    if "app" in sys.modules:
        del sys.modules["app"]
    if repo_root_str in sys.path:
        sys.path.remove(repo_root_str)


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
    assert change_response.status_code == 200
    assert b"Passwort ge\xc3\xa4ndert" in change_response.data


def test_logs_endpoint_limits_response_size(client):
    client, app_module = client
    log_path = Path(app_module.app.config["LOG_VIEW_FILE"])
    lines = [f"line{i:04d}-{'x' * 20}" for i in range(200)]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    _login(client)

    response = client.get("/logs")
    assert response.status_code == 200

    html = response.get_data(as_text=True)

    assert "line0000-" not in html
    assert "line0149-" not in html
    assert "line0199-" in html
    assert "Ältere Einträge wurden abgeschnitten." in html
    assert "Keine Logdatei vorhanden" not in html

    match = re.search(r"<pre class=\"log-output\">(.*?)</pre>", html, re.S)
    assert match is not None
    pre_content = match.group(1)
    visible_lines = [line for line in pre_content.splitlines() if line.startswith("line")]

    max_lines = app_module.app.config["LOG_VIEW_MAX_LINES"]
    max_bytes = app_module.app.config["LOG_VIEW_MAX_BYTES"]

    assert len(visible_lines) <= max_lines
    visible_indices = [int(line[4:8]) for line in visible_lines]
    assert visible_indices, "Es sollten Logzeilen angezeigt werden"
    assert visible_indices[0] >= len(lines) - max_lines
    assert visible_lines[-1] == "line0199-" + "x" * 20
    assert len(pre_content.encode("utf-8")) <= max_bytes
