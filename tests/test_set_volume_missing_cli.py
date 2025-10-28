import importlib
import sys
from pathlib import Path

import pytest

from tests.csrf_utils import csrf_post
from tests.test_volume_route import _login


def _load_app_module(tmp_path, monkeypatch, disable_sudo: str):
    monkeypatch.setenv("FLASK_SECRET_KEY", "testkey")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DB_FILE", str(tmp_path / "test.db"))
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "password")
    monkeypatch.setenv("AUDIO_PI_DISABLE_SUDO", disable_sudo)

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
def app_module(tmp_path, monkeypatch):
    yield from _load_app_module(tmp_path, monkeypatch, "1")


@pytest.fixture
def client(app_module):
    with app_module.app.test_client() as client:
        yield client, app_module


@pytest.fixture
def app_module_with_sudo(tmp_path, monkeypatch):
    yield from _load_app_module(tmp_path, monkeypatch, "0")


@pytest.fixture
def client_with_sudo(app_module_with_sudo):
    with app_module_with_sudo.app.test_client() as client:
        yield client, app_module_with_sudo


def test_volume_missing_pactl_warns(monkeypatch, client):
    client, app_module = client
    monkeypatch.setattr(app_module.pygame.mixer.music, "get_busy", lambda: False)
    _login(client)

    monkeypatch.setattr(app_module, "get_current_sink", lambda: "test-sink")
    monkeypatch.setattr(app_module.pygame.mixer.music, "set_volume", lambda value: None)

    commands = []

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd:
            if cmd[0] == "pactl":
                raise FileNotFoundError("pactl missing")
            commands.append(cmd)
        return app_module.subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    response = csrf_post(
        client,
        "/volume",
        data={"volume": "50"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert [
        ["amixer", "sset", "Master", "50%"],
        ["systemctl", "start", "audio-pi-alsactl.service"],
    ] == commands
    assert app_module._PACTL_MISSING_MESSAGE.encode("utf-8") in response.data
    assert b"Lautst\xc3\xa4rke persistent gesetzt" in response.data


def test_volume_missing_pactl_called_process_error(monkeypatch, client_with_sudo):
    client, app_module = client_with_sudo
    monkeypatch.setattr(app_module.pygame.mixer.music, "get_busy", lambda: False)
    monkeypatch.setattr(app_module.pygame.mixer.music, "set_volume", lambda value: None)

    _login(client)

    monkeypatch.setattr(app_module, "get_current_sink", lambda: "test-sink")

    executed_commands = []

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd:
            executed_commands.append(list(cmd))
            if cmd[0] == "pactl":
                raise app_module.subprocess.CalledProcessError(
                    returncode=1,
                    cmd=["sudo", *cmd],
                    output="",
                    stderr="missing pactl",
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
    assert app_module._PACTL_MISSING_MESSAGE.encode("utf-8") in response.data
    assert b"Kommando &#39;pactl&#39; fehlgeschlagen (Code 1)." in response.data


def test_audio_pi_alsactl_unit_uses_resolvable_execstart():
    unit_path = Path(__file__).resolve().parents[1] / "scripts/systemd/audio-pi-alsactl.service"
    contents = unit_path.read_text(encoding="utf-8").splitlines()
    exec_lines = [line for line in contents if line.startswith("ExecStart=")]
    assert exec_lines, "audio-pi-alsactl.service muss eine ExecStart-Zeile enthalten"
    exec_command = exec_lines[0].split("=", 1)[1].strip()
    assert exec_command, "ExecStart darf nicht leer sein"
    exec_tokens = exec_command.split()
    assert exec_tokens, "ExecStart muss ein auszuführendes Programm angeben"

    binary_path = Path(exec_tokens[0])
    assert binary_path.is_absolute(), "ExecStart muss einen absoluten Pfad verwenden"
    assert binary_path.exists(), f"{binary_path} muss im Testsystem vorhanden sein"
    if binary_path.name == "env":
        assert len(exec_tokens) >= 2 and exec_tokens[1] == "alsactl", "Bei Verwendung von /usr/bin/env muss 'alsactl' folgen"
    else:
        assert binary_path.name == "alsactl", "Direkter ExecStart muss das alsactl-Binary referenzieren"
