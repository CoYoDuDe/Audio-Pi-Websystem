import pytest
from subprocess import CalledProcessError, CompletedProcess

from tests.csrf_utils import csrf_post
from tests.test_wlan_connect import _login_admin, client as wlan_client_fixture


@pytest.fixture
def client(wlan_client_fixture):
    return wlan_client_fixture


@pytest.fixture
def sudo_env(monkeypatch):
    monkeypatch.setenv("AUDIO_PI_DISABLE_SUDO", "0")


def test_bluetooth_on_missing_cli(monkeypatch, client):
    flask_client, app_module = client
    _login_admin(flask_client)

    real_popen = app_module.subprocess.Popen

    def fake_run(args, **kwargs):
        if args[:2] == ["bluetoothctl", "power"]:
            raise FileNotFoundError("bluetoothctl not found")
        return CompletedProcess(args, 0, stdout="", stderr="")

    def fake_popen(args, *popen_args, **kwargs):
        if isinstance(args, (list, tuple)) and args[:1] == ["bluetoothctl"]:
            raise FileNotFoundError("bluetoothctl not found")
        return real_popen(args, *popen_args, **kwargs)

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    monkeypatch.setattr(app_module.subprocess, "Popen", fake_popen)

    response = csrf_post(flask_client, "/bluetooth_on", follow_redirects=False)

    assert response.status_code == 302

    with flask_client.session_transaction() as session:
        flashes = session.get("_flashes", [])

    assert flashes
    assert (
        flashes[-1][1]
        == "bluetoothctl nicht gefunden oder keine Berechtigung. Bitte Installation überprüfen."
    )

    # Sicherstellen, dass der Auto-Accept-Aufruf selbst keine Ausnahme wirft
    with app_module.app.test_request_context("/"):
        result = app_module.bluetooth_auto_accept()
        assert result == "missing_cli"


def test_bluetooth_on_missing_cli_sudo_enabled(monkeypatch, sudo_env, client):
    flask_client, app_module = client
    _login_admin(flask_client)

    def fake_run(args, **kwargs):
        if args[:2] == ["sudo", "bluetoothctl"] and args[2:4] == ["power", "on"]:
            raise CalledProcessError(
                1,
                args,
                output="sudo: bluetoothctl: command not found\n",
                stderr="sudo: bluetoothctl: command not found\n",
            )
        return CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    response = csrf_post(flask_client, "/bluetooth_on", follow_redirects=False)

    assert response.status_code == 302

    with flask_client.session_transaction() as session:
        flashes = session.get("_flashes", [])

    assert flashes
    assert (
        flashes[-1][1]
        == "bluetoothctl nicht gefunden oder keine Berechtigung. Bitte Installation überprüfen."
    )


def test_get_bluetooth_power_state_parses_powered_value(monkeypatch, client):
    _flask_client, app_module = client

    def fake_run(args, **kwargs):
        assert args == ["bluetoothctl", "show"]
        return CompletedProcess(args, 0, stdout="Controller 00:11:22\n\tPowered: yes\n", stderr="")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    assert app_module.get_bluetooth_power_state() is True


def test_toggle_bluetooth_disables_when_powered(monkeypatch, client):
    _flask_client, app_module = client
    calls = []

    monkeypatch.setattr(app_module, "get_bluetooth_power_state", lambda: True)
    monkeypatch.setattr(app_module, "enable_bluetooth", lambda: calls.append("on") or "success")
    monkeypatch.setattr(app_module, "disable_bluetooth", lambda: calls.append("off") or "success")

    assert app_module.toggle_bluetooth() == "success"
    assert calls == ["off"]


def test_toggle_bluetooth_enables_when_powered_off(monkeypatch, client):
    _flask_client, app_module = client
    calls = []

    monkeypatch.setattr(app_module, "get_bluetooth_power_state", lambda: False)
    monkeypatch.setattr(app_module, "enable_bluetooth", lambda: calls.append("on") or "success")
    monkeypatch.setattr(app_module, "disable_bluetooth", lambda: calls.append("off") or "success")

    assert app_module.toggle_bluetooth() == "success"
    assert calls == ["on"]


class _MissingCommandProcess:
    def __init__(self, stderr: str = "sudo: bluetoothctl: command not found"):
        self.returncode = None
        self._stderr = stderr

    def communicate(self, *_args, **_kwargs):
        self.returncode = 127
        return "", self._stderr


def test_bluetooth_auto_accept_missing_cli_sudo_enabled(
    monkeypatch, sudo_env, client
):
    flask_client, app_module = client
    _login_admin(flask_client)

    def fake_run(args, **kwargs):
        if args[:2] == ["sudo", "bluetoothctl"] and args[2:4] == ["power", "on"]:
            return CompletedProcess(args, 0, stdout="", stderr="")
        return CompletedProcess(args, 0, stdout="", stderr="")

    def fake_popen(args, *popen_args, **kwargs):
        if isinstance(args, (list, tuple)) and args[:2] == ["sudo", "bluetoothctl"]:
            return _MissingCommandProcess()
        raise AssertionError("Unerwartetes Kommando")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    monkeypatch.setattr(app_module.subprocess, "Popen", fake_popen)

    with app_module.app.test_request_context("/"):
        result = app_module.bluetooth_auto_accept()

    assert result == "missing_cli"

    response = csrf_post(flask_client, "/bluetooth_on", follow_redirects=False)

    assert response.status_code == 302

    with flask_client.session_transaction() as session:
        flashes = session.get("_flashes", [])

    assert flashes
    assert (
        flashes[-1][1]
        == "bluetoothctl nicht gefunden oder keine Berechtigung. Bitte Installation überprüfen."
    )
