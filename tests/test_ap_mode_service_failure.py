import logging
import os

os.environ.setdefault("FLASK_SECRET_KEY", "testing-secret")
os.environ.setdefault("TESTING", "1")

import app
from flask import get_flashed_messages


def test_setup_ap_logs_warning_on_service_failure(monkeypatch, caplog):
    monkeypatch.setattr(app, "has_network", lambda: False)
    monkeypatch.setattr(app.subprocess, "call", lambda *_args, **_kwargs: 1)

    with caplog.at_level(logging.WARNING):
        with app.app.test_request_context("/"):
            result = app.setup_ap()
            flashed = get_flashed_messages()

    assert result is False
    assert any(
        "systemctl start dnsmasq endete mit Exit-Code 1" in record.message
        for record in caplog.records
    )
    assert any(
        "Warnung: systemctl start dnsmasq endete mit Exit-Code 1" in message
        for message in flashed
    )


def test_disable_ap_logs_warning_on_service_failure(monkeypatch, caplog):
    monkeypatch.setattr(app.subprocess, "call", lambda *_args, **_kwargs: 1)

    with caplog.at_level(logging.WARNING):
        with app.app.test_request_context("/"):
            result = app.disable_ap()
            flashed = get_flashed_messages()

    assert result is False
    assert any(
        "systemctl stop hostapd endete mit Exit-Code 1" in record.message
        for record in caplog.records
    )
    assert any(
        "Warnung: systemctl stop hostapd endete mit Exit-Code 1" in message
        for message in flashed
    )


def test_setup_ap_propagates_disable_failure(monkeypatch):
    monkeypatch.setattr(app, "has_network", lambda: True)

    called = {"count": 0}

    def fake_disable_ap():
        called["count"] += 1
        return False

    monkeypatch.setattr(app, "disable_ap", fake_disable_ap)

    assert app.setup_ap() is False
    assert called["count"] == 1
