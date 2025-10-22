"""Gunicorn-Konfiguration für das Audio-Pi-Websystem.

Die Werte lassen sich über Umgebungsvariablen anpassen. Standardmäßig wird
der Port aus ``FLASK_PORT`` gelesen, damit bestehende Installationen ohne
zusätzliche Anpassungen weiterhin den gleichen Listen-Port nutzen.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import threading
import time
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
_BACKGROUND_SERVICE_HANDOFF_TIMEOUT = _read_int_from_env(
    "AUDIO_PI_GUNICORN_SERVICE_HANDOFF_TIMEOUT", 30, minimum=0
)
_HANDOFF_RETRY_INTERVAL = 0.1
_HANDOFF_REGISTRY_LOCK = threading.Lock()
_PENDING_HANDOFFS: set[int] = set()


def _start_background_services_for_worker(
    worker_pid: int, *, previous_owner: int | None = None
) -> None:
    try:
        app_module = _ensure_app_module()
        started = app_module.start_background_services()
    except Exception:  # pragma: no cover - Fehlerführung via Gunicorn
        _logger.exception(
            "Start der Hintergrunddienste im Worker %s fehlgeschlagen.",
            worker_pid,
        )
        with _BACKGROUND_SERVICE_OWNER.get_lock():
            if _BACKGROUND_SERVICE_OWNER.value == worker_pid:
                _BACKGROUND_SERVICE_OWNER.value = 0
        raise

    if previous_owner is not None and started:
        _logger.info(
            "Hintergrunddienste nach Übergabe von Worker %s auf %s gestartet.",
            previous_owner,
            worker_pid,
        )
    elif started:
        _logger.info("Hintergrunddienste im Worker %s gestartet.", worker_pid)
    else:
        _logger.debug(
            "Hintergrunddienste waren bereits aktiv (Worker %s).",
            worker_pid,
        )


def _await_background_service_handoff(worker_pid: int) -> None:
    deadline = time.monotonic() + _BACKGROUND_SERVICE_HANDOFF_TIMEOUT
    previous_owner: int | None = None

    while True:
        with _BACKGROUND_SERVICE_OWNER.get_lock():
            owner = _BACKGROUND_SERVICE_OWNER.value
            if owner == 0:
                _BACKGROUND_SERVICE_OWNER.value = worker_pid
                break
            if owner == worker_pid:
                return

        if _BACKGROUND_SERVICE_HANDOFF_TIMEOUT == 0:
            with _BACKGROUND_SERVICE_OWNER.get_lock():
                previous_owner = _BACKGROUND_SERVICE_OWNER.value
                _BACKGROUND_SERVICE_OWNER.value = worker_pid
            break

        if time.monotonic() >= deadline:
            with _BACKGROUND_SERVICE_OWNER.get_lock():
                previous_owner = _BACKGROUND_SERVICE_OWNER.value
                _BACKGROUND_SERVICE_OWNER.value = worker_pid
            _logger.warning(
                "Worker %s übernimmt Hintergrunddienste nach Ablauf der Übergabefrist."
                " Vorheriger Besitzer war %s.",
                worker_pid,
                previous_owner,
            )
            break

        time.sleep(_HANDOFF_RETRY_INTERVAL)

    _start_background_services_for_worker(
        worker_pid, previous_owner=previous_owner
    )


def _schedule_background_service_handoff(worker_pid: int) -> None:
    with _HANDOFF_REGISTRY_LOCK:
        if worker_pid in _PENDING_HANDOFFS:
            return
        _PENDING_HANDOFFS.add(worker_pid)

    def _handoff_loop() -> None:
        try:
            _await_background_service_handoff(worker_pid)
        finally:
            with _HANDOFF_REGISTRY_LOCK:
                _PENDING_HANDOFFS.discard(worker_pid)

    thread = threading.Thread(
        target=_handoff_loop,
        name=f"audio-pi-background-handoff-{worker_pid}",
        daemon=True,
    )
    thread.start()


def _ensure_app_module():
    try:
        import app  # type: ignore
    except Exception:  # pragma: no cover - Fehler wird im Aufrufer geloggt
        raise
    return app


def post_fork(server, worker):  # pragma: no cover - Wird in Tests simuliert
    """Startet Hintergrunddienste genau einmal nach dem Fork."""

    with _BACKGROUND_SERVICE_OWNER.get_lock():
        owner = _BACKGROUND_SERVICE_OWNER.value
        if owner == 0:
            _BACKGROUND_SERVICE_OWNER.value = worker.pid
            immediate_start = True
        elif owner == worker.pid:
            return
        else:
            immediate_start = False

    if immediate_start:
        _start_background_services_for_worker(worker.pid)
    else:
        _schedule_background_service_handoff(worker.pid)


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
