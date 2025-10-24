import importlib
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

    repo_root = Path(__file__).resolve().parents[1]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)
    if "app" in sys.modules:
        del sys.modules["app"]
    app_module = importlib.import_module("app")
    importlib.reload(app_module)

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    app_module.app.config["UPLOAD_FOLDER"] = str(upload_dir)

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


@pytest.fixture
def app_module_sudo_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "testkey")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DB_FILE", str(tmp_path / "test.db"))
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "password")
    monkeypatch.setenv("AUDIO_PI_DISABLE_SUDO", "0")

    repo_root = Path(__file__).resolve().parents[1]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)
    if "app" in sys.modules:
        del sys.modules["app"]
    app_module = importlib.import_module("app")
    importlib.reload(app_module)

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    app_module.app.config["UPLOAD_FOLDER"] = str(upload_dir)

    yield app_module

    if hasattr(app_module, "conn") and app_module.conn is not None:
        app_module.conn.close()
    if "app" in sys.modules:
        del sys.modules["app"]
    if repo_root_str in sys.path:
        sys.path.remove(repo_root_str)


@pytest.fixture
def client_sudo_enabled(app_module_sudo_enabled):
    with app_module_sudo_enabled.app.test_client() as client:
        yield client, app_module_sudo_enabled


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
    return change_response


