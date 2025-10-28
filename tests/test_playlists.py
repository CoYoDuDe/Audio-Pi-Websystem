import importlib
import sys
from pathlib import Path

import pytest

from tests.csrf_utils import csrf_post


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "testkey")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DB_FILE", str(tmp_path / "test.db"))
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "password")

    repo_root = Path(__file__).resolve().parents[1]
    sys_path_entry = str(repo_root)
    added_path = False
    if sys_path_entry not in sys.path:
        sys.path.insert(0, sys_path_entry)
        added_path = True

    if "app" in sys.modules:
        del sys.modules["app"]
    app_module = importlib.import_module("app")
    importlib.reload(app_module)
    app_module.pygame.mixer.music.get_busy = lambda: False

    try:
        with app_module.app.test_client() as test_client:
            yield test_client, app_module
    finally:
        if getattr(app_module, "conn", None) is not None:
            app_module.conn.close()
        if added_path and sys_path_entry in sys.path:
            sys.path.remove(sys_path_entry)


def _login(client, app_module):
    login_data = {"username": "admin", "password": "password"}
    response = csrf_post(
        client,
        "/login",
        data=login_data,
        follow_redirects=True,
        source_url="/login",
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


def _get_playlist_names(app_module):
    with app_module.get_db_connection() as (conn, cursor):
        cursor.execute("SELECT name FROM playlists ORDER BY id")
        rows = cursor.fetchall()
    return [row["name"] for row in rows]


def test_create_playlist_trims_name(client):
    test_client, app_module = client
    _login(test_client, app_module)

    response = csrf_post(
        test_client,
        "/create_playlist",
        data={"name": "  Chill Mix  "},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Playlist erstellt" in response.get_data(as_text=True)
    assert _get_playlist_names(app_module) == ["Chill Mix"]


def test_create_playlist_rejects_empty_name(client):
    test_client, app_module = client
    _login(test_client, app_module)

    response = csrf_post(
        test_client,
        "/create_playlist",
        data={"name": "   "},
        follow_redirects=True,
    )

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Playlist-Name darf nicht leer sein" in body
    assert _get_playlist_names(app_module) == []


def test_create_playlist_rejects_too_long_name(client):
    test_client, app_module = client
    _login(test_client, app_module)

    too_long_name = "L" * 101
    response = csrf_post(
        test_client,
        "/create_playlist",
        data={"name": too_long_name},
        follow_redirects=True,
    )

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Playlist-Name darf maximal 100 Zeichen lang sein" in body
    assert _get_playlist_names(app_module) == []
