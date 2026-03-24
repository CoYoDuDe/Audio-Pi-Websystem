import importlib
import sys
from pathlib import Path


def _reload_app(monkeypatch, tmp_path, **env_vars):
    monkeypatch.setenv("FLASK_SECRET_KEY", "testkey")
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DB_FILE", str(tmp_path / "test.db"))
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "password")
    monkeypatch.setenv("AUDIO_PI_SUPPRESS_AUTOSTART", "1")

    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)

    repo_root = Path(__file__).resolve().parents[1]
    repo_path = str(repo_root)
    if repo_path not in sys.path:
        sys.path.insert(0, repo_path)

    sys.modules.pop("app", None)
    return importlib.import_module("app")


def test_audio_pi_dac_sink_environment_alias(monkeypatch, tmp_path):
    app_module = _reload_app(
        monkeypatch,
        tmp_path,
        AUDIO_PI_DAC_SINK="alsa_output.hifiberry.stereo-fallback",
    )

    assert app_module.DAC_SINK_HINT == "alsa_output.hifiberry.stereo-fallback"


def test_audio_pi_gpio_pin_environment_alias(monkeypatch, tmp_path):
    app_module = _reload_app(
        monkeypatch,
        tmp_path,
        AUDIO_PI_GPIO_PIN="27",
    )

    assert app_module.GPIO_PIN_ENDSTUFE == 27
