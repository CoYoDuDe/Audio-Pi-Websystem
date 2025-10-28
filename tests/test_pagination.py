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


def _seed_files(app_module, count):
    with app_module.get_db_connection() as (conn, cursor):
        for index in range(count):
            cursor.execute(
                "INSERT INTO audio_files (filename, duration_seconds) VALUES (?, ?)",
                (f"track_{index:02d}.mp3", 60.0 + index),
            )
        conn.commit()
        cursor.execute("SELECT id FROM audio_files ORDER BY id")
        return [row["id"] for row in cursor.fetchall()]


def _create_playlist(app_module, name, file_ids):
    with app_module.get_db_connection() as (conn, cursor):
        cursor.execute("INSERT INTO playlists (name) VALUES (?)", (name,))
        playlist_id = cursor.lastrowid
        for file_id in file_ids:
            cursor.execute(
                "INSERT INTO playlist_files (playlist_id, file_id) VALUES (?, ?)",
                (playlist_id, file_id),
            )
        conn.commit()
        return playlist_id


def _seed_schedules(app_module, count, file_ids):
    if not file_ids:
        raise ValueError("Es werden Audiodateien zur Erstellung von Zeitplänen benötigt")
    with app_module.get_db_connection() as (conn, cursor):
        for index in range(count):
            file_id = file_ids[index % len(file_ids)]
            timestamp = f"2024-01-01T08:{index:02d}"
            cursor.execute(
                """
                INSERT INTO schedules (
                    item_id,
                    item_type,
                    time,
                    repeat,
                    delay,
                    start_date,
                    end_date,
                    day_of_month,
                    executed,
                    volume_percent
                ) VALUES (?, 'file', ?, 'once', ?, NULL, NULL, NULL, 0, 100)
                """,
                (file_id, timestamp, 0),
            )
        conn.commit()


def test_index_default_pagination(client):
    test_client, app_module = client
    _login(test_client, app_module)
    _seed_files(app_module, 15)

    response = test_client.get("/")
    assert response.status_code == 200
    content = response.get_data(as_text=True)
    assert content.count('class="file-entry"') == 10
    assert "Seite 1 von 2" in content
    assert "15 Dateien gesamt" in content


def test_index_files_page_size_all(client):
    test_client, app_module = client
    _login(test_client, app_module)
    _seed_files(app_module, 12)

    response = test_client.get("/?file_page_size=all")
    assert response.status_code == 200
    content = response.get_data(as_text=True)
    assert content.count('class="file-entry"') == 12
    assert "Seite 1 von 1" in content
    assert "12 Dateien gesamt" in content


def test_schedule_pagination_navigation(client):
    test_client, app_module = client
    _login(test_client, app_module)
    file_ids = _seed_files(app_module, 3)
    _seed_schedules(app_module, 12, file_ids)

    response = test_client.get("/?schedule_page=2")
    assert response.status_code == 200
    content = response.get_data(as_text=True)
    assert content.count('class="schedule-entry"') == 2
    assert "Seite 2 von 2" in content
    assert "12 Zeitpläne gesamt" in content
    assert content.count('data-schedule-id="') == 2


def test_schedule_form_has_server_rendered_options(client):
    test_client, app_module = client
    _login(test_client, app_module)
    file_ids = _seed_files(app_module, 1)
    playlist_id = _create_playlist(app_module, "Frühstück", file_ids)

    response = test_client.get("/")
    assert response.status_code == 200
    content = response.get_data(as_text=True)

    file_option_snippet = f'<option value="{file_ids[0]}" data-item-type="file"'
    playlist_option_snippet = f'<option value="{playlist_id}" data-item-type="playlist"'

    assert 'id="item-select"' in content
    assert file_option_snippet in content
    assert playlist_option_snippet in content
    assert f'{file_option_snippet} selected' in content

