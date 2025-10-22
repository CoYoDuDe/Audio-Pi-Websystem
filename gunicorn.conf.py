"""Gunicorn-Konfiguration für das Audio-Pi-Websystem.

Die Werte lassen sich über Umgebungsvariablen anpassen. Standardmäßig wird
der Port aus ``FLASK_PORT`` gelesen, damit bestehende Installationen ohne
zusätzliche Anpassungen weiterhin den gleichen Listen-Port nutzen.
"""

from __future__ import annotations

import logging
import os


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


def _configure_workers() -> int:
    configured = _read_int_from_env("AUDIO_PI_GUNICORN_WORKERS", 1, minimum=1)
    if configured > 1:
        logging.getLogger(__name__).warning(
            "Mehrere Gunicorn-Worker werden nicht unterstützt, weil der Dienst auf "
            "gemeinsame In-Memory-Zustände angewiesen ist. Fallback auf einen Worker.",
        )
        return 1
    return configured


bind_port = _read_int_from_env("FLASK_PORT", 80, minimum=1)
bind = f"0.0.0.0:{bind_port}"

workers = _configure_workers()
threads = _read_int_from_env("AUDIO_PI_GUNICORN_THREADS", 2, minimum=1)
worker_class = "gthread"

timeout = _read_int_from_env("AUDIO_PI_GUNICORN_TIMEOUT", 120, minimum=30)
graceful_timeout = _read_int_from_env(
    "AUDIO_PI_GUNICORN_GRACEFUL_TIMEOUT", 30, minimum=10
)
keepalive = _read_int_from_env("AUDIO_PI_GUNICORN_KEEPALIVE", 5, minimum=1)

# Preloading deaktivieren, damit Hintergrund-Threads im eigentlichen Worker-Prozess
# gestartet werden und nicht nur im Master-Prozess existieren.
preload_app = False
capture_output = True
errorlog = "-"
accesslog = "-"
loglevel = os.getenv("AUDIO_PI_GUNICORN_LOGLEVEL", "info")
