import sys

import pytest

from .csrf_utils import csrf_post
from tests.test_playback_decode_failure import _setup_app


@pytest.fixture(autouse=True)
def clear_app_module():
    sys.modules.pop("app", None)
    yield
    sys.modules.pop("app", None)


class TrackingSegment:
    def __init__(self, collector):
        self._collector = collector

    def normalize(self, headroom):
        self._collector.append(headroom)
        return self

    def export(self, *_args, **_kwargs):
        return None


def _run_prepare_with_headroom(monkeypatch, tmp_path, headroom_env=None, stored_value=None):
    if headroom_env is not None:
        monkeypatch.setenv("NORMALIZATION_HEADROOM_DB", headroom_env)
    else:
        monkeypatch.delenv("NORMALIZATION_HEADROOM_DB", raising=False)

    app_module, _dummy_music = _setup_app(monkeypatch, tmp_path)

    if stored_value is None:
        app_module.set_setting(app_module.NORMALIZATION_HEADROOM_SETTING_KEY, None)
    else:
        app_module.set_setting(
            app_module.NORMALIZATION_HEADROOM_SETTING_KEY, str(stored_value)
        )

    collector = []
    monkeypatch.setattr(
        app_module.AudioSegment,
        "from_file",
        lambda *_args, **_kwargs: TrackingSegment(collector),
    )

    source_path = tmp_path / "source.mp3"
    source_path.write_bytes(b"data")
    target_path = tmp_path / "prepared.wav"

    assert app_module._prepare_audio_for_playback(
        str(source_path), str(target_path)
    )
    return collector[0], app_module


def test_prepare_audio_uses_default_headroom(monkeypatch, tmp_path):
    headroom, app_module = _run_prepare_with_headroom(monkeypatch, tmp_path)
    assert headroom == pytest.approx(app_module.DEFAULT_NORMALIZATION_HEADROOM_DB)


def test_prepare_audio_uses_stored_headroom(monkeypatch, tmp_path):
    expected = 1.5
    headroom, _ = _run_prepare_with_headroom(
        monkeypatch, tmp_path, stored_value=expected
    )
    assert headroom == pytest.approx(expected)


def test_prepare_audio_prefers_environment_headroom(monkeypatch, tmp_path):
    expected = 2.75
    headroom, _ = _run_prepare_with_headroom(
        monkeypatch, tmp_path, headroom_env=str(expected), stored_value=1.1
    )
    assert headroom == pytest.approx(expected)


def test_prepare_audio_sanitizes_negative_environment_headroom(monkeypatch, tmp_path):
    headroom, _ = _run_prepare_with_headroom(
        monkeypatch, tmp_path, headroom_env="-3"
    )
    assert headroom == pytest.approx(3.0)


def test_save_normalization_headroom_interprets_negative_target_level(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("NORMALIZATION_HEADROOM_DB", raising=False)
    app_module, _dummy_music = _setup_app(monkeypatch, tmp_path)

    client = app_module.app.test_client()
    with client:
        response = csrf_post(
            client,
            "/settings/normalization_headroom",
            data={"normalization_headroom_db": "-3"},
            follow_redirects=True,
        )
        assert response.status_code == 200

    stored_value = app_module.get_setting(app_module.NORMALIZATION_HEADROOM_SETTING_KEY)
    assert stored_value is not None
    assert float(stored_value) == pytest.approx(3.0)

    collector = []
    monkeypatch.setattr(
        app_module.AudioSegment,
        "from_file",
        lambda *_args, **_kwargs: TrackingSegment(collector),
    )

    source_path = tmp_path / "target.mp3"
    source_path.write_bytes(b"data")
    target_path = tmp_path / "prepared.wav"

    assert app_module._prepare_audio_for_playback(
        str(source_path), str(target_path)
    )
    assert collector[0] == pytest.approx(3.0)
