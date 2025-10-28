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

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    app_module.UPLOAD_FOLDER = str(upload_dir)
    app_module.app.config["UPLOAD_FOLDER"] = str(upload_dir)

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


def _insert_playlist(app_module, name="Test Playlist"):
    with app_module.get_db_connection() as (conn, cursor):
        cursor.execute("INSERT INTO playlists (name) VALUES (?)", (name,))
        playlist_id = cursor.lastrowid
        conn.commit()
    return playlist_id


def _insert_audio_file(app_module, filename="track.mp3", duration=120):
    with app_module.get_db_connection() as (conn, cursor):
        cursor.execute(
            "INSERT INTO audio_files (filename, duration_seconds) VALUES (?, ?)",
            (filename, duration),
        )
        file_id = cursor.lastrowid
        conn.commit()
    return file_id


def _get_playlist_files(app_module):
    with app_module.get_db_connection() as (conn, cursor):
        cursor.execute(
            "SELECT playlist_id, file_id FROM playlist_files ORDER BY rowid"
        )
        rows = cursor.fetchall()
    return [(row["playlist_id"], row["file_id"]) for row in rows]


def _get_playlist_entries_with_position(app_module):
    with app_module.get_db_connection() as (conn, cursor):
        cursor.execute(
            """
            SELECT playlist_id, file_id, position
            FROM playlist_files
            ORDER BY playlist_id, position, rowid
            """
        )
        rows = cursor.fetchall()
    return [
        (row["playlist_id"], row["file_id"], row["position"])
        for row in rows
    ]


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


def test_add_to_playlist_rejects_non_integer_ids(client):
    test_client, app_module = client
    _login(test_client, app_module)

    playlist_id = _insert_playlist(app_module)
    file_id = _insert_audio_file(app_module)

    response = csrf_post(
        test_client,
        "/add_to_playlist",
        data={"playlist_id": "abc", "file_id": str(file_id)},
        follow_redirects=True,
    )

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Ungültige Playlist- oder Datei-ID." in body
    assert _get_playlist_files(app_module) == []


def test_add_to_playlist_requires_existing_playlist(client):
    test_client, app_module = client
    _login(test_client, app_module)

    file_id = _insert_audio_file(app_module)

    response = csrf_post(
        test_client,
        "/add_to_playlist",
        data={"playlist_id": "999", "file_id": str(file_id)},
        follow_redirects=True,
    )

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Playlist wurde nicht gefunden." in body
    assert _get_playlist_files(app_module) == []


def test_add_to_playlist_requires_existing_file(client):
    test_client, app_module = client
    _login(test_client, app_module)

    playlist_id = _insert_playlist(app_module)

    response = csrf_post(
        test_client,
        "/add_to_playlist",
        data={"playlist_id": str(playlist_id), "file_id": "999"},
        follow_redirects=True,
    )

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Audiodatei wurde nicht gefunden." in body
    assert _get_playlist_files(app_module) == []


def test_add_to_playlist_happy_path(client):
    test_client, app_module = client
    _login(test_client, app_module)

    playlist_id = _insert_playlist(app_module)
    file_id = _insert_audio_file(app_module)

    response = csrf_post(
        test_client,
        "/add_to_playlist",
        data={"playlist_id": str(playlist_id), "file_id": str(file_id)},
        follow_redirects=True,
    )

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Datei zur Playlist hinzugefügt" in body
    assert _get_playlist_files(app_module) == [(playlist_id, file_id)]


def test_add_to_playlist_rejects_duplicates(client):
    test_client, app_module = client
    _login(test_client, app_module)

    playlist_id = _insert_playlist(app_module)
    file_id = _insert_audio_file(app_module)

    first_response = csrf_post(
        test_client,
        "/add_to_playlist",
        data={"playlist_id": str(playlist_id), "file_id": str(file_id)},
        follow_redirects=True,
    )
    assert first_response.status_code == 200

    duplicate_response = csrf_post(
        test_client,
        "/add_to_playlist",
        data={"playlist_id": str(playlist_id), "file_id": str(file_id)},
        follow_redirects=True,
    )

    duplicate_body = duplicate_response.get_data(as_text=True)
    assert duplicate_response.status_code == 200
    assert "Diese Datei ist bereits in der Playlist vorhanden." in duplicate_body
    assert _get_playlist_files(app_module) == [(playlist_id, file_id)]


def test_add_to_playlist_assigns_incremental_positions(client):
    test_client, app_module = client
    _login(test_client, app_module)

    playlist_id = _insert_playlist(app_module)
    first_file = _insert_audio_file(app_module, filename="first_track.mp3")
    second_file = _insert_audio_file(app_module, filename="second_track.mp3")

    for file_id in (first_file, second_file):
        response = csrf_post(
            test_client,
            "/add_to_playlist",
            data={"playlist_id": str(playlist_id), "file_id": str(file_id)},
            follow_redirects=True,
        )
        assert response.status_code == 200

    entries = _get_playlist_entries_with_position(app_module)
    assert entries == [
        (playlist_id, first_file, 0),
        (playlist_id, second_file, 1),
    ]


def test_play_item_respects_playlist_positions(client, monkeypatch):
    test_client, app_module = client
    _login(test_client, app_module)

    playlist_id = _insert_playlist(app_module)
    first_file = _insert_audio_file(app_module, filename="b_title.mp3")
    second_file = _insert_audio_file(app_module, filename="a_title.mp3")

    for file_id in (first_file, second_file):
        response = csrf_post(
            test_client,
            "/add_to_playlist",
            data={"playlist_id": str(playlist_id), "file_id": str(file_id)},
            follow_redirects=True,
        )
        assert response.status_code == 200

    upload_dir = Path(app_module.app.config["UPLOAD_FOLDER"])
    upload_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("b_title.mp3", "a_title.mp3"):
        (upload_dir / filename).write_bytes(b"test")

    processed_files = []

    def fake_prepare(file_path, temp_path):
        processed_files.append(Path(file_path).name)
        Path(temp_path).write_bytes(b"0")
        return True

    playback_state = {"busy": False}

    def fake_play():
        playback_state["busy"] = True

    def fake_get_busy():
        if playback_state["busy"]:
            playback_state["busy"] = False
            return True
        return False

    monkeypatch.setattr(app_module, "_prepare_audio_for_playback", fake_prepare)
    monkeypatch.setattr(app_module.pygame.mixer.music, "load", lambda _: None)
    monkeypatch.setattr(app_module.pygame.mixer.music, "play", lambda: fake_play())
    monkeypatch.setattr(app_module.pygame.mixer.music, "get_busy", fake_get_busy)
    monkeypatch.setattr(app_module, "set_sink", lambda sink: True)
    monkeypatch.setattr(app_module, "activate_amplifier", lambda: None)
    monkeypatch.setattr(app_module, "deactivate_amplifier", lambda: None)
    monkeypatch.setattr(app_module, "is_bt_connected", lambda: False)
    monkeypatch.setattr(app_module, "resume_bt_audio", lambda: None)
    monkeypatch.setattr(app_module, "load_loopback", lambda: None)
    monkeypatch.setattr(app_module.time, "sleep", lambda *_, **__: None)

    play_result = app_module.play_item(playlist_id, "playlist", delay=0, is_schedule=False)

    assert play_result is True
    assert processed_files == ["b_title.mp3", "a_title.mp3"]