def test_volume_command_failure(monkeypatch, client):
    client, app_module = client
    monkeypatch.setattr(app_module.pygame.mixer.music, "get_busy", lambda: False)
    _login(client)

    monkeypatch.setattr(app_module, "get_current_sink", lambda: "test-sink")
    monkeypatch.setattr(app_module.pygame.mixer.music, "set_volume", lambda value: None)

    commands = []

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list):
            commands.append(cmd)
            if cmd and cmd[0] == "pactl":
                raise app_module.subprocess.CalledProcessError(
                    returncode=1,
                    cmd=cmd,
                    output="error",
                    stderr="fail",
                )
            return app_module.subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return app_module.subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    response = csrf_post(
        client,
        "/volume",
        data={"volume": "50"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Kommando &#39;pactl&#39; fehlgeschlagen (Code 1)." in response.data
    assert b"Lautst\xc3\xa4rke persistent gesetzt" in response.data
    command_tuples = [tuple(cmd) if isinstance(cmd, list) else tuple(cmd) for cmd in commands]
    assert (
        ("pactl", "set-sink-volume", "test-sink", "50%") in command_tuples
        and ("amixer", "sset", "Master", "50%") in command_tuples
        and ("systemctl", "start", "audio-pi-alsactl.service") in command_tuples
    )


def test_volume_failure_when_audio_commands_fail(monkeypatch, client):
    client, app_module = client
    monkeypatch.setattr(app_module.pygame.mixer.music, "get_busy", lambda: False)
    _login(client)

    monkeypatch.setattr(app_module, "get_current_sink", lambda: "test-sink")
    monkeypatch.setattr(app_module.pygame.mixer.music, "set_volume", lambda value: None)

    commands = []

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list):
            commands.append(cmd)
            if cmd and cmd[0] in {"pactl", "amixer"}:
                raise app_module.subprocess.CalledProcessError(
                    returncode=1,
                    cmd=cmd,
                    output="error",
                    stderr="fail",
                )
            return app_module.subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return app_module.subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    response = csrf_post(
        client,
        "/volume",
        data={"volume": "60"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Kommando &#39;pactl&#39; fehlgeschlagen (Code 1)." in response.data
    assert b"Kommando &#39;amixer&#39; fehlgeschlagen (Code 1)." in response.data
    assert b"Lautst\xc3\xa4rke persistent gesetzt" not in response.data
    assert b"Lautst\xc3\xa4rke konnte nicht gesetzt werden" in response.data
    command_tuples = [tuple(cmd) if isinstance(cmd, list) else tuple(cmd) for cmd in commands]
    assert (
        ("pactl", "set-sink-volume", "test-sink", "60%") in command_tuples
        and ("amixer", "sset", "Master", "60%") in command_tuples
        and ("systemctl", "start", "audio-pi-alsactl.service") in command_tuples
    )


def test_volume_persistence_failure(monkeypatch, client):
    client, app_module = client
    monkeypatch.setattr(app_module.pygame.mixer.music, "get_busy", lambda: False)
    _login(client)

    monkeypatch.setattr(app_module, "get_current_sink", lambda: "test-sink")
    monkeypatch.setattr(app_module.pygame.mixer.music, "set_volume", lambda value: None)

    commands = []

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list):
            commands.append(cmd)
            if cmd and cmd[0] == "systemctl":
                raise app_module.subprocess.CalledProcessError(
                    returncode=1,
                    cmd=cmd,
                    output="error",
                    stderr="fail",
                )
        return app_module.subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    response = csrf_post(
        client,
        "/volume",
        data={"volume": "55"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert (
        b"Lautst\xc3\xa4rke gesetzt, konnte aber nicht persistent gespeichert werden"
        in response.data
    )
    assert b"Lautst\xc3\xa4rke persistent gesetzt" not in response.data
    command_tuples = [tuple(cmd) if isinstance(cmd, list) else tuple(cmd) for cmd in commands]
    assert (
        ("pactl", "set-sink-volume", "test-sink", "55%") in command_tuples
        and ("amixer", "sset", "Master", "55%") in command_tuples
        and ("systemctl", "start", "audio-pi-alsactl.service") in command_tuples
    )


def test_volume_uses_default_sink(monkeypatch, client):
    client, app_module = client
    monkeypatch.setattr(app_module.pygame.mixer.music, "get_busy", lambda: False)
    _login(client)

    monkeypatch.setattr(app_module, "get_current_sink", lambda: " ")
    monkeypatch.setattr(app_module.pygame.mixer.music, "set_volume", lambda value: None)

    commands = []

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list):
            commands.append(cmd)
        return app_module.subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    response = csrf_post(
        client,
        "/volume",
        data={"volume": "30"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    command_tuples = [tuple(cmd) for cmd in commands]
    pactl_set_volume = [
        cmd for cmd in command_tuples if cmd and cmd[0] == "pactl" and cmd[1] == "set-sink-volume"
    ]
    if pactl_set_volume:
        assert all(cmd[2] == "@DEFAULT_SINK@" for cmd in pactl_set_volume)
    assert ("amixer", "sset", "Master", "30%") in command_tuples
    assert ("systemctl", "start", "audio-pi-alsactl.service") in command_tuples


def test_volume_runs_without_pygame(monkeypatch, client):
    client, app_module = client
    monkeypatch.setattr(app_module.pygame.mixer.music, "get_busy", lambda: False)
    _login(client)

    app_module.pygame_available = False

    def failing_set_volume(_value):
        raise AssertionError("pygame.set_volume darf bei deaktiviertem pygame nicht aufgerufen werden")

    monkeypatch.setattr(app_module.pygame.mixer.music, "set_volume", failing_set_volume)
    monkeypatch.setattr(app_module, "get_current_sink", lambda: "test-sink")

    commands = []

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list):
            commands.append(cmd)
        return app_module.subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    response = csrf_post(
        client,
        "/volume",
        data={"volume": "70"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert (
        b"pygame nicht verf\xc3\xbcgbar, setze ausschlie\xc3\x9flich die Systemlautst\xc3\xa4rke."
        in response.data
    )
    assert b"Lautst\xc3\xa4rke persistent gesetzt" in response.data
    command_tuples = [tuple(cmd) for cmd in commands]
    assert ("pactl", "set-sink-volume", "test-sink", "70%") in command_tuples
    assert ("amixer", "sset", "Master", "70%") in command_tuples
    assert ("systemctl", "start", "audio-pi-alsactl.service") in command_tuples


def test_volume_persistent_command_uses_alsactl_when_sudo_enabled(monkeypatch, client_sudo_enabled):
    client, app_module = client_sudo_enabled
    monkeypatch.setattr(app_module.pygame.mixer.music, "get_busy", lambda: False)
    _login(client)

    monkeypatch.setattr(app_module, "get_current_sink", lambda: "test-sink")
    monkeypatch.setattr(app_module.pygame.mixer.music, "set_volume", lambda value: None)

    commands = []

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list):
            commands.append(cmd)
        return app_module.subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    response = csrf_post(
        client,
        "/volume",
        data={"volume": "40"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    command_tuples = [tuple(cmd) for cmd in commands]
    assert ("pactl", "set-sink-volume", "test-sink", "40%") in command_tuples
    assert ("amixer", "sset", "Master", "40%") in command_tuples
    assert ("sudo", "alsactl", "store") in command_tuples
    assert ("systemctl", "start", "audio-pi-alsactl.service") not in command_tuples
