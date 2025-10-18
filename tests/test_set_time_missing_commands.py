import errno
import os

from tests.csrf_utils import csrf_post
from tests.test_set_time import _login, app_module, client  # noqa: F401


def test_set_time_handles_missing_command(monkeypatch, client):
    client, app_module = client
    _login(client)

    set_rtc_called = False

    def fake_set_rtc(dt):
        nonlocal set_rtc_called
        set_rtc_called = True

    def fake_run(cmd, *args, **kwargs):
        assert kwargs.get("check") is True
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), cmd[0])

    monkeypatch.setattr(app_module, "set_rtc", fake_set_rtc)
    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    monkeypatch.setattr(app_module.subprocess, "getoutput", lambda *args, **kwargs: "")

    response = csrf_post(
        client,
        "/set_time",
        data={"datetime": "2024-01-01T12:00:00"},
        follow_redirects=True,
    )

    expected_message = (
        "Kommando &#39;sudo&#39; wurde nicht gefunden. Systemzeit konnte nicht gesetzt werden."
    )
    assert expected_message.encode("utf-8") in response.data
    assert set_rtc_called is False
