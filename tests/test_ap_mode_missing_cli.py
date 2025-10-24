import importlib
import logging
import os
import types

os.environ.setdefault("FLASK_SECRET_KEY", "testing-secret")
os.environ.setdefault("TESTING", "1")

import app
from flask import get_flashed_messages


def _raise_file_not_found(*args, **kwargs):
    raise FileNotFoundError("systemctl")


def test_setup_ap_missing_cli(monkeypatch, caplog):
    monkeypatch.setattr(app, "has_network", lambda: False)
    monkeypatch.setattr(app.subprocess, "run", _raise_file_not_found)

    with caplog.at_level(logging.ERROR):
        with app.app.test_request_context("/"):
            assert app.setup_ap() is False
            flashed = get_flashed_messages()

    assert any(
        "systemctl-Aufruf fehlgeschlagen" in record.message
        for record in caplog.records
    )
    assert "systemctl nicht verfügbar oder Berechtigung verweigert" in flashed


def test_disable_ap_missing_cli(monkeypatch, caplog):
    monkeypatch.setattr(app.subprocess, "run", _raise_file_not_found)

    with caplog.at_level(logging.ERROR):
        with app.app.test_request_context("/"):
            assert app.disable_ap() is False
            flashed = get_flashed_messages()

    assert any(
        "systemctl-Aufruf fehlgeschlagen" in record.message
        for record in caplog.records
    )
    assert "systemctl nicht verfügbar oder Berechtigung verweigert" in flashed


def test_setup_ap_missing_systemctl_with_sudo(monkeypatch, caplog):
    original_disable = os.environ.get("AUDIO_PI_DISABLE_SUDO")
    monkeypatch.setenv("AUDIO_PI_DISABLE_SUDO", "0")
    importlib.reload(app)

    original_run = app.subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if cmd[:2] == ["sudo", "systemctl"]:
            assert kwargs.get("check") is False
            assert kwargs.get("capture_output") is True
            assert kwargs.get("text") is True
            return types.SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="sudo: systemctl: command not found",
            )
        return original_run(cmd, *args, **kwargs)

    try:
        monkeypatch.setattr(app, "has_network", lambda: False)
        monkeypatch.setattr(app.subprocess, "run", fake_run)

        with caplog.at_level(logging.ERROR):
            with app.app.test_request_context("/"):
                assert app.setup_ap() is False
                flashed = get_flashed_messages()

        expected_message = (
            "systemctl ist nicht verfügbar. Bitte stellen Sie sicher, dass systemctl installiert ist."
        )
        assert expected_message in flashed
        assert any(
            expected_message in record.message for record in caplog.records
        )
    finally:
        if original_disable is None:
            os.environ.pop("AUDIO_PI_DISABLE_SUDO", None)
        else:
            os.environ["AUDIO_PI_DISABLE_SUDO"] = original_disable
        importlib.reload(app)
