import importlib
import logging
import sys
import types
from pathlib import Path

import pytest
from flask import get_flashed_messages


class DummyMusic:
    def set_volume(self, value):
        self.last_set_volume = value

    def get_volume(self):
        return 1.0

    def load(self, path):
        self.last_loaded = path

    def play(self):
        self.play_called = True

    def get_busy(self):
        return False


def _load_app_module(monkeypatch, tmp_path):
    sys.modules.pop("app", None)

    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "password")
    monkeypatch.setenv("DB_FILE", str(tmp_path / "test.db"))
    monkeypatch.setenv("TESTING", "1")

    dummy_music = DummyMusic()
    dummy_pygame = types.ModuleType("pygame")
    dummy_pygame.error = RuntimeError
    dummy_pygame.mixer = types.SimpleNamespace(
        music=dummy_music,
        init=lambda: None,
    )

    dummy_lgpio = types.ModuleType("lgpio")
    dummy_lgpio.error = RuntimeError
    dummy_lgpio.gpiochip_open = lambda *_args, **_kwargs: object()
    dummy_lgpio.gpio_write = lambda *_args, **_kwargs: None
    dummy_lgpio.gpio_free = lambda *_args, **_kwargs: None
    dummy_lgpio.gpio_claim_output = lambda *_args, **_kwargs: None

    dummy_smbus = types.ModuleType("smbus")

    monkeypatch.setitem(sys.modules, "pygame", dummy_pygame)
    monkeypatch.setitem(sys.modules, "lgpio", dummy_lgpio)
    monkeypatch.setitem(sys.modules, "smbus", dummy_smbus)

    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(repo_root))

    app_module = importlib.import_module("app")
    app_module.app.config["LOGIN_DISABLED"] = True
    app_module.app.config["UPLOAD_FOLDER"] = str(tmp_path / "uploads")
    app_module.app.config["WTF_CSRF_ENABLED"] = False

    return app_module


@pytest.fixture
def app_module(monkeypatch, tmp_path):
    app_module = _load_app_module(monkeypatch, tmp_path)

    yield app_module

    sys.modules.pop("app", None)


def _prepare_status_dependencies(monkeypatch, app_module):
    monkeypatch.setattr(app_module, "_run_pactl_command", lambda *args: None)
    monkeypatch.setattr(app_module, "get_current_sink", lambda: "Nicht verfügbar")
    monkeypatch.setattr(app_module.pygame.mixer.music, "get_busy", lambda: False)
    monkeypatch.setattr(app_module, "is_bt_connected", lambda: False)
    monkeypatch.setattr(
        app_module,
        "get_normalization_headroom_details",
        lambda: {
            "value": 0,
            "env_raw": None,
            "source": "default",
            "stored_raw": None,
            "stored_value": None,
        },
    )
    monkeypatch.setattr(
        app_module,
        "get_schedule_default_volume_details",
        lambda: {
            "percent": None,
            "source": "default",
            "raw_percent": None,
            "raw_db": None,
            "db_value": None,
        },
    )


def test_gather_status_missing_iwgetid(monkeypatch, app_module, caplog):
    _prepare_status_dependencies(monkeypatch, app_module)

    def fake_run(args, **kwargs):
        if args and args[0] == "iwgetid":
            raise FileNotFoundError("iwgetid")
        return app_module.subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    with caplog.at_level(logging.ERROR):
        status = app_module.gather_status()

    assert status["wlan_status"] == "Nicht verfügbar (iwgetid fehlt)"
    assert any("iwgetid" in record.message for record in caplog.records)


def test_gather_status_iwgetid_failure_exit_code(monkeypatch, app_module, caplog):
    _prepare_status_dependencies(monkeypatch, app_module)

    def fake_run(args, **kwargs):
        if args and args[0] == "iwgetid":
            return app_module.subprocess.CompletedProcess(
                args,
                5,
                stdout="", 
                stderr="command failed",
            )
        return app_module.subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    with caplog.at_level(logging.ERROR):
        status = app_module.gather_status()

    assert status["wlan_status"] == "Nicht verfügbar (iwgetid fehlt)"
    assert any("iwgetid" in record.message and "Exit-Code" in record.message for record in caplog.records)


def test_wlan_scan_missing_wpa_cli(monkeypatch, app_module, caplog):
    def fake_run(args, **kwargs):
        if args and "wpa_cli" in args:
            raise FileNotFoundError("wpa_cli")
        return app_module.subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    with caplog.at_level(logging.ERROR):
        with app_module.app.test_request_context("/wlan_scan", method="POST"):
            response = app_module.wlan_scan()
            flashes = get_flashed_messages()

    expected_message = "Scan nicht möglich, wpa_cli fehlt oder meldet einen Fehler"
    assert expected_message in response
    assert flashes == [expected_message]
    assert any("wpa_cli" in record.message for record in caplog.records)


