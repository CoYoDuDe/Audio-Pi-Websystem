import importlib
import sys
from pathlib import Path

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "testkey")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DB_FILE", str(tmp_path / "test.db"))
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "password")
    monkeypatch.setenv("AUDIO_PI_DISABLE_SUDO", "1")

    repo_root = Path(__file__).resolve().parents[1]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)

    if "app" in sys.modules:
        del sys.modules["app"]

    app_module = importlib.import_module("app")
    importlib.reload(app_module)

    if hasattr(app_module.pygame, "mixer") and hasattr(app_module.pygame.mixer, "music"):
        try:
            app_module.pygame.mixer.music.get_busy  # type: ignore[attr-defined]
        except Exception:
            pass
        else:
            app_module.pygame.mixer.music.get_busy = lambda: False  # type: ignore[assignment]

    with app_module.app.test_client() as test_client:
        yield test_client, app_module

    if hasattr(app_module, "conn") and app_module.conn is not None:
        app_module.conn.close()

    if "app" in sys.modules:
        del sys.modules["app"]

    if repo_root_str in sys.path:
        sys.path.remove(repo_root_str)


def test_login_page_renders_base_layout(client):
    test_client, _ = client

    response = test_client.get("/login")
    assert response.status_code == 200

    html = response.get_data(as_text=True)

    assert '<link rel="stylesheet" href="/static/vendor/simple.min.css"' in html
    assert '<link rel="stylesheet" href="/static/styles.css"' in html
    assert "Zum Login" not in html
