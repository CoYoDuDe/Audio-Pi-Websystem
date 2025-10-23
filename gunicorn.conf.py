"""Gunicorn-Konfiguration für das Audio-Pi-Websystem.

Die Werte lassen sich über Umgebungsvariablen anpassen. Standardmäßig wird
der Port aus ``FLASK_PORT`` gelesen, damit bestehende Installationen ohne
zusätzliche Anpassungen weiterhin den gleichen Listen-Port nutzen.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import sys
from pathlib import Path
from typing import Callable

# Verhindert, dass app.py während des Preloads eigenständig Hintergrunddienste startet.
os.environ.setdefault("AUDIO_PI_SUPPRESS_AUTOSTART", "1")


def _read_int_from_env(name: str, default: int, *, minimum: int | None = None) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        logging.getLogger(__name__).warning(
            "Ungültiger numerischer Wert für %s='%s'. Fallback auf %s.",
            name,
            value,
            default,
        )
        return default
    if minimum is not None and parsed < minimum:
        logging.getLogger(__name__).warning(
            "Wert %s=%s unterschreitet Mindestwert %s. Fallback auf %s.",
            name,
            parsed,
            minimum,
            max(minimum, default),
        )
        return max(minimum, default)
    return parsed


def _configure_workers(cpu_count_func: Callable[[], int]) -> int:
    configured = _read_int_from_env("AUDIO_PI_GUNICORN_WORKERS", -1)
    if configured > 0:
        return configured

    try:
        cpu_count = cpu_count_func()
    except NotImplementedError:
        cpu_count = 1

    if cpu_count < 1:
        cpu_count = 1

    # Konservative Voreinstellung für Raspberry-Pi-Boards
    if cpu_count <= 2:
        return 2
    if cpu_count <= 4:
        return 3
    return min(6, cpu_count + 1)


bind_port = _read_int_from_env("FLASK_PORT", 80, minimum=1)
bind = f"0.0.0.0:{bind_port}"

workers = _configure_workers(multiprocessing.cpu_count)
threads = _read_int_from_env("AUDIO_PI_GUNICORN_THREADS", 2, minimum=1)
worker_class = "gthread"

timeout = _read_int_from_env("AUDIO_PI_GUNICORN_TIMEOUT", 120, minimum=30)
graceful_timeout = _read_int_from_env(
    "AUDIO_PI_GUNICORN_GRACEFUL_TIMEOUT", 30, minimum=10
)
keepalive = _read_int_from_env("AUDIO_PI_GUNICORN_KEEPALIVE", 5, minimum=1)

preload_app = True
capture_output = True
errorlog = "-"
accesslog = "-"
loglevel = os.getenv("AUDIO_PI_GUNICORN_LOGLEVEL", "info")

_logger = logging.getLogger(__name__)
_BACKGROUND_SERVICE_OWNER = multiprocessing.Value("i", 0)


def _ensure_app_module():
    repo_root = Path(__file__).resolve().parent
    repo_path = str(repo_root)
    if repo_path not in sys.path:
        sys.path.insert(0, repo_path)
    app_module = sys.modules.get("app")
    if app_module is None:
        for module in list(sys.modules.values()):
            candidate = getattr(module, "app", None)
            if getattr(candidate, "__name__", None) == "app":
                app_module = candidate
                sys.modules["app"] = candidate
                break
    if app_module is not None:
        return app_module
    try:
        import app as imported_app  # type: ignore
    except Exception:  # pragma: no cover - Fehler wird im Aufrufer geloggt
        raise
    sys.modules["app"] = imported_app
    return imported_app


def post_fork(server, worker):  # pragma: no cover - Wird in Tests simuliert
    """Startet Hintergrunddienste genau einmal nach dem Fork."""

    with _BACKGROUND_SERVICE_OWNER.get_lock():
        if _BACKGROUND_SERVICE_OWNER.value not in (0, worker.pid):
            return

        try:
            app_module = _ensure_app_module()
            started = app_module.start_background_services()
        except Exception:  # pragma: no cover - Fehlerführung via Gunicorn
            _logger.exception(
                "Start der Hintergrunddienste im Worker %s fehlgeschlagen.",
                worker.pid,
            )
            raise

        if started:
            _logger.info("Hintergrunddienste im Worker %s gestartet.", worker.pid)
        else:
            _logger.debug(
                "Hintergrunddienste waren bereits aktiv (Worker %s).",
                worker.pid,
            )
        _BACKGROUND_SERVICE_OWNER.value = worker.pid


def worker_exit(server, worker):  # pragma: no cover - Wird in Tests simuliert
    """Stoppt Hintergrunddienste, wenn der verantwortliche Worker endet."""

    with _BACKGROUND_SERVICE_OWNER.get_lock():
        if _BACKGROUND_SERVICE_OWNER.value != worker.pid:
            return

        try:
            app_module = _ensure_app_module()
            stopped = app_module.stop_background_services()
        except Exception:
            _logger.exception(
                "Stoppen der Hintergrunddienste im Worker %s fehlgeschlagen.",
                worker.pid,
            )
        else:
            if stopped:
                _logger.info(
                    "Hintergrunddienste durch Worker %s gestoppt.", worker.pid
                )
            else:
                _logger.debug(
                    "Worker %s meldete bereits gestoppte Hintergrunddienste.",
                    worker.pid,
                )
        finally:
            _BACKGROUND_SERVICE_OWNER.value = 0