def test_wlan_scan_wpa_cli_failure_exit_code(monkeypatch, app_module, caplog):
    def fake_run(args, **kwargs):
        if args and "wpa_cli" in args and args[-1] == "scan":
            return app_module.subprocess.CompletedProcess(
                args,
                7,
                stdout="",
                stderr="permission denied",
            )
        return app_module.subprocess.CompletedProcess(args, 0, stdout="OK", stderr="")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    with caplog.at_level(logging.ERROR):
        with app_module.app.test_request_context("/wlan_scan", method="POST"):
            response = app_module.wlan_scan()
            flashes = get_flashed_messages()

    expected_message = "Scan nicht möglich, wpa_cli fehlt oder meldet einen Fehler"
    assert expected_message in response
    assert flashes == [expected_message]
    assert any(
        "wpa_cli" in record.message and "Exit-Code" in record.message
        for record in caplog.records
    )


def test_wlan_scan_wpa_cli_fail_output(monkeypatch, app_module, caplog):
    def fake_run(args, **kwargs):
        if args and "wpa_cli" in args and args[-1] == "scan":
            return app_module.subprocess.CompletedProcess(args, 0, stdout="OK", stderr="")
        if args and "wpa_cli" in args and args[-1] == "scan_results":
            return app_module.subprocess.CompletedProcess(
                args,
                0,
                stdout="FAIL-BUSY",
                stderr="",
            )
        return app_module.subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    with caplog.at_level(logging.ERROR):
        with app_module.app.test_request_context("/wlan_scan", method="POST"):
            response = app_module.wlan_scan()
            flashes = get_flashed_messages()

    expected_message = "Scan nicht möglich, wpa_cli fehlt oder meldet einen Fehler"
    assert expected_message in response
    assert flashes == [expected_message]
    assert any("FAIL" in record.message for record in caplog.records)


def test_wlan_scan_get_not_allowed(app_module):
    client = app_module.app.test_client()

    response = client.get("/wlan_scan")

    assert response.status_code == 405


def test_wlan_scan_post_success(monkeypatch, app_module):
    call_log = {"wpa_cli": [], "wifi_tool": None}

    def fake_run_wpa_cli(args, expect_ok=True, **kwargs):
        call_log["wpa_cli"].append(list(args))
        return "OK"

    def fake_run_wifi_tool(args, fallback_message, log_context, *, flash_on_error=False):
        call_log["wifi_tool"] = {
            "args": list(args),
            "fallback": fallback_message,
            "context": log_context,
            "flash_on_error": flash_on_error,
        }
        return True, (
            "Gefundene Netzwerke: 1\n"
            "SSID: Testnetz\n"
            "  Signal: -40 dBm @ 2412 MHz\n"
            "  Flags: [WPA2]\n"
            "  BSSID: 00:11:22:33:44:55"
        )

    monkeypatch.setattr(app_module, "_run_wpa_cli", fake_run_wpa_cli)
    monkeypatch.setattr(app_module, "_run_wifi_tool", fake_run_wifi_tool)

    client = app_module.app.test_client()
    response = client.post("/wlan_scan")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Gefundene Netzwerke: 1" in body
    assert call_log["wpa_cli"]
    assert call_log["wpa_cli"][0][-1] == "scan"
    assert call_log["wifi_tool"] is not None
    assert call_log["wifi_tool"]["args"][-1] == "scan_results"


def test_run_wifi_tool_logs_command_not_found(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("AUDIO_PI_DISABLE_SUDO", "0")
    app_module = _load_app_module(monkeypatch, tmp_path)

    try:
        def fake_run(args, **kwargs):
            return app_module.subprocess.CompletedProcess(
                args,
                1,
                stdout="",
                stderr="sudo: wpa_cli: command not found",
            )

        monkeypatch.setattr(app_module.subprocess, "run", fake_run)

        fallback_message = "Scan nicht möglich, wpa_cli fehlt oder meldet einen Fehler"

        with app_module.app.test_request_context("/wlan_scan", method="POST"):
            with caplog.at_level(logging.ERROR):
                success, output = app_module._run_wifi_tool(
                    ["sudo", "wpa_cli", "scan"],
                    fallback_message,
                    "wpa_cli Test",
                    flash_on_error=True,
                )
                flashes = get_flashed_messages()

        assert not success
        assert output == fallback_message
        assert flashes == [fallback_message]
        assert any(
            "Kommando 'wpa_cli' nicht gefunden" in record.message
            for record in caplog.records
        )
    finally:
        sys.modules.pop("app", None)
