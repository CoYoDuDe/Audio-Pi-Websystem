import logging
import os

os.environ.setdefault("FLASK_SECRET_KEY", "testing-secret")
os.environ.setdefault("TESTING", "1")

import app
from flask import get_flashed_messages


def _raise_file_not_found(*args, **kwargs):
    raise FileNotFoundError("systemctl")


def test_setup_ap_missing_cli(monkeypatch, caplog):
    monkeypatch.setattr(app, "has_network", lambda: False)
    monkeypatch.setattr(app.subprocess, "call", _raise_file_not_found)

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
    monkeypatch.setattr(app.subprocess, "call", _raise_file_not_found)

    with caplog.at_level(logging.ERROR):
        with app.app.test_request_context("/"):
            assert app.disable_ap() is False
            flashed = get_flashed_messages()

    assert any(
        "systemctl-Aufruf fehlgeschlagen" in record.message
        for record in caplog.records
    )
    assert "systemctl nicht verfügbar oder Berechtigung verweigert" in flashed
