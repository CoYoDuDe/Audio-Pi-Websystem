from __future__ import annotations

import atexit
import functools
import os
import time
import subprocess
import threading
import types
import glob
import shlex
import socket
from pathlib import Path
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import (
    SchedulerAlreadyRunningError,
    SchedulerNotRunningError,
)
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
import sqlite3
import tempfile
import calendar
import fnmatch
import math
import logging
from collections import deque
from dataclasses import dataclass, asdict, is_dataclass
from datetime import date, datetime, timedelta
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    has_request_context,
    g,
    current_app,
)
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    login_required,
    logout_user,
    current_user,
)
from flask_wtf import CSRFProtect
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)


try:  # pragma: no cover - Import wird separat getestet
    import lgpio as GPIO
except ImportError:  # pragma: no cover - Verhalten wird in Tests geprüft
    GPIO = None  # type: ignore[assignment]
    GPIO_AVAILABLE = False
    logger.warning("lgpio konnte nicht importiert werden, GPIO-Funktionen deaktiviert.")
else:
    GPIO_AVAILABLE = True

class _PygameUnavailableError(Exception):
    """Platzhalter, wenn pygame nicht verfügbar ist."""


try:  # pragma: no cover - Import wird separat getestet
    import pygame
except ImportError:  # pragma: no cover - Verhalten wird in Tests geprüft
    pygame = None  # type: ignore[assignment]
    pygame_imported = False
    pygame_error = _PygameUnavailableError
    logging.getLogger(__name__).warning(
        "pygame konnte nicht importiert werden, Audio-Funktionen deaktiviert."
    )
else:
    pygame_imported = True
    pygame_error = getattr(pygame, "error", _PygameUnavailableError)


def _ensure_pygame_music_interface() -> None:
    if pygame is None:
        return

    mixer = getattr(pygame, "mixer", None)
    if mixer is None:
        dummy_music = types.SimpleNamespace()
        mixer = types.SimpleNamespace(music=dummy_music)
        setattr(pygame, "mixer", mixer)

    if not hasattr(mixer, "init"):
        setattr(mixer, "init", lambda *args, **kwargs: None)

    music = getattr(mixer, "music", None)
    if music is None:
        music = types.SimpleNamespace()
        setattr(mixer, "music", music)

    defaults = {
        "set_volume": lambda *args, **kwargs: None,
        "get_volume": lambda *args, **kwargs: 1.0,
        "get_busy": lambda *args, **kwargs: False,
        "load": lambda *args, **kwargs: None,
        "play": lambda *args, **kwargs: None,
        "stop": lambda *args, **kwargs: None,
        "pause": lambda *args, **kwargs: None,
        "unpause": lambda *args, **kwargs: None,
    }

    for name, default in defaults.items():
        if not hasattr(music, name):
            setattr(music, name, default)


_ensure_pygame_music_interface()


from network_config import (
    NetworkConfigError,
    NormalizedNetworkSettings,
    get_current_hostname as _get_current_hostname,
    load_network_settings as _load_network_settings,
    normalize_network_settings as _normalize_network_settings,
    restore_network_backup as _restore_network_backup,
    update_hosts_file as _update_hosts_file,
    validate_hostname as _validate_hostname,
    validate_local_domain as _validate_local_domain,
    write_network_settings as _write_network_settings,
)


NETWORK_SETTINGS_DEFAULTS: Dict[str, str] = {
    "mode": "dhcp",
    "ipv4_address": "",
    "ipv4_prefix": "",
    "ipv4_gateway": "",
    "dns_servers": "",
    "hostname": "",
    "local_domain": "",
}

NETWORK_SETTING_KEY_MAP: Dict[str, str] = {
    "mode": "network_mode",
    "ipv4_address": "network_ipv4_address",
    "ipv4_prefix": "network_ipv4_prefix",
    "ipv4_gateway": "network_ipv4_gateway",
    "dns_servers": "network_dns_servers",
    "hostname": "network_hostname",
    "local_domain": "network_local_domain",
}


def _load_network_settings_for_template(interface: str) -> Dict[str, str]:
    """Lädt Netzwerkeinstellungen und wandelt sie für das Template auf."""

    defaults = dict(NETWORK_SETTINGS_DEFAULTS)
    try:
        candidate = _load_network_settings(interface)
    except NetworkConfigError as exc:  # pragma: no cover - Validierungsfehler protokollieren
        logger.warning(
            "Netzwerkeinstellungen für %s konnten nicht geladen werden: %s",
            interface,
            exc,
        )
        normalized: Dict[str, Any] = {}
    except Exception:  # pragma: no cover - robustes Fallback
        logger.warning(
            "Netzwerkeinstellungen für %s konnten nicht geladen werden.",
            interface,
            exc_info=True,
        )
        normalized = {}
    else:
        if candidate is None:
            normalized = {}
        elif isinstance(candidate, dict):
            normalized = dict(candidate)
        elif is_dataclass(candidate):
            normalized = asdict(candidate)
        elif hasattr(candidate, "to_dict") and callable(getattr(candidate, "to_dict")):
            try:
                normalized = candidate.to_dict()  # type: ignore[assignment]
            except Exception:  # pragma: no cover - resiliente Konvertierung
                logger.warning(
                    "Netzwerkeinstellungen-Objekt konnte nicht in ein Dict konvertiert werden.",
                    exc_info=True,
                )
                normalized = {}
        else:
            normalized = {}

    result = defaults.copy()
    for key in defaults:
        value = normalized.get(key)
        if value is None:
            continue
        if key == "mode":
            if isinstance(value, bytes):
                value = value.decode(errors="ignore")
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"manual", "static", "static_ipv4", "manual_ipv4"}:
                    result["mode"] = "manual"
                elif lowered == "dhcp":
                    result["mode"] = "dhcp"
                elif lowered:
                    result["mode"] = lowered
            continue
        if isinstance(value, (list, tuple, set)):
            rendered = ", ".join(
                str(item).strip() for item in value if str(item).strip()
            )
            result[key] = rendered
        else:
            result[key] = str(value)

    for field, setting_key in NETWORK_SETTING_KEY_MAP.items():
        try:
            stored_value = get_setting(setting_key, None)
        except Exception:
            stored_value = None
        if stored_value in (None, ""):
            continue
        result[field] = stored_value

    if not result.get("hostname"):
        try:
            result["hostname"] = _get_current_hostname()
        except Exception:  # pragma: no cover - defensiver Fallback
            result["hostname"] = socket.gethostname()

    return result


def _read_disable_sudo_flag() -> bool:
    value = os.environ.get("AUDIO_PI_DISABLE_SUDO", "1").strip().lower()
    return value in {"1", "true", "yes", "on"}


_SUDO_DISABLED = _read_disable_sudo_flag()


def is_sudo_disabled() -> bool:
    return _SUDO_DISABLED


def _strip_sudo_from_command(command):
    if command is None:
        return command

    if isinstance(command, (list, tuple)):
        if not command:
            return []
        if command[0] == "sudo":
            return list(command[1:])
        return list(command)

    if isinstance(command, str):
        stripped = command.lstrip()
        if stripped.startswith("sudo "):
            return stripped[5:]
        if stripped == "sudo":
            return ""
        return command

    return command


def _build_privileged_command(parts: Iterable[str]) -> List[str]:
    command = list(parts)
    if not command:
        return command
    if _SUDO_DISABLED:
        return command
    return ["sudo", *command]


def privileged_command(*parts: str) -> List[str]:
    return _build_privileged_command(parts)


def _wrap_subprocess_function(func):
    @functools.wraps(func)
    def wrapper(command, *args, **kwargs):
        if command is not None:
            command = _strip_sudo_from_command(command)
        if "args" in kwargs:
            kwargs["args"] = _strip_sudo_from_command(kwargs["args"])
        return func(command, *args, **kwargs)

    return wrapper


def _wrap_subprocess_popen(func):
    @functools.wraps(func)
    def wrapper(*popenargs, **kwargs):
        if popenargs:
            first = _strip_sudo_from_command(popenargs[0])
            popenargs = (first, *popenargs[1:])
        if "args" in kwargs:
            kwargs["args"] = _strip_sudo_from_command(kwargs["args"])
        return func(*popenargs, **kwargs)

    return wrapper


def _should_strip_sudo() -> bool:
    return _SUDO_DISABLED


_SUBPROCESS_METHODS = (
    "run",
    "check_call",
    "call",
    "check_output",
    "Popen",
)


try:
    _ORIGINAL_SUBPROCESS_FUNCTIONS  # type: ignore[name-defined]
except NameError:  # pragma: no cover - nur beim ersten Import relevant
    _ORIGINAL_SUBPROCESS_FUNCTIONS: Dict[str, Callable[..., Any]] = {}

try:
    _SUBPROCESS_PATCHED  # type: ignore[name-defined]
except NameError:  # pragma: no cover - nur beim ersten Import relevant
    _SUBPROCESS_PATCHED: bool = False


def _store_original_subprocess_functions() -> None:
    if _ORIGINAL_SUBPROCESS_FUNCTIONS:
        return

    for name in _SUBPROCESS_METHODS:
        _ORIGINAL_SUBPROCESS_FUNCTIONS[name] = getattr(subprocess, name)


def _restore_subprocess_functions() -> None:
    global _SUBPROCESS_PATCHED

    if not _SUBPROCESS_PATCHED:
        return

    for name, original in _ORIGINAL_SUBPROCESS_FUNCTIONS.items():
        setattr(subprocess, name, original)

    _SUBPROCESS_PATCHED = False


def _patch_subprocess_functions() -> None:
    global _SUBPROCESS_PATCHED

    if _SUBPROCESS_PATCHED:
        return

    _store_original_subprocess_functions()

    subprocess.run = _wrap_subprocess_function(
        _ORIGINAL_SUBPROCESS_FUNCTIONS["run"]
    )  # type: ignore[assignment]
    subprocess.check_call = _wrap_subprocess_function(
        _ORIGINAL_SUBPROCESS_FUNCTIONS["check_call"]
    )  # type: ignore[assignment]
    subprocess.call = _wrap_subprocess_function(
        _ORIGINAL_SUBPROCESS_FUNCTIONS["call"]
    )  # type: ignore[assignment]
    subprocess.check_output = _wrap_subprocess_function(
        _ORIGINAL_SUBPROCESS_FUNCTIONS["check_output"]
    )  # type: ignore[assignment]
    subprocess.Popen = _wrap_subprocess_popen(
        _ORIGINAL_SUBPROCESS_FUNCTIONS["Popen"]
    )  # type: ignore[assignment]

    _SUBPROCESS_PATCHED = True


def refresh_subprocess_wrapper_state() -> None:
    if _should_strip_sudo():
        _patch_subprocess_functions()
    else:
        _restore_subprocess_functions()


_store_original_subprocess_functions()
refresh_subprocess_wrapper_state()

from pydub import AudioSegment
from pydub.exceptions import CouldntDecodeError
try:  # pragma: no cover - Import wird separat getestet
    import smbus
except ImportError:  # pragma: no cover - Verhalten wird in Tests geprüft
    SMBUS_AVAILABLE = False
    fallback_env = os.environ.get("AUDIO_PI_ALLOW_SMBUS2_FALLBACK")
    testing_active = os.environ.get("TESTING", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if fallback_env is not None:
        fallback_allowed = fallback_env.strip().lower() not in {
            "0",
            "false",
            "no",
        }
    else:
        fallback_allowed = not testing_active

    if fallback_allowed:
        try:
            from smbus2 import SMBus as _SMBusFallback
        except ImportError:
            pass
        else:
            smbus = types.SimpleNamespace(SMBus=_SMBusFallback)  # type: ignore[assignment]
            SMBUS_AVAILABLE = True
            logging.getLogger(__name__).info(
                "smbus ist nicht verfügbar, nutze smbus2 als Fallback."
            )

    if not SMBUS_AVAILABLE:
        smbus = None  # type: ignore[assignment]
        logging.getLogger(__name__).warning(
            "smbus konnte nicht importiert werden, I²C-Funktionen deaktiviert."
        )
else:
    SMBUS_AVAILABLE = True
import sys
import secrets
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Literal, Set
from hardware.buttons import ButtonAssignment, ButtonMonitor

if GPIO_AVAILABLE:
    GPIOError = GPIO.error
else:
    class GPIOError(Exception):
        """Platzhalter, wenn lgpio nicht verfügbar ist."""


app = Flask(__name__)
_logger = logging.getLogger(__name__)
_secret_key_from_env = os.environ.get("FLASK_SECRET_KEY")
SECRET_KEY_GENERATED = False

if _secret_key_from_env and _secret_key_from_env.strip():
    secret_key = _secret_key_from_env
else:
    secret_key = secrets.token_urlsafe(32)
    SECRET_KEY_GENERATED = True
    _logger.warning(
        "FLASK_SECRET_KEY nicht gesetzt oder leer. Temporären Schlüssel generiert."
    )

app.secret_key = secret_key
app.config["SECRET_KEY_GENERATED"] = SECRET_KEY_GENERATED
app.config["INITIAL_ADMIN_PASSWORD_FILE"] = None
csrf = CSRFProtect()
csrf.init_app(app)
TESTING_RAW = os.getenv("TESTING")


def _env_to_bool(value):
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


TESTING = _env_to_bool(TESTING_RAW)
SUPPRESS_AUTOSTART = _env_to_bool(os.getenv("AUDIO_PI_SUPPRESS_AUTOSTART"))
app.testing = TESTING
login_manager = LoginManager(app)
login_manager.login_view = "login"

@app.before_request
def enforce_initial_password_change():
    if not current_user.is_authenticated:
        return None

    if not getattr(current_user, "must_change_password", False):
        return None

    endpoint = request.endpoint
    if endpoint is None:
        return None

    allowed_endpoints = {"change_password", "logout_route"}
    if endpoint == "static" or endpoint.startswith("static"):
        return None

    if endpoint in allowed_endpoints:
        return None

    return redirect(url_for("change_password"))

# Konfiguration
UPLOAD_FOLDER = "uploads"
ALLOWED_EXTENSIONS = {"wav", "mp3"}
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DEFAULT_MAX_UPLOAD_MB = 100

DEFAULT_LOG_VIEW_MAX_BYTES = 64 * 1024
DEFAULT_LOG_VIEW_MAX_LINES = 2000
DEFAULT_LOG_FILE_NAME = "app.log"


def _resolve_positive_int_env(env_var: str, default: int) -> int:
    raw_value = os.getenv(env_var)
    if raw_value is None:
        return default

    candidate = raw_value.strip()
    if not candidate:
        return default

    try:
        parsed = int(candidate)
    except ValueError:
        _logger.warning(
            "Ungültiger Wert für %s (%s). Verwende Standard %s.",
            env_var,
            raw_value,
            default,
        )
        return default

    if parsed <= 0:
        _logger.warning(
            "Wert für %s muss größer als 0 sein. Verwende Standard %s.",
            env_var,
            default,
        )
        return default

    return parsed


def _resolve_log_file_path(raw_value: Optional[str]) -> str:
    if raw_value is None:
        return DEFAULT_LOG_FILE_NAME

    candidate = raw_value.strip()
    if not candidate:
        return DEFAULT_LOG_FILE_NAME

    return str(Path(candidate).expanduser())


def _resolve_max_upload_mb(raw_value: Optional[str]) -> int:
    if raw_value is None:
        return DEFAULT_MAX_UPLOAD_MB

    candidate = raw_value.strip()
    if not candidate:
        return DEFAULT_MAX_UPLOAD_MB

    try:
        parsed = int(candidate)
    except ValueError:
        _logger.warning(
            "Ungültiger Wert für AUDIO_PI_MAX_UPLOAD_MB (%s). Verwende Standard %s MB.",
            raw_value,
            DEFAULT_MAX_UPLOAD_MB,
        )
        return DEFAULT_MAX_UPLOAD_MB

    if parsed <= 0:
        _logger.warning(
            "Nichtpositiver Wert für AUDIO_PI_MAX_UPLOAD_MB (%s). Verwende Standard %s MB.",
            raw_value,
            DEFAULT_MAX_UPLOAD_MB,
        )
        return DEFAULT_MAX_UPLOAD_MB

    return parsed


_max_upload_env = os.getenv("AUDIO_PI_MAX_UPLOAD_MB")
MAX_UPLOAD_SIZE_MB = _resolve_max_upload_mb(_max_upload_env)
app.config["MAX_CONTENT_LENGTH_MB"] = MAX_UPLOAD_SIZE_MB
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE_MB * 1024 * 1024

LOG_VIEW_MAX_BYTES = _resolve_positive_int_env(
    "AUDIO_PI_LOG_VIEW_MAX_BYTES",
    DEFAULT_LOG_VIEW_MAX_BYTES,
)
LOG_VIEW_MAX_LINES = _resolve_positive_int_env(
    "AUDIO_PI_LOG_VIEW_MAX_LINES",
    DEFAULT_LOG_VIEW_MAX_LINES,
)
LOG_VIEW_FILE = _resolve_log_file_path(os.getenv("AUDIO_PI_LOG_FILE"))

app.config["LOG_VIEW_MAX_BYTES"] = LOG_VIEW_MAX_BYTES
app.config["LOG_VIEW_MAX_LINES"] = LOG_VIEW_MAX_LINES
app.config["LOG_VIEW_FILE"] = LOG_VIEW_FILE

DB_FILE = os.getenv("DB_FILE", "audio.db")
DEFAULT_INITIAL_PASSWORD_FILENAME = "initial_admin_password.txt"
INITIAL_ADMIN_PASSWORD_FILE_ENV = os.getenv("INITIAL_ADMIN_PASSWORD_FILE")
DEFAULT_AMPLIFIER_GPIO_PIN = 17
AMPLIFIER_GPIO_PIN_SETTING_KEY = "amplifier_gpio_pin"
GPIO_PIN_ENDSTUFE = DEFAULT_AMPLIFIER_GPIO_PIN
CONFIGURED_AMPLIFIER_GPIO_PIN: Optional[int] = None
VERZOEGERUNG_SEC = 5
DEFAULT_BUTTON_DEBOUNCE_MS = 150
DEFAULT_MAX_SCHEDULE_DELAY_SECONDS = 60
DAC_SINK_SETTING_KEY = "dac_sink_name"
DAC_SINK_LABEL_SETTING_KEY = "dac_sink_label"
DEFAULT_DAC_SINK_FALLBACK = "alsa_output.platform-soc_107c000000_sound.stereo-fallback"
DEFAULT_DAC_SINK = DEFAULT_DAC_SINK_FALLBACK
DEFAULT_DAC_SINK_HINT = DEFAULT_DAC_SINK_FALLBACK


def _determine_effective_default_dac_sink() -> str:
    env_value = os.getenv("DAC_SINK_NAME")
    if env_value is not None:
        candidate = env_value.strip()
        if candidate:
            return candidate
    return DEFAULT_DAC_SINK_FALLBACK


def _refresh_default_dac_sink() -> str:
    global DEFAULT_DAC_SINK, DEFAULT_DAC_SINK_HINT

    default_sink = _determine_effective_default_dac_sink()
    DEFAULT_DAC_SINK = default_sink
    DEFAULT_DAC_SINK_HINT = default_sink
    return default_sink


_refresh_default_dac_sink()
DEFAULT_DAC_SINK_LABEL = "Konfigurierter DAC"
DAC_SINK = DEFAULT_DAC_SINK
DAC_SINK_HINT = DEFAULT_DAC_SINK
CONFIGURED_DAC_SINK: Optional[str] = None
DAC_SINK_LABEL: Optional[str] = None
NORMALIZATION_HEADROOM_SETTING_KEY = "normalization_headroom_db"
NORMALIZATION_HEADROOM_ENV_KEY = "NORMALIZATION_HEADROOM_DB"
DEFAULT_NORMALIZATION_HEADROOM_DB = 0.1
raw_max_schedule_delay = os.getenv(
    "MAX_SCHEDULE_DELAY_SECONDS", str(DEFAULT_MAX_SCHEDULE_DELAY_SECONDS)
)
try:
    parsed_max_schedule_delay = int(raw_max_schedule_delay)
except (TypeError, ValueError):
    MAX_SCHEDULE_DELAY_SECONDS = DEFAULT_MAX_SCHEDULE_DELAY_SECONDS
    logging.warning(
        "Ungültiger MAX_SCHEDULE_DELAY_SECONDS-Wert '%s'. Fallback auf %s Sekunden.",
        raw_max_schedule_delay,
        DEFAULT_MAX_SCHEDULE_DELAY_SECONDS,
    )
else:
    sanitized_max_schedule_delay = max(0, parsed_max_schedule_delay)
    if sanitized_max_schedule_delay != parsed_max_schedule_delay:
        logging.warning(
            "MAX_SCHEDULE_DELAY_SECONDS-Wert '%s' ist negativ. Verwende %s Sekunden.",
            raw_max_schedule_delay,
            sanitized_max_schedule_delay,
        )
    MAX_SCHEDULE_DELAY_SECONDS = sanitized_max_schedule_delay
SCHEDULE_VOLUME_PERCENT_SETTING_KEY = "schedule_default_volume_percent"
SCHEDULE_VOLUME_DB_SETTING_KEY = "schedule_default_volume_db"
SCHEDULE_DEFAULT_VOLUME_PERCENT_FALLBACK = 100
SCHEDULE_VOLUME_PERCENT_MIN = 0
SCHEDULE_VOLUME_PERCENT_MAX = 100

PLAY_NOW_ALLOWED_TYPES = {"file", "playlist"}

HARDWARE_BUTTON_ACTIONS = [
    ("PLAY", "Wiedergabe (Datei/Playlist)"),
    ("STOP", "Wiedergabe stoppen"),
    ("BT_ON", "Bluetooth aktivieren"),
    ("BT_OFF", "Bluetooth deaktivieren"),
]
HARDWARE_BUTTON_ACTION_LABELS = {key: label for key, label in HARDWARE_BUTTON_ACTIONS}

PAGE_SIZE_DEFAULT = 10
PAGE_SIZE_ALLOWED = {10, 25, 50}
PAGE_SIZE_OPTIONS = [
    {"value": "10", "label": "10"},
    {"value": "25", "label": "25"},
    {"value": "50", "label": "50"},
    {"value": "all", "label": "Alle"},
]


def _parse_page_size(raw_value: Optional[str]) -> int | str:
    if raw_value is None:
        return PAGE_SIZE_DEFAULT
    normalized = raw_value.strip().lower()
    if normalized == "all":
        return "all"
    try:
        numeric = int(normalized)
    except (TypeError, ValueError):
        return PAGE_SIZE_DEFAULT
    if numeric in PAGE_SIZE_ALLOWED:
        return numeric
    return PAGE_SIZE_DEFAULT


def _parse_page_number(raw_value: Optional[str]) -> int:
    try:
        page = int(raw_value)
    except (TypeError, ValueError):
        return 1
    return page if page > 0 else 1


def _compute_pagination_meta(total_count: int, page_number: int, page_size: int | str) -> dict:
    if page_size == "all":
        total_pages = 1 if total_count else 1
        page = 1
        limit = None
        offset = 0
        pages = [1]
    else:
        total_pages = max(1, math.ceil(total_count / page_size)) if total_count else 1
        page = min(page_number, total_pages)
        limit = page_size
        offset = (page - 1) * page_size
        pages = list(range(1, total_pages + 1))
    return {
        "page": page,
        "page_size": page_size,
        "page_size_value": "all" if page_size == "all" else str(page_size),
        "limit": limit,
        "offset": offset,
        "total_count": total_count,
        "total_pages": total_pages,
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "previous_page": page - 1 if page > 1 else None,
        "next_page": page + 1 if page < total_pages else None,
        "pages": pages,
    }


def build_index_url(**kwargs):
    params = request.args.to_dict()
    for key, value in kwargs.items():
        if value is None:
            params.pop(key, None)
        else:
            params[key] = value
    return url_for("index", **params)

# Logging
logging.basicConfig(
    filename="app.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
gpio_handle: Optional[int] = None
gpio_chip_id: Optional[int] = None

if not TESTING and GPIO_AVAILABLE:
    _gpio_chip_candidates: List[int] = []
    _gpio_chip_seen: Set[int] = set()

    def _add_gpio_candidate(chip_id: int) -> None:
        if chip_id in _gpio_chip_seen:
            return
        _gpio_chip_seen.add(chip_id)
        _gpio_chip_candidates.append(chip_id)

    _add_gpio_candidate(4)  # Raspberry Pi 5 bevorzugt Chip 4
    _add_gpio_candidate(0)  # Raspberry Pi 4 und ältere Modelle

    for _chip_path in sorted(glob.glob("/dev/gpiochip*")):
        if not _chip_path.startswith("/dev/gpiochip"):
            continue
        suffix = _chip_path[len("/dev/gpiochip") :]
        if suffix.isdigit():
            _add_gpio_candidate(int(suffix))

    _errors: List[Tuple[int, Exception]] = []

    for _chip_id in _gpio_chip_candidates:
        try:
            gpio_handle = GPIO.gpiochip_open(_chip_id)
        except (GPIOError, OSError) as exc:
            _errors.append((_chip_id, exc))
        else:
            gpio_chip_id = _chip_id
            logging.info(
                "GPIO initialisiert für Verstärker (OUTPUT/HIGH = an, LOW = aus) - Nutzung von gpiochip%s",
                _chip_id,
            )
            break

    if gpio_handle is None:
        if _errors:
            _tested = ", ".join(
                f"gpiochip{chip}: {error}" for chip, error in _errors
            )
            logging.warning(
                "GPIO-Chip konnte nicht geöffnet werden, starte ohne Verstärkersteuerung (versucht: %s)",
                _tested,
            )
        else:
            logging.warning(
                "GPIO-Chip konnte nicht geöffnet werden, keine Kandidaten gefunden."
            )
else:
    if not TESTING and not GPIO_AVAILABLE:
        logging.warning(
            "lgpio nicht verfügbar, starte ohne Verstärkersteuerung."
        )
    gpio_handle = None
    gpio_chip_id = None
amplifier_claimed = False

# Track pause status manually since pygame lacks a get_paused() helper
is_paused = False


# Globale Statusinformationen für Audiofunktionen
audio_status = {"dac_sink_detected": None}

pygame_available = TESTING and pygame_imported


def load_initial_volume():
    output = subprocess.getoutput("pactl get-sink-volume @DEFAULT_SINK@")
    match = re.search(r"(\d+)%", output)
    if match:
        initial_vol = int(match.group(1))
        pygame.mixer.music.set_volume(initial_vol / 100.0)
        logging.info(f"Initiale Lautstärke geladen: {initial_vol}%")


# Nutzerhinweis, wenn Audio-Funktionen nicht verfügbar sind
AUDIO_UNAVAILABLE_MESSAGE = (
    "Audio-Wiedergabe nicht verfügbar, da pygame nicht initialisiert werden konnte."
)


def _notify_audio_unavailable(action: str) -> None:
    message = f"{action}: {AUDIO_UNAVAILABLE_MESSAGE}" if action else AUDIO_UNAVAILABLE_MESSAGE
    logging.warning(message)
    if has_request_context():
        try:
            flash(message)
        except Exception:
            logging.debug(
                "Konnte Flash-Nachricht für nicht verfügbare Audio-Funktion nicht senden.",
                exc_info=True,
            )


# Pygame Audio und Lautstärke nur initialisieren, wenn nicht im Test
if not TESTING and pygame_imported:
    try:
        pygame.mixer.init()
    except pygame_error as exc:
        pygame_available = False
        logging.warning(
            "pygame.mixer konnte nicht initialisiert werden. Audio-Funktionen werden deaktiviert: %s",
            exc,
        )
    else:
        pygame_available = True
        load_initial_volume()

# RTC (Echtzeituhr) Setup
class RTCUnavailableError(Exception):
    """RTC I²C-Bus konnte nicht initialisiert werden."""


class RTCWriteError(RTCUnavailableError):
    """Fehler beim Schreiben auf die RTC über den I²C-Bus."""


class UnsupportedRTCError(Exception):
    """Gefundener RTC-Typ wird derzeit nicht unterstützt."""


RTC_SUPPORTED_TYPES = {
    "auto": {
        "label": "Automatische Erkennung",
        "default_addresses": (0x51, 0x68, 0x57, 0x6F),
    },
    "pcf8563": {
        "label": "PCF8563 (0x51)",
        "default_addresses": (0x51,),
    },
    "ds3231": {
        "label": "DS3231 / DS1307 (0x68)",
        "default_addresses": (0x68, 0x57),
    },
}
RTC_MODULE_SETTING_KEY = "rtc_module_type"
RTC_ADDRESS_SETTING_KEY = "rtc_addresses"

RTC_DEFAULT_CANDIDATE_ADDRESSES = RTC_SUPPORTED_TYPES["auto"]["default_addresses"]
RTC_CANDIDATE_ADDRESSES: Tuple[int, ...] = RTC_DEFAULT_CANDIDATE_ADDRESSES
RTC_ADDRESS = RTC_DEFAULT_CANDIDATE_ADDRESSES[0]
RTC_AVAILABLE = False
RTC_DETECTED_ADDRESS: Optional[int] = None
RTC_FORCED_TYPE: Optional[str] = None
RTC_MISSING_FLAG = False
RTC_SYNC_STATUS = {"success": None, "last_error": None}
RTC_KNOWN_ADDRESS_TYPES = {
    0x51: "pcf8563",
    0x57: "ds3231",
    0x68: "ds3231",
    0x69: "ds3231",
    0x6F: "ds3231",
}


def scan_i2c_addresses_for_rtc(
    i2c_bus, candidate_addresses: Iterable[int]
) -> Optional[int]:
    """Suche nach einer RTC auf dem angegebenen I²C-Bus."""

    if i2c_bus is None:
        return None

    for address in candidate_addresses:
        try:
            try:
                i2c_bus.read_byte_data(address, 0x00)
            except AttributeError:
                i2c_bus.read_byte(address)
        except OSError:
            continue
        except Exception as exc:  # pragma: no cover - nur zur Sicherheit
            logging.debug(
                "RTC-Scan: Adresse 0x%02X übersprungen (%s)",
                address,
                exc,
            )
            continue
        return address
    return None


def _normalize_rtc_addresses(addresses: Iterable[int]) -> Tuple[int, ...]:
    normalized = []
    for address in addresses:
        if not isinstance(address, int):
            continue
        if address < 0 or address > 0x7F:
            continue
        if address not in normalized:
            normalized.append(address)
    return tuple(normalized)


def _set_rtc_candidate_addresses(addresses: Iterable[int]) -> Tuple[int, ...]:
    global RTC_CANDIDATE_ADDRESSES, RTC_ADDRESS
    normalized = _normalize_rtc_addresses(addresses)
    if not normalized:
        normalized = RTC_DEFAULT_CANDIDATE_ADDRESSES
    RTC_CANDIDATE_ADDRESSES = normalized
    RTC_ADDRESS = normalized[0]
    return normalized


def refresh_rtc_detection(candidate_addresses: Optional[Iterable[int]] = None):
    global RTC_AVAILABLE, RTC_DETECTED_ADDRESS, RTC_MISSING_FLAG, bus, RTC_ADDRESS

    if candidate_addresses is None:
        candidate_addresses = RTC_CANDIDATE_ADDRESSES

    candidates = _set_rtc_candidate_addresses(candidate_addresses)
    if not candidates:
        RTC_ADDRESS = None
    if bus is None:
        RTC_AVAILABLE = False
        RTC_DETECTED_ADDRESS = None
        RTC_MISSING_FLAG = True
        return

    try:
        detected_address = scan_i2c_addresses_for_rtc(bus, candidates)
    except Exception as exc:  # pragma: no cover - hardwareabhängig
        logging.warning(f"RTC-Scan fehlgeschlagen: {exc}")
        RTC_DETECTED_ADDRESS = None
        RTC_AVAILABLE = False
        RTC_MISSING_FLAG = True
        return

    if detected_address is not None:
        RTC_ADDRESS = detected_address
        RTC_DETECTED_ADDRESS = detected_address
        RTC_AVAILABLE = True
        RTC_MISSING_FLAG = False
        logging.info("RTC auf I²C-Adresse 0x%02X erkannt.", detected_address)
        return

    RTC_DETECTED_ADDRESS = None
    RTC_AVAILABLE = False
    RTC_MISSING_FLAG = True
    logging.warning(
        "Keine RTC auf den bekannten I²C-Adressen gefunden (%s).",
        ", ".join(f"0x{addr:02X}" for addr in candidates) or "(keine)",
    )


bus = None
if not SMBUS_AVAILABLE:
    RTC_AVAILABLE = False
    RTC_DETECTED_ADDRESS = None
    RTC_MISSING_FLAG = True
elif not TESTING:
    try:
        bus = smbus.SMBus(1)  # type: ignore[union-attr]
    except (FileNotFoundError, OSError) as first_exc:
        logging.debug("RTC SMBus 1 nicht verfügbar: %s", first_exc)
        try:
            bus = smbus.SMBus(0)  # type: ignore[union-attr]
            logging.info("RTC nutzt I²C-Bus 0 als Fallback")
        except (FileNotFoundError, OSError) as second_exc:
            logging.warning(
                "RTC SMBus nicht verfügbar (Bus 1 und 0 fehlgeschlagen): %s / %s",
                first_exc,
                second_exc,
            )
            bus = None
            RTC_MISSING_FLAG = True
        else:
            RTC_MISSING_FLAG = False
    else:
        RTC_MISSING_FLAG = bus is None
else:
    RTC_MISSING_FLAG = True

refresh_rtc_detection()


def bcd_to_dec(val):
    return ((val >> 4) * 10) + (val & 0x0F)


def dec_to_bcd(val):
    return ((val // 10) << 4) | (val % 10)


def _determine_rtc_type(address: int) -> str:
    if RTC_FORCED_TYPE:
        if RTC_FORCED_TYPE not in RTC_SUPPORTED_TYPES or RTC_FORCED_TYPE == "auto":
            raise UnsupportedRTCError(
                f"RTC-Typ '{RTC_FORCED_TYPE}' wird nicht unterstützt"
            )
        return RTC_FORCED_TYPE

    rtc_type = RTC_KNOWN_ADDRESS_TYPES.get(address)
    if rtc_type:
        return rtc_type
    raise UnsupportedRTCError(f"RTC-Typ auf Adresse 0x{address:02X} nicht unterstützt")


def _python_weekday_to_rtc(py_weekday: int, rtc_type: str) -> int:
    if rtc_type == "pcf8563":
        return (py_weekday + 1) % 7
    if rtc_type == "ds3231":
        return ((py_weekday + 1) % 7) + 1 or 1
    raise UnsupportedRTCError(f"RTC-Typ '{rtc_type}' nicht unterstützt")


def _rtc_weekday_to_python(raw_weekday: int, rtc_type: str) -> int:
    if rtc_type == "pcf8563":
        return (raw_weekday + 6) % 7
    if rtc_type == "ds3231":
        weekday = raw_weekday & 0x07
        if weekday == 0:
            weekday = 1
        return (weekday + 5) % 7
    raise UnsupportedRTCError(f"RTC-Typ '{rtc_type}' nicht unterstützt")


def read_rtc():
    if bus is None or not RTC_AVAILABLE or RTC_ADDRESS is None:
        raise RTCUnavailableError("RTC-Bus nicht initialisiert")
    address = RTC_DETECTED_ADDRESS or RTC_ADDRESS
    rtc_type = _determine_rtc_type(address)
    if rtc_type == "pcf8563":
        data = bus.read_i2c_block_data(address, 0x02, 7)
        second = bcd_to_dec(data[0] & 0x7F)
        minute = bcd_to_dec(data[1] & 0x7F)
        hour = bcd_to_dec(data[2] & 0x3F)
        day = bcd_to_dec(data[3] & 0x3F)
        weekday_raw = data[4] & 0x07
        month = bcd_to_dec(data[5] & 0x1F)
        year_offset = bcd_to_dec(data[6])
        century_offset = 2000
    elif rtc_type == "ds3231":
        data = bus.read_i2c_block_data(address, 0x00, 7)
        second = bcd_to_dec(data[0] & 0x7F)
        minute = bcd_to_dec(data[1] & 0x7F)
        hour = bcd_to_dec(data[2] & 0x3F)
        weekday_raw = data[3] & 0x07
        day = bcd_to_dec(data[4] & 0x3F)
        month_raw = data[5]
        month = bcd_to_dec(month_raw & 0x1F)
        century_offset = 2100 if (month_raw & 0x80) else 2000
        year_offset = bcd_to_dec(data[6])
    else:  # pragma: no cover - abgesichert durch _determine_rtc_type
        raise UnsupportedRTCError(f"RTC-Typ '{rtc_type}' nicht unterstützt")

    if month < 1 or month > 12:
        raise ValueError("Ungültiger Monat von RTC – RTC evtl. initialisieren!")
    weekday_python = _rtc_weekday_to_python(weekday_raw, rtc_type)
    dt_value = datetime(
        century_offset + year_offset,
        month,
        day,
        hour,
        minute,
        second,
    )
    if dt_value.weekday() != weekday_python:
        logging.debug(
            "RTC-Wochentag unterscheidet sich (RTC=%s, Python=%s)",
            weekday_raw,
            dt_value.weekday(),
        )
    return dt_value


def set_rtc(dt):
    if bus is None or not RTC_AVAILABLE or RTC_ADDRESS is None:
        raise RTCUnavailableError("RTC-Bus nicht initialisiert")
    address = RTC_DETECTED_ADDRESS or RTC_ADDRESS
    rtc_type = _determine_rtc_type(address)
    second = dec_to_bcd(dt.second)
    minute = dec_to_bcd(dt.minute)
    hour = dec_to_bcd(dt.hour)
    date = dec_to_bcd(dt.day)
    weekday_value = _python_weekday_to_rtc(dt.weekday(), rtc_type)
    try:
        if rtc_type == "pcf8563":
            month = dec_to_bcd(dt.month)
            year = dec_to_bcd(dt.year - 2000)
            payload = [second, minute, hour, date, weekday_value, month, year]
            bus.write_i2c_block_data(address, 0x02, payload)
        elif rtc_type == "ds3231":
            month_value = dec_to_bcd(dt.month)
            year_value = dt.year
            century_bit = 0
            if year_value >= 2100:
                century_bit = 0x80
                year_value -= 100
            year = dec_to_bcd(year_value - 2000)
            payload = [
                second,
                minute,
                hour,
                weekday_value & 0x07,
                date,
                month_value | century_bit,
                year,
            ]
            bus.write_i2c_block_data(address, 0x00, payload)
        else:  # pragma: no cover - abgesichert durch _determine_rtc_type
            raise UnsupportedRTCError(f"RTC-Typ '{rtc_type}' nicht unterstützt")
    except OSError as exc:
        raise RTCWriteError("Schreibzugriff auf die RTC ist fehlgeschlagen") from exc
    logging.info(f'RTC gesetzt auf {dt.strftime("%Y-%m-%d %H:%M:%S")}')


def _update_rtc_sync_status(success: bool, error: Optional[str] = None) -> None:
    RTC_SYNC_STATUS["success"] = success
    RTC_SYNC_STATUS["last_error"] = error


def sync_rtc_to_system() -> bool:
    try:
        rtc_time = read_rtc()
    except (ValueError, OSError, RTCUnavailableError, UnsupportedRTCError) as e:
        logging.warning(f"RTC-Sync übersprungen: {e}")
        _update_rtc_sync_status(False, str(e))
        return False

    set_time_value = rtc_time.strftime("%Y-%m-%d %H:%M:%S")
    date_command = privileged_command("timedatectl", "set-time", set_time_value)

    try:
        subprocess.run(
            date_command,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        primary_command = exc.filename or _extract_primary_command(date_command)
        logging.error(
            "RTC-Sync fehlgeschlagen: Kommando '%s' nicht gefunden (%s)",
            primary_command,
            exc,
        )
        _update_rtc_sync_status(
            False,
            f"Kommando '{primary_command}' nicht gefunden",
        )
        return False
    except subprocess.CalledProcessError as exc:
        failing_command = exc.cmd if exc.cmd else date_command
        primary_command = _extract_primary_command(failing_command or [])
        stderr_text = exc.stderr if isinstance(exc.stderr, str) else ""
        stdout_text = exc.stdout if isinstance(exc.stdout, str) else ""
        if _command_not_found(stderr_text, stdout_text, exc.returncode):
            logging.error(
                "RTC-Sync fehlgeschlagen: Kommando '%s' nicht gefunden (%s)",
                primary_command,
                stderr_text or exc,
            )
            _update_rtc_sync_status(
                False,
                f"Kommando '{primary_command}' nicht gefunden",
            )
        else:
            logging.error(
                "RTC-Sync fehlgeschlagen: Kommando %s lieferte Rückgabecode %s",
                " ".join(map(str, failing_command)),
                exc.returncode,
            )
            _update_rtc_sync_status(False, f"Rückgabecode {exc.returncode}")
        return False
    except Exception as exc:  # pragma: no cover - unerwartete Fehler
        logging.error("RTC-Sync fehlgeschlagen: %s", exc)
        _update_rtc_sync_status(False, str(exc))
        return False

    logging.info("RTC auf Systemzeit synchronisiert")
    _update_rtc_sync_status(True, None)
    return True


if not TESTING:
    sync_rtc_to_system()

# DB Setup
from contextlib import contextmanager, nullcontext


@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        yield conn, cursor
    finally:
        cursor.close()
        conn.close()


def _determine_initial_password_path() -> Path:
    base_dir = Path(DB_FILE).resolve().parent
    candidate = INITIAL_ADMIN_PASSWORD_FILE_ENV
    if candidate:
        path = Path(candidate)
        if not path.is_absolute():
            path = (base_dir / path).resolve()
    else:
        path = (base_dir / DEFAULT_INITIAL_PASSWORD_FILENAME).resolve()
    return path


def _write_initial_admin_password(password: str) -> Optional[Path]:
    target_path = _determine_initial_password_path()
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        mode = 0o600
        fd = os.open(target_path, flags, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(password)
            handle.write("\n")
        os.chmod(target_path, mode)
    except Exception as exc:  # pragma: no cover - sollte nicht auftreten
        logging.error(
            "Generiertes Initialpasswort konnte nicht in %s gespeichert werden: %s",
            target_path,
            exc,
        )
        app.config["INITIAL_ADMIN_PASSWORD_FILE"] = None
        return None

    app.config["INITIAL_ADMIN_PASSWORD_FILE"] = str(target_path)
    return target_path


AUTO_REBOOT_DEFAULTS = {
    "auto_reboot_enabled": "0",
    "auto_reboot_mode": "daily",
    "auto_reboot_time": "03:00",
    "auto_reboot_weekday": "monday",
}


@dataclass
class HardwareButtonConfigEntry:
    id: int
    gpio_pin: int
    action: str
    item_type: Optional[str]
    item_id: Optional[int]
    debounce_ms: int
    enabled: bool


def initialize_database():
    with get_db_connection() as (conn, cursor):
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                password TEXT,
                must_change_password INTEGER DEFAULT 0
            )
            """
        )
        cursor.execute("PRAGMA table_info(users)")
        user_columns = {row[1] for row in cursor.fetchall()}
        if "must_change_password" not in user_columns:
            cursor.execute(
                "ALTER TABLE users ADD COLUMN must_change_password INTEGER DEFAULT 0"
            )
        cursor.execute(
            "UPDATE users SET must_change_password = 0 WHERE must_change_password IS NULL"
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS audio_files (
                id INTEGER PRIMARY KEY,
                filename TEXT,
                duration_seconds REAL
            )
            """
        )
        cursor.execute("PRAGMA table_info(audio_files)")
        audio_columns = {row[1] for row in cursor.fetchall()}
        if "duration_seconds" not in audio_columns:
            cursor.execute("ALTER TABLE audio_files ADD COLUMN duration_seconds REAL")
            conn.commit()
        cursor.execute(
            "SELECT id, filename FROM audio_files WHERE duration_seconds IS NULL"
        )
        rows_without_duration = cursor.fetchall()
        for row in rows_without_duration:
            file_id, filename = row[0], row[1]
            file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            duration = None
            if os.path.exists(file_path):
                try:
                    sound = AudioSegment.from_file(file_path)
                    duration = len(sound) / 1000.0
                except Exception as exc:
                    logging.warning(
                        "Konnte Dauer für bestehende Datei %s nicht bestimmen: %s",
                        filename,
                        exc,
                    )
            if duration is not None:
                cursor.execute(
                    "UPDATE audio_files SET duration_seconds=? WHERE id=?",
                    (duration, file_id),
                )
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY,
                item_id INTEGER,
                item_type TEXT,
                time TEXT,
                repeat TEXT,
                delay INTEGER,
                start_date TEXT,
                end_date TEXT,
                day_of_month INTEGER,
                executed INTEGER DEFAULT 0,
                volume_percent INTEGER DEFAULT 100
            )"""
        )
        try:
            cursor.execute("ALTER TABLE schedules ADD COLUMN executed INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        cursor.execute("PRAGMA table_info(schedules)")
        schedule_columns = {row[1] for row in cursor.fetchall()}
        if "volume_percent" not in schedule_columns:
            try:
                cursor.execute(
                    "ALTER TABLE schedules ADD COLUMN volume_percent INTEGER DEFAULT 100"
                )
            except sqlite3.OperationalError:
                pass
            else:
                schedule_columns.add("volume_percent")
        if "volume_percent" in schedule_columns:
            cursor.execute(
                "UPDATE schedules SET volume_percent = 100 WHERE volume_percent IS NULL"
            )
        for column, column_type in (
            ("start_date", "TEXT"),
            ("end_date", "TEXT"),
            ("day_of_month", "INTEGER"),
        ):
            try:
                cursor.execute(f"ALTER TABLE schedules ADD COLUMN {column} {column_type}")
            except sqlite3.OperationalError:
                pass
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS playlists (id INTEGER PRIMARY KEY, name TEXT)"""
        )
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS playlist_files (playlist_id INTEGER, file_id INTEGER)"""
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS hardware_buttons (
                id INTEGER PRIMARY KEY,
                gpio_pin INTEGER UNIQUE,
                action TEXT,
                item_type TEXT,
                item_id INTEGER,
                debounce_ms INTEGER,
                enabled INTEGER
            )
            """
        )
        cursor.execute("PRAGMA table_info(hardware_buttons)")
        hardware_button_columns = {row[1] for row in cursor.fetchall()}
        if "gpio_pin" not in hardware_button_columns:
            cursor.execute("ALTER TABLE hardware_buttons ADD COLUMN gpio_pin INTEGER")
        if "action" not in hardware_button_columns:
            cursor.execute("ALTER TABLE hardware_buttons ADD COLUMN action TEXT")
        if "item_type" not in hardware_button_columns:
            cursor.execute("ALTER TABLE hardware_buttons ADD COLUMN item_type TEXT")
        if "item_id" not in hardware_button_columns:
            cursor.execute("ALTER TABLE hardware_buttons ADD COLUMN item_id INTEGER")
        if "debounce_ms" not in hardware_button_columns:
            cursor.execute(
                "ALTER TABLE hardware_buttons ADD COLUMN debounce_ms INTEGER"
            )
        if "enabled" not in hardware_button_columns:
            cursor.execute(
                "ALTER TABLE hardware_buttons ADD COLUMN enabled INTEGER DEFAULT 1"
            )
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_hardware_buttons_gpio_pin ON hardware_buttons (gpio_pin)"
        )
        cursor.execute(
            "UPDATE hardware_buttons SET enabled = 1 WHERE enabled IS NULL"
        )
        cursor.execute(
            "UPDATE hardware_buttons SET debounce_ms = ? WHERE debounce_ms IS NULL",
            (DEFAULT_BUTTON_DEBOUNCE_MS,),
        )
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"""
        )
        for key, value in AUTO_REBOOT_DEFAULTS.items():
            cursor.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (AMPLIFIER_GPIO_PIN_SETTING_KEY, str(DEFAULT_AMPLIFIER_GPIO_PIN)),
        )
        if not cursor.execute("SELECT * FROM users").fetchone():
            initial_password = os.environ.get("INITIAL_ADMIN_PASSWORD")
            generated_password = False
            if not initial_password:
                initial_password = secrets.token_urlsafe(16)
                generated_password = True
            hashed_password = generate_password_hash(initial_password)
            must_change_value = 1
            cursor.execute(
                "INSERT INTO users (username, password, must_change_password) VALUES (?, ?, ?)",
                ("admin", hashed_password, must_change_value),
            )
            if generated_password:
                password_file = _write_initial_admin_password(initial_password)
                if password_file is not None:
                    logging.warning(
                        "Initialpasswort für 'admin' generiert und unter %s abgelegt. "
                        "Bitte Datei nach Übernahme löschen oder sicher verwahren.",
                        password_file,
                    )
                else:
                    logging.error(
                        "Initialpasswort für 'admin' generiert, konnte aber nicht sicher "
                        "abgelegt werden. Bitte INITIAL_ADMIN_PASSWORD setzen oder das Passwort "
                        "manuell aktualisieren."
                    )
            else:
                logging.info(
                    "Initialpasswort für 'admin' aus Umgebungsvariable INITIAL_ADMIN_PASSWORD übernommen."
                )
        conn.commit()

    loader = globals().get("load_dac_sink_from_settings")
    if callable(loader):
        loader()


initialize_database()


hardware_button_config_lock = threading.Lock()
hardware_button_config: List[HardwareButtonConfigEntry] = []


def load_hardware_button_config() -> List[HardwareButtonConfigEntry]:
    global hardware_button_config

    with get_db_connection() as (conn, cursor):
        cursor.execute(
            """
            SELECT id, gpio_pin, action, item_type, item_id, debounce_ms, enabled
            FROM hardware_buttons
            ORDER BY gpio_pin
            """
        )
        rows = cursor.fetchall()

    entries: List[HardwareButtonConfigEntry] = []
    seen_pins: Set[int] = set()

    for row in rows:
        raw_pin = row["gpio_pin"]
        try:
            gpio_pin = int(raw_pin)
        except (TypeError, ValueError):
            logging.warning(
                "Hardware-Button #%s übersprungen: Ungültiger GPIO-Pin '%s'",
                row["id"],
                raw_pin,
            )
            continue

        if gpio_pin < 0:
            logging.warning(
                "Hardware-Button #%s übersprungen: GPIO-Pin %s darf nicht negativ sein",
                row["id"],
                gpio_pin,
            )
            continue

        if gpio_pin == GPIO_PIN_ENDSTUFE:
            logging.warning(
                "Hardware-Button #%s übersprungen: GPIO %s ist für die Endstufe reserviert",
                row["id"],
                gpio_pin,
            )
            continue

        if gpio_pin in seen_pins:
            logging.warning(
                "Hardware-Button-Konfiguration enthält doppelte Pinbelegung für GPIO %s",
                gpio_pin,
            )
            continue

        seen_pins.add(gpio_pin)

        action_raw = row["action"] or ""
        action = action_raw.strip().upper()
        if not action:
            logging.warning(
                "Hardware-Button #%s übersprungen: Keine Aktion konfiguriert",
                row["id"],
            )
            continue

        item_type_raw = row["item_type"] or None
        item_type = item_type_raw.strip().lower() if item_type_raw else None

        item_id_raw = row["item_id"]
        if item_id_raw is None:
            item_id: Optional[int] = None
        else:
            try:
                item_id = int(item_id_raw)
            except (TypeError, ValueError):
                logging.warning(
                    "Hardware-Button #%s: Ungültige Item-ID '%s' – Eintrag wird ignoriert",
                    row["id"],
                    item_id_raw,
                )
                continue

        debounce_raw = row["debounce_ms"]
        try:
            debounce_ms = (
                int(debounce_raw)
                if debounce_raw is not None
                else DEFAULT_BUTTON_DEBOUNCE_MS
            )
        except (TypeError, ValueError):
            logging.warning(
                "Hardware-Button #%s: Ungültige Entprellzeit '%s' – verwende %s ms",
                row["id"],
                debounce_raw,
                DEFAULT_BUTTON_DEBOUNCE_MS,
            )
            debounce_ms = DEFAULT_BUTTON_DEBOUNCE_MS

        if debounce_ms < 0:
            logging.warning(
                "Hardware-Button #%s: Negative Entprellzeit %s ms – verwende %s ms",
                row["id"],
                debounce_ms,
                DEFAULT_BUTTON_DEBOUNCE_MS,
            )
            debounce_ms = DEFAULT_BUTTON_DEBOUNCE_MS

        enabled_raw = row["enabled"]
        if enabled_raw is None:
            enabled = True
        else:
            try:
                enabled = int(enabled_raw) != 0
            except (TypeError, ValueError):
                enabled = bool(enabled_raw)

        entries.append(
            HardwareButtonConfigEntry(
                id=row["id"],
                gpio_pin=gpio_pin,
                action=action,
                item_type=item_type,
                item_id=item_id,
                debounce_ms=debounce_ms,
                enabled=enabled,
            )
        )

    with hardware_button_config_lock:
        hardware_button_config = list(entries)

    return list(entries)


def get_hardware_button_config() -> List[HardwareButtonConfigEntry]:
    with hardware_button_config_lock:
        if hardware_button_config:
            return list(hardware_button_config)

    return load_hardware_button_config()


def reload_hardware_button_config() -> List[HardwareButtonConfigEntry]:
    return load_hardware_button_config()


load_hardware_button_config()


if TESTING:

    class _TestingConnectionProxy:
        def __init__(self):
            self._storage = threading.local()

        def _get_connection(self):
            conn = getattr(self._storage, "conn", None)
            if conn is None:
                conn = sqlite3.connect(DB_FILE, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                self._storage.conn = conn
            return conn

        def __getattr__(self, item):
            conn = self._get_connection()
            return getattr(conn, item)

        def close(self):
            cursor = getattr(self._storage, "cursor", None)
            if cursor is not None:
                cursor.close()
                self._storage.cursor = None
            conn = getattr(self._storage, "conn", None)
            if conn is not None:
                conn.close()
                self._storage.conn = None


    class _TestingCursorProxy:
        def __init__(self, connection_proxy):
            self._connection_proxy = connection_proxy
            self._storage = threading.local()

        def _get_cursor(self):
            cursor = getattr(self._storage, "cursor", None)
            if cursor is None:
                cursor = self._connection_proxy._get_connection().cursor()
                self._storage.cursor = cursor
            return cursor

        def __getattr__(self, item):
            cursor = self._get_cursor()
            return getattr(cursor, item)

        def close(self):
            cursor = getattr(self._storage, "cursor", None)
            if cursor is not None:
                cursor.close()
                self._storage.cursor = None


    conn = _TestingConnectionProxy()
    cursor = _TestingCursorProxy(conn)
else:
    conn = None
    cursor = None

# Scheduler
LOCAL_TZ = datetime.now().astimezone().tzinfo
scheduler = BackgroundScheduler(timezone=LOCAL_TZ)
_BACKGROUND_SERVICES_LOCK = threading.RLock()
_BACKGROUND_SERVICES_STARTED = False
AUTO_REBOOT_JOB_ID = "auto_reboot_job"
AUTO_REBOOT_MISFIRE_GRACE_SECONDS = 300
AUTO_REBOOT_WEEKDAYS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]


def _ensure_local_timezone(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=LOCAL_TZ)
    return dt


def _to_local_aware(dt):
    dt = _ensure_local_timezone(dt)
    if dt is None:
        return None
    try:
        return dt.astimezone(LOCAL_TZ)
    except ValueError:
        return dt


def _to_local_naive(dt):
    aware_dt = _to_local_aware(dt)
    if aware_dt is None:
        return None
    return aware_dt.replace(tzinfo=None)


def _format_schedule_time_for_display(time_str, repeat):
    if repeat != "once":
        return time_str
    try:
        run_dt = parse_once_datetime(time_str)
    except (TypeError, ValueError):
        return time_str
    local_dt = _to_local_aware(run_dt)
    if local_dt is None:
        return time_str
    return local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")


def get_setting(key, default=None):
    with get_db_connection() as (conn, cursor):
        row = cursor.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row:
        return row[0]
    if key in AUTO_REBOOT_DEFAULTS:
        default_value = AUTO_REBOOT_DEFAULTS[key]
        set_setting(key, default_value)
        return default_value
    return default


def set_setting(key, value):
    with get_db_connection() as (conn, cursor):
        cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, "" if value is None else str(value)),
        )
        conn.commit()


def _parse_amplifier_gpio_pin(raw_value: Optional[str]) -> Optional[int]:
    if raw_value is None:
        return None
    normalized = str(raw_value).strip()
    if not normalized:
        return None
    try:
        candidate = int(normalized, 0)
    except (TypeError, ValueError):
        return None
    if candidate < 0:
        return None
    return candidate


def load_amplifier_gpio_pin_from_settings(*, log_source: bool = False) -> int:
    global GPIO_PIN_ENDSTUFE, CONFIGURED_AMPLIFIER_GPIO_PIN

    raw_value = get_setting(AMPLIFIER_GPIO_PIN_SETTING_KEY, None)
    parsed = _parse_amplifier_gpio_pin(raw_value)
    previous_pin = GPIO_PIN_ENDSTUFE

    if parsed is None:
        if raw_value not in (None, "") and log_source:
            logging.warning(
                "Ungültiger Verstärker-Pin '%s' in den Einstellungen – verwende GPIO%s.",
                raw_value,
                DEFAULT_AMPLIFIER_GPIO_PIN,
            )
        GPIO_PIN_ENDSTUFE = DEFAULT_AMPLIFIER_GPIO_PIN
        CONFIGURED_AMPLIFIER_GPIO_PIN = None
        if log_source and previous_pin != GPIO_PIN_ENDSTUFE:
            logging.info(
                "Verstärker-Pin auf Standard GPIO%s zurückgesetzt", GPIO_PIN_ENDSTUFE
            )
    else:
        GPIO_PIN_ENDSTUFE = parsed
        if parsed == DEFAULT_AMPLIFIER_GPIO_PIN and str(parsed) == str(raw_value).strip():
            CONFIGURED_AMPLIFIER_GPIO_PIN = None
        else:
            CONFIGURED_AMPLIFIER_GPIO_PIN = parsed
        if log_source and previous_pin != parsed:
            logging.info("Verstärker-Pin aus Einstellungen geladen: GPIO%s", parsed)

    return GPIO_PIN_ENDSTUFE


def get_amplifier_gpio_pin_state() -> dict:
    source = "settings" if CONFIGURED_AMPLIFIER_GPIO_PIN is not None else "default"
    return {
        "pin": GPIO_PIN_ENDSTUFE,
        "configured": CONFIGURED_AMPLIFIER_GPIO_PIN,
        "default": DEFAULT_AMPLIFIER_GPIO_PIN,
        "source": source,
    }


def _parse_headroom_value(raw_value: Optional[str], source: str) -> Optional[float]:
    if raw_value is None:
        return None
    normalized = str(raw_value).strip()
    if not normalized:
        return None
    try:
        parsed = float(normalized)
    except (TypeError, ValueError):
        logging.warning(
            "Ungültiger Headroom-Wert '%s' aus %s. Wert wird ignoriert.",
            raw_value,
            source,
        )
        return None
    if not math.isfinite(parsed):
        logging.warning(
            "Nicht-endlicher Headroom-Wert '%s' aus %s. Wert wird ignoriert.",
            raw_value,
            source,
        )
        return None
    return parsed


def _sanitize_headroom_value(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    if not math.isfinite(value):
        logging.warning(
            "Nicht-endlicher Headroom-Wert '%s' nach Normalisierung. Wert wird verworfen.",
            value,
        )
        return None
    return abs(value)


def get_normalization_headroom_details() -> dict:
    stored_raw = get_setting(NORMALIZATION_HEADROOM_SETTING_KEY, None)
    env_raw = os.environ.get(NORMALIZATION_HEADROOM_ENV_KEY)

    stored_value = _sanitize_headroom_value(
        _parse_headroom_value(
            stored_raw, f"Einstellung '{NORMALIZATION_HEADROOM_SETTING_KEY}'"
        )
    )
    env_value = _sanitize_headroom_value(
        _parse_headroom_value(
            env_raw, f"Umgebungsvariable {NORMALIZATION_HEADROOM_ENV_KEY}"
        )
    )

    if env_raw is not None and env_value is not None:
        value = env_value
        source = "environment"
    elif env_raw is not None and env_value is None:
        if stored_value is not None:
            value = stored_value
            source = "settings"
        else:
            value = DEFAULT_NORMALIZATION_HEADROOM_DB
            source = "default"
    elif stored_value is not None:
        value = stored_value
        source = "settings"
    else:
        value = DEFAULT_NORMALIZATION_HEADROOM_DB
        source = "default"

    value = _sanitize_headroom_value(value)

    return {
        "value": value,
        "source": source,
        "env_raw": env_raw,
        "stored_raw": stored_raw,
        "stored_value": stored_value,
        "env_value": env_value,
    }


def get_normalization_headroom_db() -> float:
    details = get_normalization_headroom_details()
    return float(details["value"])


@dataclass(frozen=True)
class BluetoothVolumeCap:
    percent: int
    headroom_db: float


def get_bluetooth_volume_cap_percent() -> BluetoothVolumeCap:
    """Ermittelt den maximalen Lautstärke-Prozentwert und Headroom für Bluetooth-Sinks.

    PulseAudio interpretiert Prozentwerte nicht linear, sondern nutzt eine
    kubische Skala: "100 %" entspricht einem linearen Amplitudenfaktor von 1,
    der Prozentwert ändert aber den unterliegenden Wert, bevor PulseAudio ihn
    potenziert. Damit ein Headroom-Wert in Dezibel weiterhin dem gewünschten
    linearen Amplitudenfaktor entspricht, wird deshalb die kubische Wurzel
    gebildet."""

    headroom_raw = float(get_normalization_headroom_db())
    if not math.isfinite(headroom_raw):
        return BluetoothVolumeCap(percent=100, headroom_db=0.0)

    headroom_db = max(0.0, headroom_raw)
    if headroom_db <= 0:
        return BluetoothVolumeCap(percent=100, headroom_db=0.0)

    ratio = 10 ** (-headroom_db / 20)
    # PulseAudio-Anteile werden kubisch umgesetzt, daher ist die dritte Wurzel
    # nötig, damit die effektive Lautstärke dem linearen Amplitudenverhältnis
    # (10 ** (-headroom_db / 20)) entspricht.
    percent_float = (ratio ** (1 / 3)) * 100
    percent = int(math.floor(percent_float))
    percent_clamped = max(1, min(100, percent))
    return BluetoothVolumeCap(percent=percent_clamped, headroom_db=headroom_db)


def _parse_schedule_volume_percent(raw_value: Optional[str]) -> Optional[int]:
    if raw_value is None:
        return None
    normalized = str(raw_value).strip()
    if not normalized:
        return None
    if normalized.endswith("%"):
        normalized = normalized[:-1].strip()
    try:
        numeric = float(normalized)
    except (TypeError, ValueError):
        logging.warning(
            "Ungültiger Standard-Lautstärke-Prozentwert '%s' in den Einstellungen. Fallback auf Standard.",
            raw_value,
        )
        return None
    if not math.isfinite(numeric):
        return None
    percent = int(round(numeric))
    return max(SCHEDULE_VOLUME_PERCENT_MIN, min(SCHEDULE_VOLUME_PERCENT_MAX, percent))


def _parse_schedule_volume_db(raw_value: Optional[str]) -> Optional[float]:
    if raw_value is None:
        return None
    normalized = str(raw_value).strip().lower()
    if not normalized:
        return None
    if normalized.endswith("db"):
        normalized = normalized[:-2].strip()
    try:
        value = float(normalized)
    except (TypeError, ValueError):
        logging.warning(
            "Ungültiger Standard-Lautstärke-dB-Wert '%s' in den Einstellungen. Fallback auf Standard.",
            raw_value,
        )
        return None
    if not math.isfinite(value):
        return None
    return value


def _convert_schedule_volume_db_to_percent(db_value: float) -> int:
    ratio = 10 ** (db_value / 20.0)
    percent = int(round(ratio * 100))
    return max(SCHEDULE_VOLUME_PERCENT_MIN, min(SCHEDULE_VOLUME_PERCENT_MAX, percent))


def get_schedule_default_volume_details() -> dict:
    percent_raw = get_setting(SCHEDULE_VOLUME_PERCENT_SETTING_KEY, None)
    percent_value = _parse_schedule_volume_percent(percent_raw)
    if percent_value is not None:
        return {
            "percent": percent_value,
            "source": "settings_percent",
            "raw_percent": percent_raw,
            "raw_db": None,
            "db_value": None,
        }

    db_raw = get_setting(SCHEDULE_VOLUME_DB_SETTING_KEY, None)
    db_value = _parse_schedule_volume_db(db_raw)
    if db_value is not None:
        return {
            "percent": _convert_schedule_volume_db_to_percent(db_value),
            "source": "settings_db",
            "raw_percent": percent_raw,
            "raw_db": db_raw,
            "db_value": db_value,
        }

    return {
        "percent": SCHEDULE_DEFAULT_VOLUME_PERCENT_FALLBACK,
        "source": "default",
        "raw_percent": percent_raw,
        "raw_db": db_raw,
        "db_value": None,
    }


def get_schedule_default_volume_percent() -> int:
    details = get_schedule_default_volume_details()
    return details["percent"]


def _normalize_optional(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _load_configured_dac_label() -> Optional[str]:
    env_label = _normalize_optional(os.environ.get("DAC_SINK_LABEL"))
    if env_label:
        return env_label
    stored_label = get_setting(DAC_SINK_LABEL_SETTING_KEY, None)
    return _normalize_optional(stored_label)


def _gather_dac_sink_state() -> dict:
    default_sink = _refresh_default_dac_sink()
    env_value = _normalize_optional(os.environ.get("DAC_SINK_NAME"))
    stored_raw = get_setting(DAC_SINK_SETTING_KEY, None)
    stored_value = _normalize_optional(stored_raw)

    if env_value:
        sink_hint = env_value
        source = "env"
    elif stored_value:
        sink_hint = stored_value
        source = "settings"
    else:
        sink_hint = default_sink
        source = "default"

    resolver = globals().get("_resolve_sink_name")
    if callable(resolver):
        resolved_sink = resolver(sink_hint) or sink_hint
    else:
        resolved_sink = sink_hint

    return {
        "default": default_sink,
        "hint": sink_hint,
        "resolved": resolved_sink,
        "configured": stored_value,
        "source": source,
        "raw_setting": stored_raw,
    }


def _apply_dac_sink_state(state: dict, *, reset_detection: bool) -> None:
    global DAC_SINK, DAC_SINK_HINT, CONFIGURED_DAC_SINK, DAC_SINK_LABEL

    previous_sink = DAC_SINK
    DAC_SINK_HINT = state["hint"]
    DAC_SINK = state["resolved"]
    CONFIGURED_DAC_SINK = state["configured"]

    if DAC_SINK != previous_sink:
        logging.info("DAC-Sink aktualisiert: %s", DAC_SINK)

    DAC_SINK_LABEL = _load_configured_dac_label()

    if reset_detection:
        audio_status["dac_sink_detected"] = None


def _refresh_dac_sink_state(*, reset_detection: bool, log_source: bool) -> None:
    state = _gather_dac_sink_state()

    if log_source:
        source = state["source"]
        sink_hint = state["hint"]
        if source == "env":
            logging.info("DAC_SINK_NAME aus Umgebungsvariable übernommen: %s", sink_hint)
        elif source == "settings":
            logging.info("DAC-Sink aus Einstellungen geladen: %s", sink_hint)
        else:
            if state["raw_setting"] is None:
                logging.info(
                    "Kein gespeicherter DAC-Sink gefunden. Verwende Standard: %s",
                    state["default"],
                )

    _apply_dac_sink_state(state, reset_detection=reset_detection)


def load_dac_sink_from_settings():
    _refresh_dac_sink_state(reset_detection=True, log_source=False)


def _parse_rtc_address_string(value: Optional[str]) -> Tuple[int, ...]:
    if not value:
        return tuple()
    parts = value.replace(";", ",").split(",")
    parsed: List[int] = []
    for raw_part in parts:
        part = raw_part.strip()
        if not part:
            continue
        try:
            address = int(part, 0)
        except ValueError as exc:
            raise ValueError(f"Ungültige I²C-Adresse: {part}") from exc
        if address < 0 or address > 0x7F:
            raise ValueError(
                f"I²C-Adresse {part} außerhalb des gültigen Bereichs (0x00-0x7F)"
            )
        if address not in parsed:
            parsed.append(address)
    return tuple(parsed)


def _format_rtc_addresses(addresses: Iterable[int]) -> str:
    return ", ".join(f"0x{address:02X}" for address in _normalize_rtc_addresses(addresses))


def load_rtc_configuration_from_settings():
    global RTC_FORCED_TYPE

    module_type = (get_setting(RTC_MODULE_SETTING_KEY, "auto") or "auto").strip().lower()
    if module_type not in RTC_SUPPORTED_TYPES:
        logging.warning(
            "Unbekannter RTC-Modultyp '%s' in den Einstellungen. Fallback auf Auto.",
            module_type,
        )
        module_type = "auto"

    RTC_FORCED_TYPE = None if module_type == "auto" else module_type

    raw_addresses = get_setting(RTC_ADDRESS_SETTING_KEY, "")
    try:
        configured_addresses = _parse_rtc_address_string(raw_addresses)
    except ValueError as exc:
        logging.warning("RTC-Adressen aus Einstellungen konnten nicht geparst werden: %s", exc)
        configured_addresses = tuple()

    if configured_addresses:
        candidate_addresses = configured_addresses
    else:
        candidate_addresses = RTC_SUPPORTED_TYPES[module_type]["default_addresses"]

    refresh_rtc_detection(candidate_addresses)


def get_rtc_configuration_state() -> dict:
    module_type = (get_setting(RTC_MODULE_SETTING_KEY, "auto") or "auto").strip().lower()
    if module_type not in RTC_SUPPORTED_TYPES:
        module_type = "auto"
    raw_addresses = get_setting(RTC_ADDRESS_SETTING_KEY, "") or ""
    try:
        configured_addresses = _parse_rtc_address_string(raw_addresses)
    except ValueError:
        configured_addresses = tuple()
    return {
        "module": module_type,
        "module_label": RTC_SUPPORTED_TYPES[module_type]["label"],
        "configured_addresses": configured_addresses,
        "configured_addresses_raw": raw_addresses,
        "effective_addresses": RTC_CANDIDATE_ADDRESSES,
        "configured_addresses_display": _format_rtc_addresses(configured_addresses)
        if configured_addresses
        else "",
        "effective_addresses_display": _format_rtc_addresses(
            RTC_CANDIDATE_ADDRESSES
        ),
    }


load_amplifier_gpio_pin_from_settings(log_source=True)
load_dac_sink_from_settings()
load_rtc_configuration_from_settings()


RELAY_INVERT = get_setting("relay_invert", "0") == "1"
AMP_ON_LEVEL = 0 if RELAY_INVERT else 1
AMP_OFF_LEVEL = 1 if RELAY_INVERT else 0


def update_amp_levels():
    global AMP_ON_LEVEL, AMP_OFF_LEVEL
    AMP_ON_LEVEL = 0 if RELAY_INVERT else 1
    AMP_OFF_LEVEL = 1 if RELAY_INVERT else 0


def _set_amp_output(level, *, keep_claimed=None):
    """Schreibt einen GPIO-Pegel und berücksichtigt den Claim-Zustand."""

    global amplifier_claimed
    if keep_claimed is None:
        keep_claimed = amplifier_claimed

    if not GPIO_AVAILABLE:
        logging.warning(
            "lgpio nicht verfügbar, überspringe Setzen des Endstufenpegels"
        )
        return False

    if gpio_handle is None:
        logging.warning(
            "GPIO-Handle nicht verfügbar, überspringe Setzen des Endstufenpegels"
        )
        return False

    try:
        if amplifier_claimed:
            GPIO.gpio_write(gpio_handle, GPIO_PIN_ENDSTUFE, level)
            if not keep_claimed:
                GPIO.gpio_free(gpio_handle, GPIO_PIN_ENDSTUFE)
                amplifier_claimed = False
            return True

        GPIO.gpio_claim_output(
            gpio_handle, GPIO_PIN_ENDSTUFE, lFlags=0, level=level
        )
        GPIO.gpio_write(gpio_handle, GPIO_PIN_ENDSTUFE, level)
        if keep_claimed:
            amplifier_claimed = True
        else:
            GPIO.gpio_free(gpio_handle, GPIO_PIN_ENDSTUFE)
        return True
    except GPIOError as e:
        if "GPIO busy" in str(e):
            logging.warning(
                "GPIO busy beim Setzen des Endstufenpegels, Aktion wird übersprungen"
            )
            if amplifier_claimed and not keep_claimed:
                try:
                    GPIO.gpio_free(gpio_handle, GPIO_PIN_ENDSTUFE)
                except GPIOError:
                    pass
                amplifier_claimed = False
            return False
        raise


class User(UserMixin):
    def __init__(self, id, username, must_change_password=False):
        self.id = id
        self.username = username
        self.must_change_password = bool(must_change_password)


@login_manager.user_loader
def load_user(user_id):
    with get_db_connection() as (conn, cursor):
        cursor.execute("SELECT * FROM users WHERE id=?", (user_id,))
        user_data = cursor.fetchone()
        if user_data:
            columns = set(user_data.keys())
            must_change_value = (
                user_data["must_change_password"]
                if "must_change_password" in columns
                else 0
            )
            return User(
                user_data["id"],
                user_data["username"],
                must_change_value,
            )
        return None


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def validate_time(time_str):
    try:
        datetime.strptime(time_str, "%H:%M:%S")
        return True
    except ValueError:
        return False


def parse_once_datetime(time_str):
    """Parst einen 'once'-Zeitstempel mit verschiedenen Formaten."""
    if not time_str:
        raise ValueError("Leerer Zeitstempel für 'once'-Zeitplan")

    normalized = time_str.strip()
    iso_candidates = [normalized]
    if normalized.endswith("Z"):
        iso_candidates.append(f"{normalized[:-1]}+00:00")

    for candidate in iso_candidates:
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue

    relaxed = normalized.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(relaxed, fmt)
        except ValueError:
            continue

    raise ValueError(f"Ungültige Zeitangabe: {time_str}")


def parse_schedule_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        logging.warning(f"Ungültiges Datumsformat für Schedule: {date_str}")
        return None


def calculate_first_monthly_occurrence(start_date: date, day_of_month: int) -> date:
    """Bestimmt das erste zulässige Ausführungsdatum für einen monatlichen Zeitplan."""
    if not 1 <= day_of_month <= 31:
        raise ValueError("Ungültiger Tag im Monat")
    year = start_date.year
    month = start_date.month
    while True:
        days_in_month = calendar.monthrange(year, month)[1]
        if day_of_month <= days_in_month:
            candidate = date(year, month, day_of_month)
            if candidate >= start_date:
                return candidate
        month += 1
        if month > 12:
            month = 1
            year += 1
        start_date = date(year, month, 1)


def _get_item_duration(cursor, item_type, item_id):
    lookup_id = item_id
    try:
        lookup_id = int(item_id)
    except (TypeError, ValueError):
        pass
    if item_type == "file":
        cursor.execute(
            "SELECT duration_seconds FROM audio_files WHERE id=?",
            (lookup_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return row["duration_seconds"]
    if item_type == "playlist":
        cursor.execute(
            """
            SELECT SUM(f.duration_seconds) AS total_duration
            FROM playlist_files pf
            JOIN audio_files f ON pf.file_id = f.id
            WHERE pf.playlist_id=?
            """,
            (lookup_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return row["total_duration"]
    return None


def _schedule_interval_on_date(
    schedule_data, duration_seconds, target_date, include_adjacent=False
):
    def _interval_for_date(effective_date):
        if duration_seconds is None:
            return None
        try:
            duration = float(duration_seconds)
        except (TypeError, ValueError):
            return None
        if duration <= 0:
            return None
        repeat = schedule_data.get("repeat")
        try:
            delay_seconds = int(schedule_data.get("delay", 0))
        except (TypeError, ValueError):
            delay_seconds = 0
        start_date_obj = parse_schedule_date(schedule_data.get("start_date"))
        end_date_obj = parse_schedule_date(schedule_data.get("end_date"))
        if repeat == "once":
            try:
                run_dt = parse_once_datetime(schedule_data.get("time"))
            except (TypeError, ValueError):
                return None
            local_run_dt = _to_local_aware(run_dt)
            if local_run_dt is None:
                return None
            if local_run_dt.date() != effective_date:
                return None
            start_dt = local_run_dt + timedelta(seconds=delay_seconds)
            end_dt = start_dt + timedelta(seconds=duration)
            return _to_local_naive(start_dt), _to_local_naive(end_dt)
        if start_date_obj and effective_date < start_date_obj:
            return None
        if end_date_obj and effective_date > end_date_obj:
            return None
        try:
            base_time = datetime.strptime(schedule_data.get("time"), "%H:%M:%S").time()
        except (TypeError, ValueError):
            return None
        if repeat == "monthly":
            day_of_month = schedule_data.get("day_of_month")
            if day_of_month is None and start_date_obj is not None:
                day_of_month = start_date_obj.day
            try:
                day_of_month = int(day_of_month)
            except (TypeError, ValueError):
                return None
            if effective_date.day != day_of_month:
                return None
        base_dt = datetime.combine(effective_date, base_time)
        start_dt = base_dt + timedelta(seconds=delay_seconds)
        end_dt = start_dt + timedelta(seconds=duration)
        return start_dt, end_dt

    def _interval_overlaps_date(interval, reference_date):
        if interval is None:
            return False
        day_start = datetime.combine(reference_date, datetime.min.time())
        day_end = day_start + timedelta(days=1)
        start_dt, end_dt = interval
        return start_dt < day_end and end_dt > day_start

    if not include_adjacent:
        return _interval_for_date(target_date)

    intervals = []
    seen_intervals = set()
    for offset in (-1, 0, 1):
        effective_date = target_date + timedelta(days=offset)
        interval = _interval_for_date(effective_date)
        if interval is None:
            continue
        if not _interval_overlaps_date(interval, target_date):
            continue
        if interval not in seen_intervals:
            seen_intervals.add(interval)
            intervals.append(interval)
    return intervals


def _intervals_overlap(interval_a, interval_b):
    start_a, end_a = interval_a
    start_b, end_b = interval_b
    return start_a < end_b and start_b < end_a


def _get_first_occurrence_date(schedule_data):
    repeat = schedule_data.get("repeat")
    if repeat == "once":
        try:
            run_dt = parse_once_datetime(schedule_data.get("time"))
        except (TypeError, ValueError):
            return None
        local_run_dt = _to_local_aware(run_dt)
        if local_run_dt is None:
            return None
        return local_run_dt.date()
    start_date_obj = parse_schedule_date(schedule_data.get("start_date"))
    if repeat == "monthly" and start_date_obj is not None:
        day_of_month = schedule_data.get("day_of_month")
        if day_of_month is None:
            day_of_month = start_date_obj.day
        try:
            day_of_month = int(day_of_month)
        except (TypeError, ValueError):
            return None
        try:
            return calculate_first_monthly_occurrence(start_date_obj, day_of_month)
        except ValueError:
            return None
    return start_date_obj


def _has_schedule_conflict(cursor, new_schedule_data, new_duration_seconds, new_first_date):
    if new_duration_seconds is None:
        return False
    try:
        duration_value = float(new_duration_seconds)
    except (TypeError, ValueError):
        return False
    if duration_value <= 0:
        return False
    cursor.execute(
        """
        SELECT item_id, item_type, time, repeat, delay, start_date, end_date, day_of_month, executed
        FROM schedules
        """
    )
    existing_rows = cursor.fetchall()
    duration_cache = {}
    base_dates = set()
    if new_first_date is not None:
        base_dates.add(new_first_date)
    for row in existing_rows:
        schedule = dict(row)
        if schedule.get("repeat") == "once" and schedule.get("executed"):
            continue
        key = (schedule.get("item_type"), schedule.get("item_id"))
        if key not in duration_cache:
            duration_cache[key] = _get_item_duration(
                cursor, schedule.get("item_type"), schedule.get("item_id")
            )
        existing_duration = duration_cache[key]
        if existing_duration is None:
            continue
        try:
            existing_duration_value = float(existing_duration)
        except (TypeError, ValueError):
            continue
        if existing_duration_value <= 0:
            continue
        relevant_dates = set(base_dates)
        first_date = _get_first_occurrence_date(schedule)
        if first_date is not None:
            relevant_dates.add(first_date)
        if schedule.get("repeat") == "once":
            try:
                run_dt = parse_once_datetime(schedule.get("time"))
            except (TypeError, ValueError):
                run_dt = None
            if run_dt is not None:
                local_run_dt = _to_local_aware(run_dt)
                if local_run_dt is not None:
                    relevant_dates.add(local_run_dt.date())
        for candidate_date in relevant_dates:
            new_intervals = _schedule_interval_on_date(
                new_schedule_data,
                duration_value,
                candidate_date,
                include_adjacent=True,
            )
            if not new_intervals:
                continue
            existing_intervals = _schedule_interval_on_date(
                schedule,
                existing_duration_value,
                candidate_date,
                include_adjacent=True,
            )
            if not existing_intervals:
                continue
            for new_interval in new_intervals:
                for existing_interval in existing_intervals:
                    if _intervals_overlap(new_interval, existing_interval):
                        return True
    return False


def is_within_schedule_range(start_date_str, end_date_str, reference=None):
    reference_date = (reference or datetime.now()).date()
    start_date = parse_schedule_date(start_date_str)
    end_date = parse_schedule_date(end_date_str)
    if start_date and reference_date < start_date:
        return False
    if end_date and reference_date > end_date:
        return False
    return True


# PulseAudio
def get_current_sink():
    output = _run_pactl_command("get-default-sink")
    if not output:
        return "Nicht verfügbar"
    return output.splitlines()[0]


def _list_pulse_sinks():
    try:
        output = subprocess.check_output(
            ["pactl", "list", "short", "sinks"],
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        logging.warning("Konnte PulseAudio-Sinks nicht abfragen: %s", exc)
        return []
    sinks = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            sinks.append(parts[1])
    return sinks


def _sink_matches_hint(sink_name: str, hint: str) -> bool:
    if not sink_name or not hint:
        return False
    if sink_name == hint:
        return True
    if hint.startswith("pattern:"):
        return fnmatch.fnmatch(sink_name, hint[len("pattern:") :])
    if hint.startswith("regex:"):
        pattern = hint[len("regex:") :]
        try:
            return re.search(pattern, sink_name) is not None
        except re.error as exc:
            logging.warning("Ungültiges Regex-Muster für PulseAudio-Sink '%s': %s", hint, exc)
            return False
    if any(ch in hint for ch in "*?[]"):
        return fnmatch.fnmatch(sink_name, hint)
    return False


def _resolve_sink_name(sink_hint: str, *, sinks=None):
    if not sink_hint:
        return None
    hint = sink_hint.strip()
    if not hint:
        return None
    if sinks is None:
        sinks = _list_pulse_sinks()
    if hint in sinks:
        return hint
    if hint.startswith("pattern:"):
        pattern = hint[len("pattern:") :]
        for sink in sinks:
            if fnmatch.fnmatch(sink, pattern):
                return sink
        return None
    if hint.startswith("regex:"):
        pattern = hint[len("regex:") :]
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            logging.warning("Ungültiges Regex-Muster '%s': %s", pattern, exc)
            return None
        for sink in sinks:
            if regex.search(sink):
                return sink
        return None
    if any(ch in hint for ch in "*?[]"):
        for sink in sinks:
            if fnmatch.fnmatch(sink, hint):
                return sink
        return None
    for sink in sinks:
        if hint in sink:
            return sink
    return None


def _is_sink_available(sink_name):
    sinks = _list_pulse_sinks()
    resolved = _resolve_sink_name(sink_name, sinks=sinks)
    return resolved is not None


def set_sink(sink_name):
    global DAC_SINK

    sinks = _list_pulse_sinks()
    target_name = sink_name or DAC_SINK
    if not target_name:
        logging.warning("Kein Ziel-Sink angegeben und kein Standard konfiguriert.")
        return False

    resolved = _resolve_sink_name(target_name, sinks=sinks)
    if resolved is None:
        if _sink_is_configured(target_name):
            audio_status["dac_sink_detected"] = False
        logging.warning(
            "Sink '%s' nicht gefunden. Behalte aktuellen Standardsink bei.",
            target_name,
        )
        return False

    try:
        exit_code = subprocess.call(["pactl", "set-default-sink", resolved])
    except (FileNotFoundError, OSError) as exc:
        logging.warning(
            "PulseAudio-Sink konnte nicht gesetzt werden, 'pactl' fehlt oder ist nicht aufrufbar: %s",
            exc,
        )
        audio_status["dac_sink_detected"] = False
        if has_request_context():
            _notify_audio_unavailable("PulseAudio-Sink konnte nicht gesetzt werden")
        return False
    if exit_code != 0:
        logging.warning(
            "PulseAudio-Sink konnte nicht gesetzt werden, 'pactl' lieferte Exit-Code %s",
            exit_code,
        )
        audio_status["dac_sink_detected"] = False
        if has_request_context():
            _notify_audio_unavailable("PulseAudio-Sink konnte nicht gesetzt werden")
        return False
    if _sink_is_configured(resolved):
        DAC_SINK = resolved
        audio_status["dac_sink_detected"] = True
    logging.info("Switch zu Sink: %s", resolved)
    return True


def load_dac_sink_configuration():
    _refresh_dac_sink_state(reset_detection=False, log_source=True)


load_dac_sink_configuration()


def _sink_is_configured(sink_name: str) -> bool:
    if not sink_name:
        return False
    if sink_name == DAC_SINK:
        return True
    return _sink_matches_hint(sink_name, DAC_SINK_HINT)


def _describe_command(args: Sequence[str]) -> str:
    if not args:
        return "<unbekannt>"
    if args[0] == "sudo" and len(args) > 1:
        return " ".join(args[1:])
    return " ".join(args)


def _extract_primary_command(args: Sequence[str]) -> str:
    if not args:
        return "<unbekannt>"
    if args[0] == "sudo" and len(args) > 1:
        return args[1]
    return args[0]


_COMMAND_NOT_FOUND_PATTERNS: Tuple[str, ...] = (
    "command not found",
    "befehl nicht gefunden",
    "kommando nicht gefunden",
    "commande introuvable",
    "comando non trovato",
    "comando no encontrado",
    "comando não encontrado",
)


def _contains_command_not_found_message(*outputs: Optional[str]) -> bool:
    for output in outputs:
        if not isinstance(output, str):
            continue
        normalized = output.strip().lower()
        if not normalized:
            continue
        if any(pattern in normalized for pattern in _COMMAND_NOT_FOUND_PATTERNS):
            return True
    return False


def _command_not_found(stderr: Optional[str], stdout: Optional[str], returncode: Optional[int]) -> bool:
    if _contains_command_not_found_message(stderr, stdout):
        return True
    return returncode == 127


def _run_wifi_tool(
    args,
    fallback_message,
    log_context,
    *,
    flash_on_error=False,
):
    command_display = _describe_command(args)
    primary_command = _extract_primary_command(args)
    try:
        result = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        logging.error(
            "%s nicht gefunden: %s (%s)",
            log_context,
            primary_command,
            exc,
        )
        if flash_on_error:
            flash(fallback_message, "error")
        return False, fallback_message

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()

    if _command_not_found(stderr, stdout, result.returncode):
        logging.error(
            "%s: Kommando '%s' nicht gefunden",
            log_context,
            primary_command,
        )
        if flash_on_error:
            flash(fallback_message, "error")
        return False, fallback_message

    fail_indicator = False
    if primary_command == "wpa_cli":
        fail_indicator = any(
            line.strip().upper().startswith("FAIL") for line in stdout.splitlines()
        )

    if result.returncode != 0 or fail_indicator:
        combined_output = "\n".join(filter(None, [stdout, stderr])) or "Keine Ausgabe"
        logging.error(
            "%s fehlgeschlagen (Exit-Code %s): %s (Kommando: %s)",
            log_context,
            result.returncode,
            combined_output,
            command_display,
        )
        if flash_on_error:
            flash(fallback_message, "error")
        return False, fallback_message

    return True, stdout


def gather_status():
    if app.testing and has_request_context():
        success = False
        wlan_output = "Nicht verfügbar (Testmodus)"
    else:
        success, wlan_output = _run_wifi_tool(
            ["iwgetid", "wlan0", "-r"],
            "Nicht verfügbar (iwgetid fehlt)",
            "iwgetid für WLAN-Status",
        )
    if success:
        wlan_ssid = wlan_output or "Nicht verbunden"
    else:
        wlan_ssid = wlan_output
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    volume_output = _run_pactl_command("get-sink-volume", "@DEFAULT_SINK@")
    current_volume = "Unbekannt"
    if volume_output:
        match = re.search(r"(\d+)%", volume_output)
        if match:
            current_volume = f"{match.group(1)}%"
    if audio_status.get("dac_sink_detected") is None and DAC_SINK:
        audio_status["dac_sink_detected"] = _is_sink_available(DAC_SINK)

    effective_label = DAC_SINK_LABEL or DEFAULT_DAC_SINK_LABEL
    target_dac_sink = DAC_SINK or DAC_SINK_HINT
    is_playing = pygame.mixer.music.get_busy() if pygame_available else False
    headroom_details = get_normalization_headroom_details()
    schedule_default_volume = get_schedule_default_volume_details()
    amplifier_state = get_amplifier_gpio_pin_state()
    network_info = _load_network_settings_for_template("wlan0")
    network_mode = (network_info.get("mode") or "dhcp").strip().lower()
    if network_mode not in {"manual", "dhcp"}:
        network_mode = "dhcp"
    if network_mode == "manual" and network_info.get("ipv4_address"):
        ipv4_address = network_info.get("ipv4_address", "").strip()
        ipv4_prefix = network_info.get("ipv4_prefix", "").strip()
        if ipv4_address and ipv4_prefix:
            network_ip = f"{ipv4_address}/{ipv4_prefix}"
        else:
            network_ip = ipv4_address or "Unbekannt"
    else:
        network_ip = "DHCP"
    hostname_value = network_info.get("hostname") or ""
    if not hostname_value:
        try:
            hostname_value = _get_current_hostname()
        except Exception:  # pragma: no cover - defensiver Fallback
            hostname_value = socket.gethostname()

    return {
        "playing": is_playing,
        "bluetooth_status": "Verbunden" if is_bt_connected() else "Nicht verbunden",
        "wlan_status": wlan_ssid,
        "current_sink": get_current_sink(),
        "current_time": current_time,
        "amplifier_status": "An" if amplifier_claimed else "Aus",
        "relay_invert": RELAY_INVERT,
        "current_volume": current_volume,
        "dac_sink_detected": audio_status.get("dac_sink_detected"),
        "dac_sink_label": effective_label,
        "target_dac_sink": target_dac_sink,
        "dac_sink_hint": DAC_SINK_HINT,
        "configured_dac_sink": CONFIGURED_DAC_SINK,
        "default_dac_sink": DEFAULT_DAC_SINK,
        "normalization_headroom": headroom_details["value"],
        "normalization_headroom_env": headroom_details["env_raw"],
        "normalization_headroom_source": headroom_details["source"],
        "normalization_headroom_stored": headroom_details["stored_raw"],
        "schedule_default_volume_percent": schedule_default_volume["percent"],
        "schedule_default_volume_source": schedule_default_volume["source"],
        "schedule_default_volume_raw_percent": schedule_default_volume["raw_percent"],
        "schedule_default_volume_raw_db": schedule_default_volume["raw_db"],
        "schedule_default_volume_db_value": schedule_default_volume["db_value"],
        "amplifier_gpio_pin": amplifier_state["pin"],
        "amplifier_gpio_pin_default": amplifier_state["default"],
        "amplifier_gpio_pin_source": amplifier_state["source"],
        "amplifier_gpio_pin_configured": amplifier_state["configured"],
        "network_mode": network_mode,
        "network_ip": network_ip,
        "network_hostname": hostname_value,
    }


def _parse_auto_reboot_time(time_str):
    if not time_str:
        return None
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            dt = datetime.strptime(time_str, fmt)
            return dt.hour, dt.minute
        except ValueError:
            continue
    return None


def _normalize_time_for_input(time_str):
    parsed = _parse_auto_reboot_time(time_str)
    if parsed is None:
        return ""
    hour, minute = parsed
    return f"{hour:02d}:{minute:02d}"


def run_auto_reboot_job():
    try:
        logging.info("Automatischer Neustart wird initiiert.")
        command = privileged_command("systemctl", "reboot")
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        stdout_text = result.stdout
        stderr_text = result.stderr
        if _command_not_found(stderr_text, stdout_text, result.returncode):
            primary_command = _extract_primary_command(command)
            logging.error(
                "Automatischer Neustart fehlgeschlagen: %s nicht gefunden",
                primary_command,
            )
        elif result.returncode != 0:
            logging.error(
                "Automatischer Neustart fehlgeschlagen – Rückgabewert %s",
                result.returncode,
            )
    except Exception as exc:  # pragma: no cover - reine Vorsichtsmaßnahme
        logging.error("Fehler beim automatischen Neustart: %s", exc, exc_info=True)


def update_auto_reboot_job():
    enabled = get_setting("auto_reboot_enabled") == "1"
    try:
        job = scheduler.get_job(AUTO_REBOOT_JOB_ID)
    except Exception:  # pragma: no cover - Defensive, Scheduler kann JobLookupError werfen
        job = None
    if not enabled:
        if job is not None:
            scheduler.remove_job(AUTO_REBOOT_JOB_ID)
            logging.info("Auto-Reboot-Job entfernt (deaktiviert).")
        return False
    time_value = get_setting(
        "auto_reboot_time", AUTO_REBOOT_DEFAULTS["auto_reboot_time"]
    )
    parsed_time = _parse_auto_reboot_time(time_value)
    if parsed_time is None:
        logging.error(
            "Auto-Reboot: Ungültige Zeit '%s' – Job wird nicht geplant.", time_value
        )
        if job is not None:
            scheduler.remove_job(AUTO_REBOOT_JOB_ID)
        return False
    hour, minute = parsed_time
    mode = (get_setting("auto_reboot_mode") or "").strip().lower()
    if not mode:
        mode = AUTO_REBOOT_DEFAULTS["auto_reboot_mode"]
    cron_kwargs = {
        "hour": hour,
        "minute": minute,
        "second": 0,
        "timezone": LOCAL_TZ,
    }
    if mode == "weekly":
        weekday = (get_setting("auto_reboot_weekday") or "").strip().lower()
        if weekday not in AUTO_REBOOT_WEEKDAYS:
            weekday = AUTO_REBOOT_DEFAULTS["auto_reboot_weekday"]
        cron_kwargs["day_of_week"] = weekday
    elif mode != "daily":
        logging.warning(
            "Auto-Reboot: Unbekannter Modus '%s' – Job wird deaktiviert.", mode
        )
        if job is not None:
            scheduler.remove_job(AUTO_REBOOT_JOB_ID)
        return False
    trigger = CronTrigger(**cron_kwargs)
    scheduler.add_job(
        run_auto_reboot_job,
        trigger,
        id=AUTO_REBOOT_JOB_ID,
        replace_existing=True,
        misfire_grace_time=AUTO_REBOOT_MISFIRE_GRACE_SECONDS,
    )
    logging.info(
        "Auto-Reboot-Job geplant: Modus=%s, Zeit=%02d:%02d%s",
        mode,
        hour,
        minute,
        f", Wochentag={cron_kwargs.get('day_of_week')}"
        if mode == "weekly"
        else "",
    )
    return True


# GPIO für Endstufe
def activate_amplifier():
    global amplifier_claimed
    if not GPIO_AVAILABLE:
        logging.warning(
            "lgpio nicht verfügbar, überspringe Aktivierung der Endstufe"
        )
        return
    if gpio_handle is None:
        logging.warning(
            "GPIO-Handle nicht verfügbar, überspringe Aktivierung der Endstufe"
        )
        return
    was_claimed = amplifier_claimed
    try:
        if not was_claimed:
            if not _set_amp_output(AMP_OFF_LEVEL, keep_claimed=True):
                return
        if _set_amp_output(AMP_ON_LEVEL, keep_claimed=True):
            logging.info(
                "Endstufe EIN (bereits belegt)"
                if was_claimed
                else "Endstufe EIN"
            )
    except GPIOError as e:
        if "GPIO busy" in str(e):
            logging.warning("GPIO bereits belegt, überspringe claim")
        else:
            raise e


def deactivate_amplifier():
    global amplifier_claimed
    if not GPIO_AVAILABLE:
        logging.warning(
            "lgpio nicht verfügbar, überspringe Deaktivierung der Endstufe"
        )
        return
    if gpio_handle is None:
        logging.warning(
            "GPIO-Handle nicht verfügbar, überspringe Deaktivierung der Endstufe"
        )
        return
    if not amplifier_claimed:
        _set_amp_output(AMP_OFF_LEVEL, keep_claimed=False)
        return
    try:
        if _set_amp_output(AMP_OFF_LEVEL, keep_claimed=False):
            logging.info("Endstufe AUS")
    except GPIOError as e:
        if "GPIO busy" in str(e):
            logging.warning("GPIO busy beim deaktivieren, ignoriere")
        else:
            raise e


# Endstufe beim Start aus
if not TESTING:
    deactivate_amplifier()

play_lock = threading.Lock()


# Wiedergabe Funktion
def _coerce_volume_percent(raw_value, *, default=100):
    if raw_value is None:
        return default
    try:
        percent = int(raw_value)
    except (TypeError, ValueError):
        return default
    return max(0, min(100, percent))


def _get_master_volume():
    if not pygame_available:
        return 1.0
    try:
        volume = float(pygame.mixer.music.get_volume())
    except Exception as exc:  # pragma: no cover - defensive Schutz für pygame
        logging.debug("Konnte Master-Lautstärke nicht auslesen: %s", exc)
        return 1.0
    return max(0.0, min(1.0, volume))


def _set_volume_safe(value):
    if not pygame_available:
        _notify_audio_unavailable("Lautstärkeanpassung nicht möglich")
        return False
    clamped = max(0.0, min(1.0, float(value)))
    try:
        pygame.mixer.music.set_volume(clamped)
    except Exception as exc:  # pragma: no cover - defensive Schutz für pygame
        logging.debug("Konnte Lautstärke nicht setzen: %s", exc)
        return False
    return True


def _temporary_volume_scale(volume_percent):
    if not pygame_available:
        return nullcontext()
    sanitized = _coerce_volume_percent(volume_percent, default=None)
    if sanitized is None or sanitized == 100:
        return nullcontext()
    master_volume = _get_master_volume()
    target = master_volume * (sanitized / 100.0)

    @contextmanager
    def _volume_context():
        if not _set_volume_safe(target):
            yield
            return
        try:
            yield
        finally:
            _set_volume_safe(master_volume)

    return _volume_context()


# Wiedergabe Funktion
def _handle_audio_decode_failure(file_path: str, error: Exception) -> None:
    logging.error("Konnte Audiodatei %s nicht dekodieren: %s", file_path, error)
    if has_request_context():
        try:
            flash(
                "Audio-Datei konnte nicht dekodiert werden: "
                f"{os.path.basename(file_path)}"
            )
        except Exception:
            logging.debug(
                "Konnte Flash-Nachricht für Dekodierfehler nicht senden.",
                exc_info=True,
            )


def _prepare_audio_for_playback(file_path: str, temp_path: str) -> bool:
    try:
        sound = AudioSegment.from_file(file_path)
        headroom = float(get_normalization_headroom_db())
        normalized = sound.normalize(headroom=headroom)
        normalized.export(temp_path, format="wav")
    except CouldntDecodeError as exc:
        _handle_audio_decode_failure(file_path, exc)
        return False
    except Exception as exc:
        logging.exception(
            "Unerwarteter Fehler beim Vorbereiten der Audiodatei %s", file_path
        )
        if has_request_context():
            try:
                flash(
                    "Beim Vorbereiten der Audio-Datei ist ein Fehler aufgetreten: "
                    f"{os.path.basename(file_path)}"
                )
            except Exception:
                logging.debug(
                    "Konnte Flash-Nachricht für allgemeinen Dekodierfehler nicht senden.",
                    exc_info=True,
                )
        return False
    return True


def play_item(item_id, item_type, delay, is_schedule=False, volume_percent=100):
    global is_paused
    if not pygame_available:
        _notify_audio_unavailable("Wiedergabe kann nicht gestartet werden")
        return
    with play_lock:
        if pygame.mixer.music.get_busy():
            logging.info(
                f"Skippe Wiedergabe für {item_type} {item_id}, da andere läuft"
            )
            return
        sink_switched = set_sink(DAC_SINK)
        if not sink_switched:
            logging.info(
                "Nutze vorhandenen PulseAudio-Standardsink für Wiedergabe, da HiFiBerry nicht verfügbar ist."
            )
        activate_amplifier()
        time.sleep(delay)
        logging.info(f"Starte Wiedergabe für {item_type} {item_id}")
        sanitized_volume = _coerce_volume_percent(volume_percent)
        tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        temp_path = tmp_file.name
        tmp_file.close()
        try:
            if item_type == "file":
                with get_db_connection() as (conn, cursor):
                    cursor.execute(
                        "SELECT filename, duration_seconds FROM audio_files WHERE id=?",
                        (item_id,),
                    )
                    row = cursor.fetchone()
                if not row:
                    logging.warning(f"Audio-Datei-ID {item_id} nicht gefunden")
                    return
                filename = row["filename"]
                duration_seconds = row["duration_seconds"]
                file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                if not os.path.exists(file_path):
                    logging.warning(f"Datei fehlt: {file_path}")
                    if not is_schedule:
                        try:
                            if has_request_context():
                                flash("Audio-Datei nicht gefunden")
                        except Exception:
                            pass
                    return
                if not _prepare_audio_for_playback(file_path, temp_path):
                    return
                with _temporary_volume_scale(sanitized_volume):
                    pygame.mixer.music.load(temp_path)
                    pygame.mixer.music.play()
                    if duration_seconds is not None:
                        logging.info(
                            "Spiele Datei %s (%.2f s)", filename, duration_seconds
                        )
                    is_paused = False
                    while pygame.mixer.music.get_busy():
                        time.sleep(1)
            elif item_type == "playlist":
                with get_db_connection() as (conn, cursor):
                    cursor.execute(
                        """
                        SELECT f.filename, f.duration_seconds
                        FROM playlist_files pf
                        JOIN audio_files f ON pf.file_id = f.id
                        WHERE pf.playlist_id=?
                        ORDER BY f.filename
                        """,
                        (item_id,),
                    )
                    files = [dict(row) for row in cursor.fetchall()]
                with _temporary_volume_scale(sanitized_volume):
                    for file_info in files:
                        filename = file_info["filename"]
                        duration_seconds = file_info.get("duration_seconds")
                        file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                        if not os.path.exists(file_path):
                            logging.warning(f"Datei fehlt: {file_path}")
                            if not is_schedule:
                                try:
                                    if has_request_context():
                                        flash("Audio-Datei nicht gefunden")
                                except Exception:
                                    pass
                            continue
                        if not _prepare_audio_for_playback(file_path, temp_path):
                            return
                        pygame.mixer.music.load(temp_path)
                        pygame.mixer.music.play()
                        if duration_seconds is not None:
                            logging.info(
                                "Spiele Playlist-Datei %s (%.2f s)",
                                filename,
                                duration_seconds,
                            )
                        is_paused = False
                        while pygame.mixer.music.get_busy():
                            time.sleep(1)
        finally:
            try:
                os.remove(temp_path)
            except FileNotFoundError:
                pass
            bt_connected = is_bt_connected()
            if bt_connected:
                logging.info(
                    "Bluetooth-Verbindung aktiv – Endstufe bleibt eingeschaltet und Loopback wird reaktiviert."
                )
                resume_bt_audio()
                load_loopback()
            else:
                deactivate_amplifier()
            logging.info("Wiedergabe beendet")


# Scheduler-Logik

def schedule_job(schedule_id):
    with get_db_connection() as (conn, cursor):
        cursor.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,))
        row = cursor.fetchone()
    if row is None:
        logging.warning(f"Schedule {schedule_id} nicht gefunden")
        return
    sch = dict(row)
    item_id = sch["item_id"]
    item_type = sch["item_type"]
    delay = sch["delay"]
    repeat = sch["repeat"]
    volume_percent = _coerce_volume_percent(sch.get("volume_percent"))
    if repeat != "once" and not is_within_schedule_range(
        sch["start_date"], sch["end_date"]
    ):
        logging.info(
            "Zeitplan %s außerhalb des gültigen Datumsbereichs (%s - %s) – übersprungen",
            schedule_id,
            sch["start_date"] or "offen",
            sch["end_date"] or "offen",
        )
        return
    play_item(item_id, item_type, delay, is_schedule=True, volume_percent=volume_percent)
    if repeat == "once":
        with get_db_connection() as (conn, cursor):
            cursor.execute(
                "UPDATE schedules SET executed=1 WHERE id=?",
                (schedule_id,),
            )
            conn.commit()
        load_schedules()


def skip_past_once_schedules():
    """Markiert abgelaufene Einmal-Zeitpläne als ausgeführt (Grace-Zeit)."""
    now = datetime.now(LOCAL_TZ)
    # Negatives Toleranzfenster, um nur eindeutig vergangene Startzeiten zu überspringen.
    tolerance = timedelta(seconds=1)
    threshold = now - tolerance
    with get_db_connection() as (conn, cursor):
        cursor.execute("SELECT id, time FROM schedules WHERE repeat='once' AND executed=0")
        schedules = cursor.fetchall()
        for sch_id, sch_time in schedules:
            try:
                run_time = parse_once_datetime(sch_time)
                run_time_local = _to_local_aware(run_time)
                if run_time_local and run_time_local <= threshold:
                    cursor.execute("UPDATE schedules SET executed=1 WHERE id=?", (sch_id,))
                    logging.info(f"Skippe überfälligen 'once' Schedule {sch_id}")
            except ValueError:
                logging.warning(f"Skippe Schedule {sch_id} mit ungültiger Zeit {sch_time}")
        conn.commit()


def load_schedules():
    try:
        auto_reboot_job_existed = scheduler.get_job(AUTO_REBOOT_JOB_ID) is not None
    except Exception:
        auto_reboot_job_existed = False

    scheduler.remove_all_jobs()
    # Misfire-Puffer: Default 60 s, optional via Settings-Key 'scheduler_misfire_grace_time'.
    raw_misfire_value = get_setting("scheduler_misfire_grace_time")
    default_grace_seconds = 60
    try:
        misfire_grace_seconds = max(1, int(raw_misfire_value)) if raw_misfire_value is not None else default_grace_seconds
    except (TypeError, ValueError):
        logging.warning(
            "Ungültiger Wert für scheduler_misfire_grace_time (%s), fallback auf %s s",
            raw_misfire_value,
            default_grace_seconds,
        )
        misfire_grace_seconds = default_grace_seconds
    with get_db_connection() as (conn, cursor):
        cursor.execute("SELECT * FROM schedules")
        schedules = [dict(row) for row in cursor.fetchall()]
    for sch in schedules:
        sch_id = sch["id"]
        time_str = sch["time"]
        repeat = sch["repeat"]
        executed = sch["executed"]
        if executed:
            continue
        try:
            start_date = parse_schedule_date(sch["start_date"])
            end_date = parse_schedule_date(sch["end_date"])
            if repeat != "once" and end_date and end_date < datetime.now().date():
                logging.info(
                    "Zeitplan %s endet am %s und wird nicht geladen",
                    sch_id,
                    end_date,
                )
                continue
            if repeat == "once":
                run_dt = parse_once_datetime(time_str)
                run_time = _to_local_aware(run_dt)
                if run_time is None:
                    logging.warning(
                        "Zeitplan %s besitzt keine gültige lokale Ausführungszeit (%s)",
                        sch_id,
                        time_str,
                    )
                    continue
                trigger = DateTrigger(run_date=run_time)
            elif repeat == "daily":
                h, m, s = [int(part) for part in time_str.split(":")]
                start_dt = (
                    datetime.combine(start_date, datetime.min.time()).replace(
                        hour=h, minute=m, second=s
                    )
                    if start_date
                    else None
                )
                start_dt = _ensure_local_timezone(start_dt)
                end_dt = (
                    datetime.combine(end_date, datetime.max.time())
                    if end_date
                    else None
                )
                end_dt = _ensure_local_timezone(end_dt)
                trigger = CronTrigger(
                    hour=h,
                    minute=m,
                    second=s,
                    start_date=start_dt,
                    end_date=end_dt,
                    timezone=LOCAL_TZ,
                )
            elif repeat == "monthly":
                h, m, s = [int(part) for part in time_str.split(":")]
                raw_day_of_month = sch["day_of_month"]
                if raw_day_of_month is None and start_date:
                    raw_day_of_month = start_date.day
                try:
                    day_of_month = int(raw_day_of_month)
                except (TypeError, ValueError):
                    logging.warning(
                        "Zeitplan %s besitzt keinen gültigen Tag im Monat und wird übersprungen",
                        sch_id,
                    )
                    continue
                if not 1 <= day_of_month <= 31:
                    logging.warning(
                        "Zeitplan %s hat einen ungültigen Tag im Monat (%s)",
                        sch_id,
                        day_of_month,
                    )
                    continue
                start_dt = None
                if start_date:
                    try:
                        first_occurrence = calculate_first_monthly_occurrence(
                            start_date, day_of_month
                        )
                    except ValueError as exc:
                        logging.warning(
                            "Zeitplan %s kann nicht geladen werden: %s",
                            sch_id,
                            exc,
                        )
                        continue
                    start_dt = datetime.combine(
                        first_occurrence, datetime.min.time()
                    ).replace(hour=h, minute=m, second=s)
                start_dt = _ensure_local_timezone(start_dt)
                end_dt = (
                    datetime.combine(end_date, datetime.max.time()) if end_date else None
                )
                end_dt = _ensure_local_timezone(end_dt)
                trigger = CronTrigger(
                    day=day_of_month,
                    hour=h,
                    minute=m,
                    second=s,
                    start_date=start_dt,
                    end_date=end_dt,
                    timezone=LOCAL_TZ,
                )
            else:
                logging.warning(f"Unbekannter Repeat-Typ {repeat} für Schedule {sch_id}")
                continue
            scheduler.add_job(
                schedule_job,
                trigger,
                args=[sch_id],
                misfire_grace_time=misfire_grace_seconds,
                id=str(sch_id),
            )
            display_time = (
                _format_schedule_time_for_display(time_str, repeat)
                if repeat == "once"
                else time_str
            )
            logging.info(
                "Geplanter Job %s: Repeat=%s, Time=%s, Misfire-Grace=%s",
                sch_id,
                repeat,
                display_time,
                misfire_grace_seconds,
            )
        except ValueError:
            logging.warning(f"Ungültige Zeit {time_str} für Schedule {sch_id}")

    if auto_reboot_job_existed:
        update_auto_reboot_job()


def start_background_services(*, force: bool = False) -> bool:
    """Startet Scheduler und abhängige Hintergrundaufgaben idempotent."""

    global _BACKGROUND_SERVICES_STARTED

    start_helpers = False

    with _BACKGROUND_SERVICES_LOCK:
        if _BACKGROUND_SERVICES_STARTED and not force:
            logging.debug("Hintergrunddienste bereits gestartet – kein erneuter Start.")
            return False

        skip_past_once_schedules()
        load_schedules()
        update_auto_reboot_job()

        try:
            if not getattr(scheduler, "running", False):
                scheduler.start()
        except SchedulerAlreadyRunningError:
            logging.debug("Scheduler läuft bereits – Start übersprungen.")
        _BACKGROUND_SERVICES_STARTED = True
        start_helpers = True
        logging.info("Hintergrunddienste initialisiert.")

    if start_helpers and not TESTING:
        if force:
            _stop_bt_audio_monitor_thread()
            _stop_button_monitor()
        _start_bluetooth_auto_accept_thread()
        _start_bt_audio_monitor_thread()
        _start_button_monitor()

    return True


def stop_background_services(*, wait: bool = False) -> bool:
    """Stoppt Scheduler und bereinigt Ressourcen idempotent."""

    global _BACKGROUND_SERVICES_STARTED

    helpers_were_active = False

    with _BACKGROUND_SERVICES_LOCK:
        was_running = bool(getattr(scheduler, "running", False))
        try:
            if was_running:
                scheduler.shutdown(wait=wait)
        except SchedulerNotRunningError:
            logging.debug("Scheduler war beim Stoppen bereits beendet.")
            was_running = False
        finally:
            helpers_were_active = _BACKGROUND_SERVICES_STARTED
            _BACKGROUND_SERVICES_STARTED = False

        if was_running:
            logging.info("Hintergrunddienste gestoppt.")

    if helpers_were_active:
        _stop_bt_audio_monitor_thread()
        _stop_button_monitor()

    return was_running


# --- Bluetooth-Hilfsfunktionen ---
_PACTL_MISSING_MESSAGE = (
    "PulseAudio-Werkzeuge (pactl) wurden nicht gefunden. Bitte Installation prüfen."
)
_PACTL_MISSING_LOGGED = False


def _notify_missing_pactl() -> None:
    """Informiert über fehlende PulseAudio-Kommandos."""

    global _PACTL_MISSING_LOGGED

    if not _PACTL_MISSING_LOGGED:
        logging.error(_PACTL_MISSING_MESSAGE)
        _PACTL_MISSING_LOGGED = True

    if has_request_context():
        if getattr(g, "_pactl_missing_notified", False):
            return
        flash(_PACTL_MISSING_MESSAGE)
        g._pactl_missing_notified = True


def _run_pactl_command(*args: str) -> Optional[str]:
    """Führt einen pactl-Befehl aus und fängt häufige Fehler ab."""

    command = ["pactl", *args]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        _notify_missing_pactl()
        return None
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        if stderr:
            logging.error(
                "pactl-Befehl '%s' fehlgeschlagen (Code %s): %s",
                " ".join(command[1:]),
                exc.returncode,
                stderr,
            )
        else:
            logging.error(
                "pactl-Befehl '%s' fehlgeschlagen (Code %s).",
                " ".join(command[1:]),
                exc.returncode,
            )
        return None

    output = (result.stdout or "").strip()
    if not output:
        return None
    return output


_PULSEAUDIO_PERCENT_PATTERN = re.compile(r"/\s*(\d+)%")
_PULSEAUDIO_DB_PATTERN = re.compile(r"(?P<value>-?(?:\d+(?:\.\d+)?|\.\d+|inf))\s*dB", re.IGNORECASE)


def _percent_to_pulseaudio_db(percent: int) -> Optional[float]:
    if percent <= 0:
        return None
    ratio = (percent / 100.0) ** 3
    if ratio <= 0:
        return None
    return 20.0 * math.log10(ratio)


def _extract_max_volume_percent(volume_output: str) -> Optional[int]:
    """Liest den höchsten Prozentwert aus einer pactl-Lautstärkeausgabe."""

    matches = [int(match.group(1)) for match in _PULSEAUDIO_PERCENT_PATTERN.finditer(volume_output)]
    if not matches:
        return None
    return max(matches)


def _extract_max_volume_db(volume_output: str) -> Optional[float]:
    """Ermittelt den höchsten dB-Wert aus einer pactl-Lautstärkeausgabe."""

    matches = []
    for match in _PULSEAUDIO_DB_PATTERN.finditer(volume_output):
        value = match.group("value")
        try:
            db_value = float(value)
        except ValueError:  # pragma: no cover - defensive
            continue
        matches.append(db_value)
    if not matches:
        return None
    return max(matches)


def _enforce_bluetooth_volume_cap_for_sink(
    sink_name: str, cap: BluetoothVolumeCap
) -> bool:
    """Begrenzt die Lautstärke eines Bluetooth-Sinks auf den gegebenen Zielwert."""

    limit_percent = cap.percent
    headroom_db = cap.headroom_db

    if limit_percent >= 100 and headroom_db <= 0:
        return False

    volume_output = _run_pactl_command("get-sink-volume", sink_name)
    if volume_output is None:
        return False

    current_percent = _extract_max_volume_percent(volume_output)
    current_db = _extract_max_volume_db(volume_output) if headroom_db > 0 else None
    initial_percent = current_percent
    initial_db = current_db
    percent_exceeds = current_percent is not None and current_percent > limit_percent

    changed = False
    db_limited = False
    target_db = -headroom_db if headroom_db > 0 else None

    if headroom_db > 0 and current_db is not None and target_db is not None:
        if current_db > target_db:
            delta_db = current_db - target_db
            if delta_db > 0:
                _run_pactl_command("set-sink-volume", sink_name, f"-{delta_db}dB")
                changed = True
                db_limited = True

                verification_output = _run_pactl_command("get-sink-volume", sink_name)
                if verification_output is None:
                    return False
                volume_output = verification_output
                current_percent = _extract_max_volume_percent(volume_output)
                current_db = _extract_max_volume_db(volume_output)
                percent_exceeds = (
                    current_percent is not None and current_percent > limit_percent
                )

                if current_db is not None and current_db > target_db:
                    if limit_percent < 100:
                        _run_pactl_command("set-sink-volume", sink_name, f"{limit_percent}%")
                        verification_output = _run_pactl_command("get-sink-volume", sink_name)
                        if verification_output is None:
                            return False
                        volume_output = verification_output
                        current_percent = _extract_max_volume_percent(volume_output)
                        current_db = _extract_max_volume_db(volume_output)
                        percent_exceeds = (
                            current_percent is not None and current_percent > limit_percent
                        )
                    if current_db is not None and current_db > target_db:
                        return False
            elif not percent_exceeds:
                return False
        elif not percent_exceeds:
            return False
    elif headroom_db > 0 and current_db is None and not percent_exceeds:
        return False
    elif headroom_db <= 0 and not percent_exceeds:
        return False

    percent_limited = False
    if percent_exceeds:
        _run_pactl_command("set-sink-volume", sink_name, f"{limit_percent}%")
        changed = True
        percent_limited = True

        verification_output = _run_pactl_command("get-sink-volume", sink_name)
        if verification_output is None:
            return False
        volume_output = verification_output
        current_percent = _extract_max_volume_percent(volume_output)
        current_db = _extract_max_volume_db(volume_output) if headroom_db > 0 else current_db
        percent_exceeds = current_percent is not None and current_percent > limit_percent

        if percent_exceeds:
            return False
        if headroom_db > 0 and target_db is not None and current_db is not None:
            if current_db > target_db:
                return False

    if not changed:
        return False

    if db_limited:
        logging.info(
            "Bluetooth-Lautstärke von %s auf %.2f dB begrenzt (vorher %s%% ≈ %.2f dB, jetzt %.2f dB)",
            sink_name,
            target_db if target_db is not None else 0.0,
            initial_percent if initial_percent is not None else "?",
            initial_db if initial_db is not None else float("nan"),
            current_db if current_db is not None else float("nan"),
        )
    if percent_limited:
        logging.info(
            "Bluetooth-Lautstärke von %s auf %s%% begrenzt (vorher %s%%, jetzt %s%%)",
            sink_name,
            limit_percent,
            initial_percent if initial_percent is not None else "?",
            current_percent if current_percent is not None else "?",
        )

    return True


def _list_bluetooth_sinks() -> List[str]:
    sinks_output = _run_pactl_command("list", "short", "sinks")
    if sinks_output is None:
        return []

    sink_names: List[str] = []
    for line in sinks_output.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        if "bluez" in parts[1]:
            sink_names.append(parts[1])
    return sink_names


def _enforce_bluetooth_volume_cap(cap: BluetoothVolumeCap) -> None:
    if cap.percent >= 100 and cap.headroom_db <= 0:
        return

    for sink_name in _list_bluetooth_sinks():
        _enforce_bluetooth_volume_cap_for_sink(sink_name, cap)


def is_bt_connected():
    """Prüft, ob ein Bluetooth-Gerät verbunden ist."""
    sinks_output = _run_pactl_command("list", "short", "sinks")
    if sinks_output is None:
        return False
    return any("bluez" in line for line in sinks_output.splitlines())


def resume_bt_audio():
    """Stellt den Bluetooth-Sink wieder als Standard ein."""
    if not pygame_available:
        _notify_audio_unavailable("Bluetooth-Wiedergabe kann nicht reaktiviert werden")
        return
    sinks_output = _run_pactl_command("list", "short", "sinks")
    if sinks_output is None:
        return False

    sink_lines = [line for line in sinks_output.splitlines() if "bluez" in line]
    if not sink_lines:
        logging.info("Kein Bluetooth-Sink zum Resume gefunden")
        return False

    bt_sink = sink_lines[0].split()[1]
    previous_detection = audio_status.get("dac_sink_detected")
    cap = get_bluetooth_volume_cap_percent()
    if cap.percent < 100 or cap.headroom_db > 0:
        _enforce_bluetooth_volume_cap_for_sink(bt_sink, cap)
    set_sink(bt_sink)
    if not _sink_is_configured(bt_sink):
        # Sicherstellen, dass der HiFiBerry-Status durch Fremd-Sinks unverändert bleibt.
        audio_status["dac_sink_detected"] = previous_detection
    logging.info(f"Bluetooth-Sink {bt_sink} wieder aktiv")
    return True


def load_loopback():
    """Aktiviert PulseAudio-Loopback von der Bluetooth-Quelle zum DAC."""
    modules_output = _run_pactl_command("list", "short", "modules")
    if modules_output is None:
        return False

    target_sink = (
        _resolve_sink_name(DAC_SINK)
        or _resolve_sink_name(DAC_SINK_HINT)
        or DAC_SINK
    )
    if not target_sink:
        logging.warning(
            "Kein PulseAudio-Zielsink für Loopback gefunden (Konfiguration: %s)",
            DAC_SINK_HINT,
        )
        return False

    for mod in modules_output.splitlines():
        if "module-loopback" in mod and target_sink in mod:
            logging.info("Loopback bereits aktiv")
            return True

    sources_output = _run_pactl_command("list", "short", "sources")
    if sources_output is None:
        return False

    sources = [line for line in sources_output.splitlines() if "bluez" in line]
    if not sources:
        logging.info("Kein Bluetooth-Source für Loopback gefunden")
        return False

    bt_source = sources[0].split()[1]
    load_result = _run_pactl_command(
        "load-module",
        "module-loopback",
        f"source={bt_source}",
        f"sink={target_sink}",
        "latency_msec=30",
    )
    if load_result is None:
        return False

    logging.info(f"Loopback geladen: {bt_source} -> {target_sink}")
    return True


# --- Bluetooth Audio Monitor (A2DP-Sink Erkennung & Verstärkersteuerung) ---
_bt_audio_monitor_thread: Optional[threading.Thread] = None
_bt_audio_monitor_stop_event: Optional[threading.Event] = None


def is_bt_audio_active():
    # Prüft, ob ein Bluetooth-Audio-Stream anliegt (A2DP)
    sinks_output = _run_pactl_command("list", "short", "sinks")
    if sinks_output is None:
        return False

    bluetooth_sink_ids = set()
    bluetooth_sink_names = set()
    for line in sinks_output.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        index, name = parts[0], parts[1]
        if "bluez" in name:
            bluetooth_sink_ids.add(index)
            bluetooth_sink_names.add(name)

    if not bluetooth_sink_ids and not bluetooth_sink_names:
        return False

    sink_inputs_output = _run_pactl_command("list", "short", "sink-inputs")
    if sink_inputs_output is None:
        return False

    for sink_input in sink_inputs_output.splitlines():
        parts = sink_input.split()
        if len(parts) < 2:
            continue

        sink_id = parts[1]
        if sink_id in bluetooth_sink_ids:
            return True

        if any(name in sink_input for name in bluetooth_sink_names):
            return True
    return False
def bt_audio_monitor(stop_event: Optional[threading.Event] = None) -> None:
    was_active = False
    while True:
        if stop_event is not None and stop_event.is_set():
            break

        active = is_bt_audio_active()
        if active:
            cap = get_bluetooth_volume_cap_percent()
            _enforce_bluetooth_volume_cap(cap)
        if active and not was_active:
            activate_amplifier()
            was_active = True
            logging.info("Bluetooth Audio erkannt, Verstärker EIN")
        elif not active and was_active:
            deactivate_amplifier()
            was_active = False
            logging.info("Bluetooth Audio gestoppt, Verstärker AUS")

        if stop_event is not None:
            if stop_event.wait(3):
                break
        else:
            time.sleep(3)

    if was_active:
        deactivate_amplifier()
        logging.info("Bluetooth Audio gestoppt, Verstärker AUS (Shutdown)")
    logging.debug("Bluetooth Audio Monitor beendet")


def _start_bluetooth_auto_accept_thread() -> None:
    thread = threading.Thread(
        target=bluetooth_auto_accept,
        name="bluetooth-auto-accept",
        daemon=True,
    )
    thread.start()


def _start_bt_audio_monitor_thread() -> None:
    global _bt_audio_monitor_thread, _bt_audio_monitor_stop_event

    existing = _bt_audio_monitor_thread
    if existing and existing.is_alive():
        return

    stop_event = threading.Event()
    _bt_audio_monitor_stop_event = stop_event
    thread = threading.Thread(
        target=bt_audio_monitor,
        kwargs={"stop_event": stop_event},
        name="bt-audio-monitor",
        daemon=True,
    )
    _bt_audio_monitor_thread = thread
    thread.start()


def _stop_bt_audio_monitor_thread(timeout: float = 2.0) -> None:
    global _bt_audio_monitor_thread, _bt_audio_monitor_stop_event

    thread = _bt_audio_monitor_thread
    if thread is None:
        return

    stop_event = _bt_audio_monitor_stop_event
    if stop_event is not None:
        stop_event.set()

    thread.join(timeout=timeout)

    if thread.is_alive():
        logging.warning(
            "Bluetooth-Audio-Monitor: Thread konnte nicht sauber beendet werden"
        )

    _bt_audio_monitor_thread = None
    _bt_audio_monitor_stop_event = None


# AP-Modus
def has_network():
    return "default" in subprocess.getoutput("ip route")


def _handle_systemctl_failure(action: str, service: str, exit_code: int) -> None:
    message = f"systemctl {action} {service} endete mit Exit-Code {exit_code}"
    logging.warning(message)
    if has_request_context():
        flash(f"Warnung: {message}")


def _call_systemctl(action: str, service: str) -> bool:
    command = privileged_command("systemctl", action, service)
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )

    if _command_not_found(result.stderr, result.stdout, result.returncode):
        primary_command = _extract_primary_command(command)
        message = (
            f"{primary_command} ist nicht verfügbar. Bitte stellen Sie sicher, dass systemctl installiert ist."
        )
        logging.error(message)
        if has_request_context():
            flash(message)
        return False

    if result.returncode != 0:
        _handle_systemctl_failure(action, service, result.returncode)
        return False
    return True


def setup_ap():
    try:
        if not has_network():
            logging.info("Kein Netzwerk – starte AP-Modus")
            if not _call_systemctl("start", "dnsmasq"):
                return False
            if not _call_systemctl("start", "hostapd"):
                return False
            return True
        return disable_ap()
    except (FileNotFoundError, OSError) as exc:
        logging.error("systemctl-Aufruf fehlgeschlagen: %s", exc)
        if has_request_context():
            flash("systemctl nicht verfügbar oder Berechtigung verweigert")
        return False


def disable_ap():
    try:
        hostapd_stopped = _call_systemctl("stop", "hostapd")
        dnsmasq_stopped = _call_systemctl("stop", "dnsmasq")
    except (FileNotFoundError, OSError) as exc:
        logging.error("systemctl-Aufruf fehlgeschlagen: %s", exc)
        if has_request_context():
            flash("systemctl nicht verfügbar oder Berechtigung verweigert")
        return False
    if hostapd_stopped and dnsmasq_stopped:
        logging.info("AP-Modus deaktiviert")
        return True
    return False


# ---- Flask Web-UI ----
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        with get_db_connection() as (conn, cursor):
            cursor.execute("SELECT * FROM users WHERE username=?", (username,))
            user_data = cursor.fetchone()
        if user_data and check_password_hash(user_data["password"], password):
            user_columns = set(user_data.keys())
            must_change_value = (
                user_data["must_change_password"]
                if "must_change_password" in user_columns
                else 0
            )
            user = User(user_data["id"], username, must_change_value)
            login_user(user)
            if user.must_change_password:
                flash("Bitte ändern Sie das initiale Passwort, bevor Sie fortfahren.")
                return redirect(url_for("change_password"))
            return redirect(url_for("index"))
        flash("Falsche Anmeldedaten")
    return render_template("login.html")


@app.route("/logout", methods=["POST"], endpoint="logout_route")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    file_page_size = _parse_page_size(request.args.get("file_page_size"))
    schedule_page_size = _parse_page_size(request.args.get("schedule_page_size"))
    file_page_number = _parse_page_number(request.args.get("file_page"))
    schedule_page_number = _parse_page_number(request.args.get("schedule_page"))

    with get_db_connection() as (conn, cursor):
        cursor.execute("SELECT COUNT(*) FROM audio_files")
        files_total_count = cursor.fetchone()[0]
        files_meta = _compute_pagination_meta(
            files_total_count, file_page_number, file_page_size
        )
        if files_meta["limit"] is None:
            cursor.execute(
                "SELECT id, filename, duration_seconds FROM audio_files ORDER BY filename"
            )
        else:
            cursor.execute(
                """
                SELECT id, filename, duration_seconds
                FROM audio_files
                ORDER BY filename
                LIMIT ? OFFSET ?
                """,
                (files_meta["limit"], files_meta["offset"]),
            )
        files_page_items = [dict(row) for row in cursor.fetchall()]

        cursor.execute(
            "SELECT id, filename, duration_seconds FROM audio_files ORDER BY filename"
        )
        files_all = [dict(row) for row in cursor.fetchall()]

        cursor.execute("SELECT id, name FROM playlists ORDER BY name")
        playlists_all = [dict(row) for row in cursor.fetchall()]

        cursor.execute("SELECT COUNT(*) FROM schedules")
        schedules_total_count = cursor.fetchone()[0]
        schedules_meta = _compute_pagination_meta(
            schedules_total_count, schedule_page_number, schedule_page_size
        )
        schedule_query = """
            SELECT
                s.id,
                CASE WHEN s.item_type='file' THEN f.filename ELSE p.name END as name,
                s.time,
                s.repeat,
                s.delay,
                s.item_type,
                s.executed,
                s.start_date,
                s.end_date,
                s.day_of_month,
                f.duration_seconds AS file_duration,
                s.volume_percent
            FROM schedules s
            LEFT JOIN audio_files f ON s.item_id = f.id AND s.item_type='file'
            LEFT JOIN playlists p ON s.item_id = p.id AND s.item_type='playlist'
            ORDER BY s.time
        """
        if schedules_meta["limit"] is None:
            cursor.execute(schedule_query)
        else:
            cursor.execute(
                schedule_query + " LIMIT ? OFFSET ?",
                (schedules_meta["limit"], schedules_meta["offset"]),
            )
        schedule_rows = cursor.fetchall()
    schedules = [
        {
            "id": row["id"],
            "name": row["name"],
            "time": row["time"],
            "time_display": _format_schedule_time_for_display(row["time"], row["repeat"]),
            "repeat": row["repeat"],
            "delay": row["delay"],
            "item_type": row["item_type"],
            "executed": row["executed"],
            "start_date": row["start_date"],
            "end_date": row["end_date"],
            "day_of_month": row["day_of_month"],
            "duration_seconds": row["file_duration"],
            "volume_percent": _coerce_volume_percent(row["volume_percent"]),
        }
        for row in schedule_rows
    ]
    files_page = {**files_meta, "items": files_page_items}
    schedules_page = {**schedules_meta, "items": schedules}
    files_total = {"count": files_total_count}
    schedules_total = {"count": schedules_total_count}
    hardware_button_configs = get_hardware_button_config()
    file_lookup = {item["id"]: item["filename"] for item in files_all}
    playlist_lookup = {item["id"]: item["name"] for item in playlists_all}
    hardware_buttons = []
    for entry in hardware_button_configs:
        entry_dict = asdict(entry)
        item_label: Optional[str]
        item_reference = ""
        if entry.item_type == "file" and entry.item_id is not None:
            item_label = file_lookup.get(entry.item_id) or f"Datei #{entry.item_id}"
            item_reference = f"file:{entry.item_id}"
        elif entry.item_type == "playlist" and entry.item_id is not None:
            item_label = playlist_lookup.get(entry.item_id) or f"Playlist #{entry.item_id}"
            item_reference = f"playlist:{entry.item_id}"
        else:
            item_label = None

        entry_dict.update(
            {
                "action_label": HARDWARE_BUTTON_ACTION_LABELS.get(
                    entry.action.upper(), entry.action
                ),
                "item_label": item_label,
                "item_reference": item_reference,
            }
        )
        hardware_buttons.append(entry_dict)
    status = gather_status()
    rtc_state = get_rtc_configuration_state()
    status.update(
        {
            "rtc_available": RTC_AVAILABLE,
            "rtc_address": RTC_DETECTED_ADDRESS,
            "rtc_missing_flag": RTC_MISSING_FLAG,
            "rtc_module": rtc_state["module"],
            "rtc_module_label": rtc_state["module_label"],
            "rtc_candidates": rtc_state["effective_addresses"],
            "rtc_candidates_display": rtc_state["effective_addresses_display"],
        }
    )
    network_settings = _load_network_settings_for_template("wlan0")

    auto_reboot_settings = {
        "enabled": get_setting("auto_reboot_enabled") == "1",
        "mode": get_setting(
            "auto_reboot_mode", AUTO_REBOOT_DEFAULTS["auto_reboot_mode"]
        ),
        "time": _normalize_time_for_input(
            get_setting("auto_reboot_time", AUTO_REBOOT_DEFAULTS["auto_reboot_time"])
        ),
        "weekday": get_setting(
            "auto_reboot_weekday", AUTO_REBOOT_DEFAULTS["auto_reboot_weekday"]
        ),
    }
    default_schedule_delay = min(VERZOEGERUNG_SEC, MAX_SCHEDULE_DELAY_SECONDS)
    return render_template(
        "index.html",
        files=files_page_items,
        files_all=files_all,
        files_page=files_page,
        files_total=files_total,
        playlists=playlists_all,
        playlists_all=playlists_all,
        schedules=schedules,
        schedules_page=schedules_page,
        schedules_total=schedules_total,
        status=status,
        auto_reboot_settings=auto_reboot_settings,
        auto_reboot_weekdays=AUTO_REBOOT_WEEKDAYS,
        page_size_options=PAGE_SIZE_OPTIONS,
        build_index_url=build_index_url,
        default_headroom=DEFAULT_NORMALIZATION_HEADROOM_DB,
        normalization_headroom_env_key=NORMALIZATION_HEADROOM_ENV_KEY,
        schedule_default_volume_percent=status.get(
            "schedule_default_volume_percent", SCHEDULE_DEFAULT_VOLUME_PERCENT_FALLBACK
        ),
        schedule_default_volume_source=status.get(
            "schedule_default_volume_source", "default"
        ),
        schedule_default_volume_raw_percent=status.get(
            "schedule_default_volume_raw_percent"
        ),
        schedule_default_volume_raw_db=status.get("schedule_default_volume_raw_db"),
        schedule_default_volume_db_value=status.get("schedule_default_volume_db_value"),
        schedule_default_volume_fallback=SCHEDULE_DEFAULT_VOLUME_PERCENT_FALLBACK,
        max_schedule_delay_seconds=MAX_SCHEDULE_DELAY_SECONDS,
        default_schedule_delay=default_schedule_delay,
        hardware_buttons=hardware_buttons,
        hardware_button_actions=HARDWARE_BUTTON_ACTIONS,
        hardware_button_action_labels=HARDWARE_BUTTON_ACTION_LABELS,
        default_button_debounce_ms=DEFAULT_BUTTON_DEBOUNCE_MS,
        network_settings=network_settings,
    )


def _hardware_button_redirect_url() -> str:
    return url_for("index") + "#hardware-buttons-admin"


def _amplifier_settings_redirect_url() -> str:
    return url_for("index") + "#amplifier-settings"


def _network_settings_redirect_url() -> str:
    return url_for("index") + "#network-settings"


def _parse_hardware_button_form(form) -> Tuple[Optional[dict], List[str]]:
    errors: List[str] = []

    gpio_raw = (form.get("gpio_pin") or "").strip()
    gpio_pin: Optional[int] = None
    if not gpio_raw:
        errors.append("GPIO-Pin ist erforderlich.")
    else:
        try:
            candidate = int(gpio_raw, 0)
        except ValueError:
            errors.append("GPIO-Pin muss eine gültige Zahl sein.")
        else:
            if candidate < 0:
                errors.append("GPIO-Pin darf nicht negativ sein.")
            elif candidate == GPIO_PIN_ENDSTUFE:
                errors.append(
                    f"GPIO {candidate} ist für die Endstufe reserviert und kann nicht als Taster verwendet werden."
                )
            else:
                gpio_pin = candidate

    action_raw = (form.get("action") or "").strip().upper()
    if not action_raw:
        errors.append("Aktion ist erforderlich.")
    elif action_raw not in HARDWARE_BUTTON_ACTION_LABELS:
        errors.append(f"Unbekannte Aktion: {action_raw}.")

    item_reference_raw = (form.get("item_reference") or "").strip()
    item_type: Optional[str] = None
    item_id: Optional[int] = None
    if item_reference_raw:
        if ":" not in item_reference_raw:
            errors.append("Ungültige Ziel-Auswahl für den PLAY-Button.")
        else:
            type_part, id_part = item_reference_raw.split(":", 1)
            normalized_type = type_part.strip().lower()
            if normalized_type not in PLAY_NOW_ALLOWED_TYPES:
                errors.append("Ungültiger Ziel-Typ für den PLAY-Button.")
            else:
                try:
                    candidate_id = int(id_part.strip(), 0)
                except ValueError:
                    errors.append("Ungültige Ziel-ID für den PLAY-Button.")
                else:
                    if candidate_id < 0:
                        errors.append("Die Ziel-ID darf nicht negativ sein.")
                    else:
                        item_type = normalized_type
                        item_id = candidate_id

    debounce_ms = DEFAULT_BUTTON_DEBOUNCE_MS
    debounce_raw = (form.get("debounce_ms") or "").strip()
    if debounce_raw:
        try:
            candidate = int(debounce_raw, 0)
        except ValueError:
            errors.append("Entprellzeit muss eine gültige Zahl sein.")
        else:
            if candidate < 0:
                errors.append("Entprellzeit darf nicht negativ sein.")
            else:
                debounce_ms = candidate

    enabled = 1 if form.get("enabled") else 0

    if action_raw == "PLAY":
        if item_type is None or item_id is None:
            errors.append("Für PLAY muss eine Datei oder Playlist ausgewählt werden.")
    else:
        item_type = None
        item_id = None

    if gpio_pin is None and not errors:
        errors.append("GPIO-Pin konnte nicht geparst werden.")

    if errors:
        return None, errors

    return (
        {
            "gpio_pin": gpio_pin,
            "action": action_raw,
            "item_type": item_type,
            "item_id": item_id,
            "debounce_ms": debounce_ms,
            "enabled": enabled,
        },
        [],
    )


def _hardware_button_target_exists(cursor, item_type: str, item_id: int) -> bool:
    if item_type == "file":
        cursor.execute("SELECT 1 FROM audio_files WHERE id=?", (item_id,))
    elif item_type == "playlist":
        cursor.execute("SELECT 1 FROM playlists WHERE id=?", (item_id,))
    else:
        return False
    return cursor.fetchone() is not None


@app.route("/hardware_buttons", methods=["POST"])
@login_required
def create_hardware_button():
    parsed, errors = _parse_hardware_button_form(request.form)
    if errors or parsed is None:
        for message in errors:
            flash(message)
        return redirect(_hardware_button_redirect_url())

    changed = False
    with get_db_connection() as (conn, cursor):
        if parsed["item_type"] and parsed["item_id"] is not None:
            if not _hardware_button_target_exists(
                cursor, parsed["item_type"], parsed["item_id"]
            ):
                flash("Ausgewähltes Ziel existiert nicht mehr.")
                return redirect(_hardware_button_redirect_url())
        try:
            cursor.execute(
                """
                INSERT INTO hardware_buttons (gpio_pin, action, item_type, item_id, debounce_ms, enabled)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    parsed["gpio_pin"],
                    parsed["action"],
                    parsed["item_type"],
                    parsed["item_id"],
                    parsed["debounce_ms"],
                    parsed["enabled"],
                ),
            )
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            message = str(exc)
            if "gpio" in message.lower() and "unique" in message.lower():
                flash("GPIO-Pin ist bereits einer Aktion zugewiesen.")
            else:
                flash(f"Hardware-Button konnte nicht angelegt werden: {message}")
            return redirect(_hardware_button_redirect_url())
        conn.commit()
        changed = True

    if changed:
        flash("Hardware-Button gespeichert.")
        _refresh_button_monitor_configuration()

    return redirect(_hardware_button_redirect_url())


@app.route("/hardware_buttons/<int:button_id>/update", methods=["POST"])
@login_required
def update_hardware_button(button_id: int):
    parsed, errors = _parse_hardware_button_form(request.form)
    if errors or parsed is None:
        for message in errors:
            flash(message)
        return redirect(_hardware_button_redirect_url())

    changed = False
    with get_db_connection() as (conn, cursor):
        cursor.execute(
            "SELECT id FROM hardware_buttons WHERE id=?",
            (button_id,),
        )
        if cursor.fetchone() is None:
            flash("Hardware-Button wurde nicht gefunden.")
            return redirect(_hardware_button_redirect_url())

        if parsed["item_type"] and parsed["item_id"] is not None:
            if not _hardware_button_target_exists(
                cursor, parsed["item_type"], parsed["item_id"]
            ):
                flash("Ausgewähltes Ziel existiert nicht mehr.")
                return redirect(_hardware_button_redirect_url())

        try:
            cursor.execute(
                """
                UPDATE hardware_buttons
                SET gpio_pin=?, action=?, item_type=?, item_id=?, debounce_ms=?, enabled=?
                WHERE id=?
                """,
                (
                    parsed["gpio_pin"],
                    parsed["action"],
                    parsed["item_type"],
                    parsed["item_id"],
                    parsed["debounce_ms"],
                    parsed["enabled"],
                    button_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            message = str(exc)
            if "gpio" in message.lower() and "unique" in message.lower():
                flash("GPIO-Pin ist bereits einer anderen Aktion zugewiesen.")
            else:
                flash(f"Hardware-Button konnte nicht aktualisiert werden: {message}")
            return redirect(_hardware_button_redirect_url())

        conn.commit()
        changed = True

    if changed:
        flash("Hardware-Button aktualisiert.")
        _refresh_button_monitor_configuration()

    return redirect(_hardware_button_redirect_url())


@app.route("/hardware_buttons/<int:button_id>/delete", methods=["POST"])
@login_required
def delete_hardware_button(button_id: int):
    deleted = False
    with get_db_connection() as (conn, cursor):
        cursor.execute(
            "DELETE FROM hardware_buttons WHERE id=?",
            (button_id,),
        )
        if cursor.rowcount:
            conn.commit()
            deleted = True
        else:
            flash("Hardware-Button wurde nicht gefunden.")
            return redirect(_hardware_button_redirect_url())

    if deleted:
        flash("Hardware-Button entfernt.")
        _refresh_button_monitor_configuration()

    return redirect(_hardware_button_redirect_url())


@app.errorhandler(RequestEntityTooLarge)
def handle_request_entity_too_large(error):
    limit_mb = app.config.get("MAX_CONTENT_LENGTH_MB")
    limit_is_int = isinstance(limit_mb, int)
    limit_display = limit_mb if limit_is_int else None
    if limit_display is None:
        message = "Die hochgeladene Datei überschreitet das erlaubte Upload-Limit."
    else:
        message = (
            f"Die hochgeladene Datei überschreitet das erlaubte Limit von {limit_display} MB."
        )

    _logger.warning(
        "Upload wegen überschrittenem Limit abgewiesen (Limit: %s MB).",
        limit_display if limit_display is not None else "unbekannt",
    )

    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        payload = {
            "error": "request_entity_too_large",
            "message": message,
        }
        if limit_display is not None:
            payload["limit_mb"] = limit_display
        return payload, 413

    flash(message)
    return (
        render_template(
            "upload_too_large.html",
            limit_mb=limit_display,
            message=message,
        ),
        413,
    )


@app.route("/upload", methods=["POST"])
@login_required
def upload():
    if "file" not in request.files:
        flash("Keine Datei ausgewählt")
        return redirect(url_for("index"))
    file = request.files["file"]
    if file.filename == "":
        flash("Keine Datei ausgewählt")
        return redirect(url_for("index"))
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        upload_folder = Path(app.config["UPLOAD_FOLDER"])
        file_path = upload_folder / filename
        if file_path.exists():
            base, ext = os.path.splitext(filename)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            attempt = 1
            while True:
                if attempt == 1:
                    candidate = f"{base}_{timestamp}{ext}"
                else:
                    candidate = f"{base}_{timestamp}_{attempt}{ext}"
                candidate_path = upload_folder / candidate
                if not candidate_path.exists():
                    filename = candidate
                    file_path = candidate_path
                    flash(
                        f"Dateiname bereits vorhanden, gespeichert als {filename} (Versuch {attempt})"
                    )
                    break
                attempt += 1
        else:
            flash("Datei hochgeladen")
        file.save(str(file_path))
        try:
            sound = AudioSegment.from_file(str(file_path))
            duration_seconds = len(sound) / 1000.0
        except Exception as exc:
            logging.error("Fehler beim Auslesen der Audiodauer von %s: %s", filename, exc)
            try:
                file_path.unlink()
            except OSError:
                pass
            flash("Audiodatei konnte nicht verarbeitet werden")
            return redirect(url_for("index"))
        with get_db_connection() as (conn, cursor):
            cursor.execute(
                "INSERT INTO audio_files (filename, duration_seconds) VALUES (?, ?)",
                (filename, duration_seconds),
            )
            conn.commit()
        return redirect(url_for("index"))
    flash("Dateiformat wird nicht unterstützt")
    return redirect(url_for("index"))


@app.route("/delete/<int:file_id>", methods=["POST"])
@login_required
def delete(file_id):
    with get_db_connection() as (conn, cursor):
        cursor.execute(
            "SELECT filename, duration_seconds FROM audio_files WHERE id=?",
            (file_id,),
        )
        row = cursor.fetchone()
        if not row:
            flash("Datei nicht gefunden")
            return redirect(url_for("index"))
        filename = row["filename"]
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        if os.path.exists(file_path):
            os.remove(file_path)
        cursor.execute("DELETE FROM audio_files WHERE id=?", (file_id,))
        cursor.execute("DELETE FROM playlist_files WHERE file_id=?", (file_id,))
        cursor.execute(
            "DELETE FROM schedules WHERE item_id=? AND item_type='file'", (file_id,)
        )
        conn.commit()
    flash("Datei gelöscht")
    return redirect(url_for("index"))


@app.route("/create_playlist", methods=["POST"])
@login_required
def create_playlist():
    name = (request.form.get("name") or "").strip()
    max_length = current_app.config.get("PLAYLIST_NAME_MAX_LENGTH", 100)
    if not name:
        flash("Playlist-Name darf nicht leer sein")
        return redirect(url_for("index"))
    if len(name) > int(max_length):
        flash(f"Playlist-Name darf maximal {int(max_length)} Zeichen lang sein")
        return redirect(url_for("index"))
    with get_db_connection() as (conn, cursor):
        cursor.execute("INSERT INTO playlists (name) VALUES (?)", (name,))
        conn.commit()
    flash("Playlist erstellt")
    return redirect(url_for("index"))


@app.route("/add_to_playlist", methods=["POST"])
@login_required
def add_to_playlist():
    playlist_id = request.form["playlist_id"]
    file_id = request.form["file_id"]
    with get_db_connection() as (conn, cursor):
        cursor.execute(
            "INSERT INTO playlist_files (playlist_id, file_id) VALUES (?, ?)",
            (playlist_id, file_id),
        )
        conn.commit()
    flash("Datei zur Playlist hinzugefügt")
    return redirect(url_for("index"))


@app.route("/delete_playlist/<int:playlist_id>", methods=["POST"])
@login_required
def delete_playlist(playlist_id):
    with get_db_connection() as (conn, cursor):
        cursor.execute("DELETE FROM playlists WHERE id=?", (playlist_id,))
        cursor.execute("DELETE FROM playlist_files WHERE playlist_id=?", (playlist_id,))
        cursor.execute(
            "DELETE FROM schedules WHERE item_id=? AND item_type='playlist'", (playlist_id,)
        )
        conn.commit()
    flash("Playlist gelöscht")
    return redirect(url_for("index"))


@app.route("/play_now/<string:item_type>/<int:item_id>", methods=["POST"])
@login_required
def play_now(item_type, item_id):
    if not pygame_available:
        _notify_audio_unavailable("Sofort-Wiedergabe nicht möglich")
        return redirect(url_for("index"))
    normalized_type = item_type.lower()
    if normalized_type not in PLAY_NOW_ALLOWED_TYPES:
        logging.warning("Ungültiger Sofort-Wiedergabe-Typ angefordert: %s", item_type)
        flash("Ungültiger Elementtyp für Sofort-Wiedergabe")
        return redirect(url_for("index"))

    delay = VERZOEGERUNG_SEC
    threading.Thread(
        target=play_item, args=(item_id, normalized_type, delay, False)
    ).start()
    flash("Abspielen gestartet")
    return redirect(url_for("index"))


@app.route("/toggle_pause", methods=["POST"])
@login_required
def toggle_pause():
    global is_paused
    if not pygame_available:
        _notify_audio_unavailable("Pausenstatus kann nicht geändert werden")
        return redirect(url_for("index"))
    if pygame.mixer.music.get_busy() or is_paused:
        if is_paused:
            pygame.mixer.music.unpause()
            is_paused = False
            logging.info("Wiedergabe fortgesetzt")
        else:
            pygame.mixer.music.pause()
            is_paused = True
            logging.info("Wiedergabe pausiert")
    return redirect(url_for("index"))


def _perform_stop_playback(*, flash_user: bool) -> bool:
    global is_paused
    if not pygame_available:
        _notify_audio_unavailable("Wiedergabe kann nicht gestoppt werden")
        return False
    pygame.mixer.music.stop()
    is_paused = False
    if not is_bt_connected():
        deactivate_amplifier()
    logging.info("Wiedergabe gestoppt")
    if is_bt_connected():
        resume_bt_audio()
        load_loopback()
    if flash_user and has_request_context():
        flash("Wiedergabe gestoppt")
    return True


@app.route("/stop_playback", methods=["POST"])
@login_required
def stop_playback():
    _perform_stop_playback(flash_user=True)
    return redirect(url_for("index"))


@app.route("/activate_amp", methods=["POST"])
@login_required
def activate_amp():
    if not GPIO_AVAILABLE:
        flash("lgpio nicht verfügbar, Endstufe kann nicht aktiviert werden.")
        return redirect(url_for("index"))
    try:
        activate_amplifier()
        flash("Endstufe aktiviert")
    except GPIOError as e:
        flash(f"Fehler beim Aktivieren der Endstufe: {str(e)}")
    return redirect(url_for("index"))


@app.route("/deactivate_amp", methods=["POST"])
@login_required
def deactivate_amp():
    if not GPIO_AVAILABLE:
        flash("lgpio nicht verfügbar, Endstufe kann nicht deaktiviert werden.")
        return redirect(url_for("index"))
    try:
        deactivate_amplifier()
        flash("Endstufe deaktiviert")
    except GPIOError as e:
        flash(f"Fehler beim Deaktivieren der Endstufe: {str(e)}")
    return redirect(url_for("index"))


def _execute_system_command(command, success_message, error_message):
    if isinstance(command, (list, tuple)):
        args = list(command)
    elif isinstance(command, str):
        args = shlex.split(command)
    elif command is None:
        args = []
    else:
        args = [str(command)]

    command_display = _describe_command(args)
    primary_command = _extract_primary_command(args)

    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:  # pragma: no cover - Fehlerfall hardwareabhängig
        logging.exception("Systemkommando %s fehlgeschlagen", command_display)
        flash(f"{error_message}: {exc}")
        return redirect(url_for("index"))

    if result.returncode == 0:
        logging.info("Systemkommando %s erfolgreich ausgeführt", command_display)
        flash(success_message)
        return redirect(url_for("index"))

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()

    logging.error(
        "Systemkommando %s fehlgeschlagen (Returncode %s): stdout=%s stderr=%s",
        command_display,
        result.returncode,
        stdout or "<leer>",
        stderr or "<leer>",
    )

    if _command_not_found(stderr, stdout, result.returncode):
        flash(f"{error_message}: {primary_command} nicht gefunden")
    else:
        detail = stderr or stdout
        if detail:
            flash(f"{error_message}: {detail}")
        else:
            flash(f"{error_message}: Rückgabecode {result.returncode}")

    return redirect(url_for("index"))


@app.route("/system/reboot", methods=["POST"])
@login_required
def system_reboot():
    return _execute_system_command(
        privileged_command("systemctl", "reboot"),
        "Systemneustart eingeleitet.",
        "Neustart konnte nicht gestartet werden",
    )


@app.route("/system/shutdown", methods=["POST"])
@login_required
def system_shutdown():
    return _execute_system_command(
        privileged_command("systemctl", "poweroff"),
        "Herunterfahren eingeleitet.",
        "Herunterfahren konnte nicht gestartet werden",
    )


@app.route("/set_relay_invert", methods=["POST"])
@login_required
def set_relay_invert():
    global RELAY_INVERT
    RELAY_INVERT = "invert" in request.form
    set_setting("relay_invert", "1" if RELAY_INVERT else "0")
    update_amp_levels()
    if amplifier_claimed:
        _set_amp_output(AMP_ON_LEVEL, keep_claimed=True)
    else:
        _set_amp_output(AMP_OFF_LEVEL, keep_claimed=False)
    flash("Relais-Logik invertiert" if RELAY_INVERT else "Relais-Logik normal")
    return redirect(url_for("index"))


@app.route("/settings/auto_reboot", methods=["POST"])
@login_required
def save_auto_reboot_settings():
    enabled = request.form.get("auto_reboot_enabled") == "on"
    mode = (request.form.get("auto_reboot_mode") or "daily").strip().lower()
    if mode not in {"daily", "weekly"}:
        flash("Ungültiger Modus für den automatischen Neustart.")
        return redirect(url_for("index"))
    time_raw = (request.form.get("auto_reboot_time") or "").strip()
    existing_time_value = get_setting(
        "auto_reboot_time", AUTO_REBOOT_DEFAULTS["auto_reboot_time"]
    )
    time_to_store = existing_time_value
    if time_raw:
        parsed_time = _parse_auto_reboot_time(time_raw)
        if parsed_time is None:
            flash("Ungültige Uhrzeit für den automatischen Neustart.")
            return redirect(url_for("index"))
        hour, minute = parsed_time
        time_to_store = f"{hour:02d}:{minute:02d}"
    else:
        parsed_existing = _parse_auto_reboot_time(time_to_store)
        if parsed_existing is None:
            if enabled:
                flash("Bitte eine gültige Uhrzeit für den automatischen Neustart wählen.")
                return redirect(url_for("index"))
            time_to_store = AUTO_REBOOT_DEFAULTS["auto_reboot_time"]
    weekday_raw = (request.form.get("auto_reboot_weekday") or "").strip().lower()
    if mode == "weekly":
        if weekday_raw not in AUTO_REBOOT_WEEKDAYS:
            flash("Bitte einen gültigen Wochentag auswählen.")
            return redirect(url_for("index"))
        weekday_to_store = weekday_raw
    else:
        existing_weekday = get_setting(
            "auto_reboot_weekday", AUTO_REBOOT_DEFAULTS["auto_reboot_weekday"]
        )
        weekday_to_store = (
            existing_weekday
            if existing_weekday in AUTO_REBOOT_WEEKDAYS
            else AUTO_REBOOT_DEFAULTS["auto_reboot_weekday"]
        )
    set_setting("auto_reboot_enabled", "1" if enabled else "0")
    set_setting("auto_reboot_mode", mode)
    set_setting("auto_reboot_time", time_to_store)
    set_setting("auto_reboot_weekday", weekday_to_store)
    update_auto_reboot_job()
    flash(
        "Automatischer Neustart aktiviert." if enabled else "Automatischer Neustart deaktiviert."
    )
    return redirect(url_for("index"))


@app.route("/network_settings", methods=["POST"])
@login_required
def save_network_settings():
    redirect_url = _network_settings_redirect_url()
    mode_raw = (request.form.get("mode") or "dhcp").strip().lower()
    mode = "manual" if mode_raw in {"manual", "static", "static_ipv4"} else "dhcp"

    manual_values = {
        "ipv4_address": (request.form.get("ipv4_address") or "").strip(),
        "ipv4_prefix": (request.form.get("ipv4_prefix") or "").strip(),
        "ipv4_gateway": (request.form.get("ipv4_gateway") or "").strip(),
        "dns_servers": (request.form.get("dns_servers") or "").strip(),
        "hostname": (request.form.get("hostname") or "").strip(),
        "local_domain": (request.form.get("local_domain") or "").strip(),
    }

    try:
        normalized_local_domain_input = _validate_local_domain(
            manual_values.get("local_domain", "")
        )
    except NetworkConfigError as exc:
        flash(str(exc), "network_settings")
        return redirect(redirect_url)

    manual_values["local_domain"] = normalized_local_domain_input

    payload: Dict[str, Any] = {"mode": mode, **manual_values}

    if mode != "manual":
        for key in (
            "ipv4_address",
            "ipv4_prefix",
            "ipv4_gateway",
            "dns_servers",
            "local_domain",
        ):
            payload[key] = ""

    normalized_result: NormalizedNetworkSettings
    try:
        normalized_result = _normalize_network_settings("wlan0", payload)
    except NetworkConfigError as exc:
        flash(str(exc), "network_settings")
        return redirect(redirect_url)
    except Exception:  # pragma: no cover - robuste Fehlerbehandlung
        logger.error("Validierung der Netzwerkeinstellungen fehlgeschlagen.", exc_info=True)
        flash(
            "Die Netzwerkeinstellungen konnten nicht validiert werden.",
            "network_settings",
        )
        return redirect(redirect_url)

    normalized_settings: Dict[str, str] = dict(normalized_result.normalized)

    try:
        current_hostname = _get_current_hostname()
    except Exception:  # pragma: no cover - defensiver Fallback
        current_hostname = socket.gethostname()

    hostname_input = manual_values.get("hostname", "")
    hostname_to_store = current_hostname
    if hostname_input:
        try:
            hostname_candidate = _validate_hostname(hostname_input)
        except NetworkConfigError as exc:
            flash(str(exc), "network_settings")
            return redirect(redirect_url)

        if hostname_candidate != current_hostname:
            command = privileged_command("hostnamectl", "set-hostname", hostname_candidate)
            try:
                result = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                )
            except Exception:
                logger.error("hostnamectl konnte nicht ausgeführt werden.", exc_info=True)
                flash(
                    "Hostname konnte nicht gesetzt werden (hostnamectl fehlgeschlagen).",
                    "network_settings",
                )
                return redirect(redirect_url)

            stdout_text = result.stdout
            stderr_text = result.stderr
            if _command_not_found(stderr_text, stdout_text, result.returncode):
                primary_command = _extract_primary_command(command)
                flash(
                    f"{primary_command} ist nicht verfügbar. Hostname wurde nicht geändert.",
                    "network_settings",
                )
                return redirect(redirect_url)
            if result.returncode != 0:
                logger.error(
                    "hostnamectl fehlgeschlagen – Rückgabewert %s, stderr: %s",
                    result.returncode,
                    stderr_text,
                )
                flash(
                    "Hostname konnte nicht gesetzt werden (hostnamectl meldete einen Fehler).",
                    "network_settings",
                )
                return redirect(redirect_url)

            hostname_to_store = hostname_candidate
        else:
            hostname_to_store = hostname_candidate

    try:
        _update_hosts_file(
            hostname_to_store,
            normalized_local_domain_input,
        )
    except NetworkConfigError as exc:
        flash(str(exc), "network_settings")
        return redirect(redirect_url)
    except Exception:
        logger.error("/etc/hosts konnte nicht aktualisiert werden.", exc_info=True)
        flash(
            "Die Host-Datei konnte nicht aktualisiert werden.",
            "network_settings",
        )
        return redirect(redirect_url)

    try:
        normalized_settings = _write_network_settings(
            "wlan0",
            payload,
            normalized_result=normalized_result,
        )
    except NetworkConfigError as exc:
        flash(str(exc), "network_settings")
        return redirect(redirect_url)
    except Exception:  # pragma: no cover - robuste Fehlerbehandlung
        logger.error("Fehler beim Speichern der Netzwerkeinstellungen", exc_info=True)
        if normalized_result.requires_update:
            try:
                _restore_network_backup(normalized_result)
            except Exception:  # pragma: no cover - defensiv loggen
                logger.error(
                    "Backup der Netzwerkkonfiguration konnte nicht wiederhergestellt werden.",
                    exc_info=True,
                )
        flash(
            "Beim Speichern der Netzwerkeinstellungen ist ein Fehler aufgetreten. Änderungen wurden zurückgesetzt.",
            "network_settings",
        )
        return redirect(redirect_url)

    normalized_settings["local_domain"] = normalized_local_domain_input

    settings_to_store: Dict[str, str] = dict(normalized_settings)
    settings_to_store["hostname"] = hostname_to_store

    try:
        for field, setting_key in NETWORK_SETTING_KEY_MAP.items():
            set_setting(setting_key, settings_to_store.get(field, ""))
    except Exception:
        logger.error(
            "Die Netzwerkeinstellungen konnten nicht in der Datenbank gesichert werden.",
            exc_info=True,
        )
        if normalized_result.requires_update:
            try:
                _restore_network_backup(normalized_result)
            except Exception:  # pragma: no cover - defensiv loggen
                logger.error(
                    "Backup der Netzwerkkonfiguration konnte nicht wiederhergestellt werden.",
                    exc_info=True,
                )
        flash(
            "Beim Aktualisieren der Einstellungen ist ein Fehler aufgetreten. Änderungen wurden zurückgesetzt.",
            "network_settings",
        )
        return redirect(redirect_url)

    if normalized_settings.get("mode") == "manual":
        flash("Statische IPv4-Konfiguration gespeichert.", "network_settings")
    else:
        flash("DHCP-Konfiguration aktiviert.", "network_settings")

    return redirect(redirect_url)


@app.route("/settings/amplifier_pin", methods=["POST"])
@login_required
def save_amplifier_pin():
    redirect_url = _amplifier_settings_redirect_url()
    raw_value = (request.form.get("amplifier_gpio_pin") or "").strip()

    if not raw_value:
        flash("GPIO-Pin für die Endstufe ist erforderlich.")
        return redirect(redirect_url)

    try:
        candidate = int(raw_value, 0)
    except ValueError:
        flash("GPIO-Pin für die Endstufe muss eine gültige Ganzzahl sein.")
        return redirect(redirect_url)

    if candidate < 0:
        flash("GPIO-Pin für die Endstufe darf nicht negativ sein.")
        return redirect(redirect_url)

    with get_db_connection() as (conn, cursor):
        cursor.execute(
            "SELECT id FROM hardware_buttons WHERE gpio_pin=? LIMIT 1",
            (candidate,),
        )
        conflict = cursor.fetchone()

    if conflict:
        flash(
            f"GPIO {candidate} ist bereits einem Hardware-Button zugewiesen. Bitte wähle einen anderen Pin."
        )
        return redirect(redirect_url)

    current_pin = GPIO_PIN_ENDSTUFE
    if candidate == current_pin:
        flash(f"Der Verstärker-Pin ist bereits auf GPIO {candidate} gesetzt.")
        return redirect(redirect_url)

    if amplifier_claimed:
        deactivate_amplifier()

    set_setting(AMPLIFIER_GPIO_PIN_SETTING_KEY, str(candidate))
    load_amplifier_gpio_pin_from_settings(log_source=True)
    deactivate_amplifier()

    flash(f"Verstärker-Pin auf GPIO {GPIO_PIN_ENDSTUFE} gespeichert.")
    return redirect(redirect_url)


@app.route("/settings/dac_sink", methods=["POST"])
@login_required
def save_dac_sink():
    new_sink = request.form.get("dac_sink_name", "")
    normalized_sink = new_sink.strip()
    set_setting(DAC_SINK_SETTING_KEY, normalized_sink if normalized_sink else None)
    load_dac_sink_from_settings()
    if normalized_sink:
        flash(f"Audio-Sink '{normalized_sink}' gespeichert.")
    else:
        flash("Audio-Sink auf Standardsink zurückgesetzt.")
    return redirect(url_for("index"))


@app.route("/settings/normalization_headroom", methods=["POST"])
@login_required
def save_normalization_headroom():
    raw_value = (request.form.get("normalization_headroom_db") or "").strip()
    env_override = os.environ.get(NORMALIZATION_HEADROOM_ENV_KEY)

    if not raw_value:
        set_setting(NORMALIZATION_HEADROOM_SETTING_KEY, None)
        flash(
            "Headroom zurückgesetzt. Der Standardwert "
            f"{DEFAULT_NORMALIZATION_HEADROOM_DB} dB wird verwendet."
        )
        if env_override:
            flash(
                "Hinweis: Die Umgebungsvariable"
                f" {NORMALIZATION_HEADROOM_ENV_KEY} (aktuell {env_override})"
                " bleibt weiterhin aktiv."
            )
        return redirect(url_for("index"))

    parsed_value = _parse_headroom_value(raw_value, "Formular 'Audio-Normalisierung'")
    if parsed_value is None:
        flash(
            "Ungültiger Zielpegel/Headroom-Wert. Bitte eine endliche Zahl eingeben."
        )
        return redirect(url_for("index"))

    headroom_value = abs(parsed_value)
    set_setting(NORMALIZATION_HEADROOM_SETTING_KEY, str(headroom_value))

    if parsed_value < 0:
        flash(
            "Zielpegel "
            f"{parsed_value:g} dB wird als Headroom {headroom_value:g} dB gespeichert."
        )
    elif parsed_value == 0:
        flash("Headroom auf 0 dB gesetzt (kein zusätzlicher Puffer).")
    else:
        flash(f"Headroom auf {headroom_value:g} dB gesetzt.")

    if env_override:
        flash(
            "Hinweis: Die Umgebungsvariable"
            f" {NORMALIZATION_HEADROOM_ENV_KEY} (aktuell {env_override})"
            " überschreibt den wirksamen Headroom/Zielpegel weiterhin."
        )
    return redirect(url_for("index"))


@app.route("/settings/schedule_default_volume", methods=["POST"])
@login_required
def save_schedule_default_volume():
    raw_value = (request.form.get("schedule_default_volume") or "").strip()
    percent_key = SCHEDULE_VOLUME_PERCENT_SETTING_KEY
    db_key = SCHEDULE_VOLUME_DB_SETTING_KEY

    if not raw_value:
        set_setting(percent_key, None)
        set_setting(db_key, None)
        details = get_schedule_default_volume_details()
        flash(
            "Standard-Lautstärke für Zeitpläne zurückgesetzt. "
            f"Es wird der Fallback von {details['percent']}% verwendet (Quelle: {details['source']})."
        )
        return redirect(url_for("index"))

    normalized_lower = raw_value.lower()
    is_db_candidate = (
        "db" in normalized_lower or normalized_lower.startswith("-") or normalized_lower.startswith("+")
    )
    percent_value: Optional[int] = None
    if not is_db_candidate:
        percent_value = _parse_schedule_volume_percent(raw_value)

    if percent_value is not None:
        set_setting(percent_key, str(percent_value))
        set_setting(db_key, None)
        details = get_schedule_default_volume_details()
        flash(
            "Standard-Lautstärke für neue Zeitpläne auf "
            f"{details['percent']}% gesetzt (Quelle: {details['source']})."
        )
        return redirect(url_for("index"))

    db_value = _parse_schedule_volume_db(raw_value)
    if db_value is None:
        flash(
            "Ungültiger Wert für die Standard-Lautstärke. Bitte Prozent (0–100) oder dB angeben."
        )
        return redirect(url_for("index"))

    set_setting(db_key, f"{db_value:g}")
    set_setting(percent_key, None)
    details = get_schedule_default_volume_details()
    db_display = details.get("db_value")
    if db_display is not None:
        flash(
            "Standard-Lautstärke für neue Zeitpläne basiert nun auf "
            f"{db_display:g} dB und entspricht {details['percent']}% (Quelle: {details['source']})."
        )
    else:
        flash(
            "Standard-Lautstärke für neue Zeitpläne aktualisiert."
        )
    return redirect(url_for("index"))


@app.route("/schedule", methods=["POST"])
@login_required
def add_schedule():
    volume_raw = (request.form.get("volume_percent") or "").strip()
    item_type = request.form["item_type"]
    item_id = request.form["item_id"]
    time_str = request.form["time"]  # Erwarte Format YYYY-MM-DDTHH:MM
    repeat = request.form["repeat"]
    delay_raw = request.form.get("delay", "0")
    try:
        delay = int(delay_raw)
    except (TypeError, ValueError):
        flash("Ungültige Verzögerung")
        return redirect(url_for("index"))

    if delay < 0:
        flash("Verzögerung darf nicht negativ sein")
        return redirect(url_for("index"))

    if delay > MAX_SCHEDULE_DELAY_SECONDS:
        flash("Verzögerung ist zu groß")
        return redirect(url_for("index"))
    start_date_input = request.form.get("start_date", "").strip()
    end_date_input = request.form.get("end_date", "").strip()
    day_of_month_value = None

    first_occurrence_date = None
    try:
        if repeat == "once":
            dt = parse_once_datetime(time_str)
        else:
            dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            if repeat == "monthly":
                day_of_month_value = dt.day
        if repeat == "once":
            time_only = dt.isoformat(timespec="seconds")
        else:
            time_only = dt.strftime("%H:%M:%S")
            if not validate_time(time_only):
                flash("Ungültiges Zeitformat")
                return redirect(url_for("index"))
    except ValueError:
        flash("Ungültiges Datums-/Zeitformat")
        return redirect(url_for("index"))

    try:
        start_date_value = None
        end_date_value = None
        start_date_dt = None
        end_date_dt = None
        if repeat != "once":
            if start_date_input:
                start_date_dt = datetime.strptime(start_date_input, "%Y-%m-%d").date()
            else:
                start_date_dt = dt.date()
            start_date_value = start_date_dt.isoformat()
            if repeat == "daily":
                first_occurrence_date = start_date_dt
            if end_date_input:
                end_date_dt = datetime.strptime(end_date_input, "%Y-%m-%d").date()
                if end_date_dt < start_date_dt:
                    flash("Enddatum darf nicht vor dem Startdatum liegen")
                    return redirect(url_for("index"))
                end_date_value = end_date_dt.isoformat()
            if repeat == "monthly":
                if day_of_month_value is None:
                    day_of_month_value = dt.day
                try:
                    first_occurrence = calculate_first_monthly_occurrence(
                        start_date_dt, day_of_month_value
                    )
                except ValueError:
                    flash("Ungültiger Tag für monatlichen Zeitplan")
                    return redirect(url_for("index"))
                if end_date_dt and first_occurrence > end_date_dt:
                    flash(
                        "Der gewählte Zeitraum enthält keinen gültigen Ausführungstag für den Zeitplan"
                    )
                    return redirect(url_for("index"))
                first_occurrence_date = first_occurrence
        else:
            start_date_value = None
            end_date_value = None
            local_dt = _to_local_aware(dt)
            first_occurrence_date = (
                local_dt.date() if local_dt is not None else dt.date()
            )
    except ValueError:
        flash("Ungültiges Start- oder Enddatum")
        return redirect(url_for("index"))

    if item_type not in ("file", "playlist"):
        flash("Ungültiger Typ ausgewählt")
        return redirect(url_for("index"))

    if not item_id:
        flash("Kein Element gewählt")
        return redirect(url_for("index"))

    if volume_raw == "":
        volume_percent = get_schedule_default_volume_percent()
    else:
        try:
            volume_percent = int(volume_raw)
        except (TypeError, ValueError):
            flash("Ungültiger Lautstärke-Wert für den Zeitplan")
            return redirect(url_for("index"))
        if not 0 <= volume_percent <= 100:
            flash("Lautstärke für den Zeitplan muss zwischen 0% und 100% liegen")
            return redirect(url_for("index"))

    new_schedule_record = {
        "item_id": item_id,
        "item_type": item_type,
        "time": time_only,
        "repeat": repeat,
        "delay": delay,
        "start_date": start_date_value,
        "end_date": end_date_value,
        "day_of_month": day_of_month_value,
    }

    with get_db_connection() as (conn, cursor):
        duration_seconds = _get_item_duration(cursor, item_type, item_id)
        if duration_seconds is not None:
            if first_occurrence_date is None and repeat != "once":
                first_occurrence_date = parse_schedule_date(start_date_value)
            if first_occurrence_date is not None:
                if _has_schedule_conflict(
                    cursor,
                    new_schedule_record,
                    duration_seconds,
                    first_occurrence_date,
                ):
                    flash(
                        "Zeitplan überschneidet sich mit einer bestehenden Wiedergabe"
                    )
                    return redirect(url_for("index"))
        cursor.execute(
            """
            INSERT INTO schedules (
                item_id,
                item_type,
                time,
                repeat,
                delay,
                start_date,
                end_date,
                day_of_month,
                executed,
                volume_percent
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                item_id,
                item_type,
                time_only,
                repeat,
                delay,
                start_date_value,
                end_date_value,
                day_of_month_value,
                volume_percent,
            ),
        )
        conn.commit()
    load_schedules()
    flash("Zeitplan hinzugefügt")
    return redirect(url_for("index"))


@app.route("/delete_schedule/<int:sch_id>", methods=["POST"])
@login_required
def delete_schedule(sch_id):
    with get_db_connection() as (conn, cursor):
        cursor.execute("DELETE FROM schedules WHERE id=?", (sch_id,))
        conn.commit()
    load_schedules()
    flash("Zeitplan gelöscht")
    return redirect(url_for("index"))


@app.route("/wlan_scan", methods=["POST"])
@login_required
def wlan_scan():
    base_cmd = privileged_command("wpa_cli", "-i", "wlan0")
    fallback_message = "Scan nicht möglich, wpa_cli fehlt oder meldet einen Fehler"

    try:
        _run_wpa_cli(
            base_cmd + ["scan"],
            log_context="wpa_cli Scan",
            flash_on_error=True,
            fallback_message=fallback_message,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return render_template("scan.html", networks=fallback_message)

    success, scan_output = _run_wifi_tool(
        base_cmd + ["scan_results"],
        fallback_message,
        "wpa_cli Scan-Ergebnisse",
        flash_on_error=True,
    )

    if not success:
        return render_template("scan.html", networks=scan_output)

    formatted = _format_wpa_cli_scan_results(scan_output)
    return render_template("scan.html", networks=formatted)


def _quote_wpa_cli(value: str) -> str:
    """Gibt einen sicher in doppelte Anführungszeichen gesetzten String zurück."""

    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _format_ssid_for_wpa_cli(ssid: str) -> str:
    """Bereitet die SSID so auf, dass `wpa_cli` Unicode zuverlässig übernimmt."""

    if all(32 <= ord(char) <= 126 for char in ssid):
        return _quote_wpa_cli(ssid)
    return "0x" + ssid.encode("utf-8").hex()


def _is_hex_psk(candidate: str) -> bool:
    """Erkennt 64-stellige hexadezimale WPA2-PSKs."""

    return len(candidate) == 64 and all(
        char in "0123456789abcdefABCDEF" for char in candidate
    )


def _format_wpa_cli_scan_results(raw_output: str) -> str:
    lines = [line for line in raw_output.splitlines() if line.strip()]
    if not lines:
        return "Keine Netzwerke gefunden."

    if lines and lines[0].lower().startswith("bssid"):
        data_lines = lines[1:]
    else:
        data_lines = lines

    networks = []
    fallback_lines = []

    def _format_frequency(value: str) -> str:
        value = value.strip()
        if not value:
            return "unbekannt"
        try:
            return f"{int(value)} MHz"
        except ValueError:
            return value

    def _format_signal(value: str) -> str:
        value = value.strip()
        if not value:
            return "unbekannt"
        try:
            return f"{int(value)} dBm"
        except ValueError:
            return value

    for line in data_lines:
        parts = line.split("\t")
        if len(parts) < 4:
            fallback_lines.append(line.strip())
            continue

        bssid = parts[0].strip()
        frequency = parts[1].strip() if len(parts) > 1 else ""
        signal_level = parts[2].strip() if len(parts) > 2 else ""
        flags = parts[3].strip() if len(parts) > 3 else ""
        extras = parts[4:]
        ssid = extras[-1].strip() if extras else ""
        metadata = [value.strip() for value in extras[:-1] if value.strip()]

        networks.append(
            {
                "ssid": ssid,
                "bssid": bssid,
                "frequency": _format_frequency(frequency),
                "signal": _format_signal(signal_level),
                "flags": flags or "-",
                "metadata": metadata,
            }
        )

    if not networks:
        if fallback_lines:
            return "\n".join(fallback_lines)
        return "Keine Netzwerke gefunden."

    formatted_lines = [f"Gefundene Netzwerke: {len(networks)}"]

    for entry in networks:
        ssid_display = entry["ssid"] or "<verborgen>"
        block_lines = [
            f"SSID: {ssid_display}",
            f"  Signal: {entry['signal']} @ {entry['frequency']}",
            f"  Flags: {entry['flags']}",
            f"  BSSID: {entry['bssid']}",
        ]
        if entry["metadata"]:
            block_lines.append("  Zusatzfelder: " + ", ".join(entry["metadata"]))
        formatted_lines.append("\n".join(block_lines))

    return "\n\n".join(formatted_lines)


def _run_wpa_cli(
    args,
    expect_ok=True,
    *,
    log_context: Optional[str] = None,
    flash_on_error: bool = False,
    fallback_message: Optional[str] = None,
):
    context = log_context or "wpa_cli-Aufruf"
    primary_command = _extract_primary_command(args)
    command_display = _describe_command(args)

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        logging.error(
            "%s nicht gefunden: %s (%s)",
            context,
            primary_command,
            exc,
        )
        if flash_on_error and fallback_message:
            flash(fallback_message, "error")
        raise

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    combined = "\n".join(filter(None, [stdout, stderr]))

    fail_indicator = "FAIL" in stdout or "FAIL" in stderr

    if result.returncode != 0 or fail_indicator:
        logging.error(
            "%s fehlgeschlagen (Exit-Code %s): %s (Kommando: %s)",
            context,
            result.returncode,
            combined or "Keine Ausgabe",
            command_display,
        )
        if flash_on_error and fallback_message:
            flash(fallback_message, "error")
        raise subprocess.CalledProcessError(
            result.returncode or 1,
            args,
            output=stdout,
            stderr=stderr,
        )

    if expect_ok and "OK" not in stdout:
        logging.error(
            "%s ohne OK-Antwort: %s (Kommando: %s)",
            context,
            combined or "Keine Ausgabe",
            command_display,
        )
        if flash_on_error and fallback_message:
            flash(fallback_message, "error")
        raise subprocess.CalledProcessError(
            result.returncode or 1,
            args,
            output=stdout,
            stderr=stderr,
        )

    return stdout


@app.route("/wlan_connect", methods=["POST"])
@login_required
def wlan_connect():
    ssid = request.form["ssid"]
    raw_password = request.form.get("password", "")
    formatted_ssid = _format_ssid_for_wpa_cli(ssid)
    is_open_network = raw_password == ""
    is_hex_psk = _is_hex_psk(raw_password)

    if not is_open_network and not is_hex_psk:
        if len(raw_password) < 8 or len(raw_password) > 63:
            flash(
                "Ungültiges WLAN-Passwort: Es muss zwischen 8 und 63 Zeichen lang sein oder"
                " eine 64-stellige Hex-Passphrase sein."
            )
            logging.warning(
                "WLAN-Verbindung zu SSID '%s' abgebrochen: Passphrase-Länge %s unzulässig.",
                ssid,
                len(raw_password),
            )
            return redirect(url_for("index"))
    base_cmd = privileged_command("wpa_cli", "-i", "wlan0")
    net_id: Optional[str] = None

    base_cli_name = _extract_primary_command(base_cmd)
    not_found_message = (
        f"{base_cli_name} nicht gefunden oder keine Berechtigung. Bitte Installation prüfen."
    )

    try:
        net_id = _run_wpa_cli(base_cmd + ["add_network"], expect_ok=False).strip()
        _run_wpa_cli(base_cmd + ["set_network", net_id, "ssid", formatted_ssid])
        if is_open_network:
            _run_wpa_cli(base_cmd + ["set_network", net_id, "key_mgmt", "NONE"])
            _run_wpa_cli(base_cmd + ["set_network", net_id, "auth_alg", "OPEN"])
        else:
            if is_hex_psk:
                psk_value = raw_password
            else:
                psk_value = _quote_wpa_cli(raw_password)
            _run_wpa_cli(base_cmd + ["set_network", net_id, "psk", psk_value])
        _run_wpa_cli(base_cmd + ["enable_network", net_id])
        _run_wpa_cli(base_cmd + ["save_config"])
        _run_wpa_cli(base_cmd + ["reconfigure"])
        flash("Versuche, mit WLAN zu verbinden")
    except FileNotFoundError as e:
        logging.error("wpa_cli nicht gefunden oder nicht ausführbar: %s", e)
        flash(not_found_message)
    except subprocess.CalledProcessError as e:
        logging.error(
            "Fehler beim WLAN-Verbindungsaufbau: %s (stdout: %s, stderr: %s)",
            e,
            getattr(e, "output", ""),
            getattr(e, "stderr", ""),
        )
        primary_command = _extract_primary_command(e.cmd or base_cmd)
        if primary_command and primary_command != "<unbekannt>":
            not_found_message = (
                f"{primary_command} nicht gefunden oder keine Berechtigung. Bitte Installation prüfen."
            )
        if net_id:
            try:
                _run_wpa_cli(base_cmd + ["remove_network", net_id], expect_ok=False)
                logging.info(
                    "Unvollständiges WLAN-Netzwerk %s nach Fehler entfernt.", net_id
                )
            except Exception as cleanup_error:  # pragma: no cover - Logging wird getestet
                logging.warning(
                    "Aufräumen des WLAN-Netzwerks %s fehlgeschlagen: %s",
                    net_id,
                    cleanup_error,
                )
        if _command_not_found(
            getattr(e, "stderr", ""), getattr(e, "output", ""), e.returncode
        ):
            flash(not_found_message)
        flash("Fehler beim WLAN-Verbindungsaufbau. Details im Log einsehbar.")
    return redirect(url_for("index"))


@app.route("/volume", methods=["POST"])
@login_required
def set_volume():
    vol = request.form.get("volume")
    try:
        int_vol = int(vol)
    except (TypeError, ValueError):
        flash("Ungültiger Lautstärke-Wert")
        return redirect(url_for("index"))

    if not 0 <= int_vol <= 100:
        flash("Ungültiger Lautstärke-Wert")
        return redirect(url_for("index"))

    info_on_missing_pygame = False

    try:
        if pygame_available:
            pygame.mixer.music.set_volume(int_vol / 100.0)
        else:
            info_on_missing_pygame = True
            message = (
                "pygame nicht verfügbar, setze ausschließlich die Systemlautstärke."
            )
            logging.info(message)
            if has_request_context():
                flash(message)
        current_sink = get_current_sink()
        if isinstance(current_sink, str):
            current_sink = current_sink.strip()
        sink_for_pactl = (
            current_sink if current_sink not in (None, "", "Nicht verfügbar") else None
        )
        commands = []
        if sink_for_pactl is None:
            logging.info(
                "Kein gültiger PulseAudio-Sink ermittelt; verwende Platzhalter '@DEFAULT_SINK@'."
            )
            sink_for_pactl = "@DEFAULT_SINK@"
        if sink_for_pactl:
            commands.append(["pactl", "set-sink-volume", sink_for_pactl, f"{int_vol}%"])
        else:
            logging.info("Überspringe pactl-Aufruf, da kein Sink verfügbar ist.")
        if is_sudo_disabled():
            persistent_command = privileged_command(
                "systemctl", "start", "audio-pi-alsactl.service"
            )
            logging.debug(
                "Persistente Lautstärke wird über systemctl start audio-pi-alsactl.service ausgelöst."
            )
        else:
            persistent_command = privileged_command("alsactl", "store")
            logging.debug(
                "Persistente Lautstärke wird direkt über alsactl store gesichert (sudo aktiv)."
            )
        commands.extend(
            [
                ["amixer", "sset", "Master", f"{int_vol}%"],
                persistent_command,
            ]
        )
        audio_command_success = False
        persistence_success = False
        for command in commands:
            command_display = _describe_command(command)
            primary_command = _extract_primary_command(command)
            try:
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except FileNotFoundError:
                message: Optional[str] = None
                if primary_command == "pactl":
                    _notify_missing_pactl()
                else:
                    message = (
                        f"Kommando '{primary_command}' wurde nicht gefunden."
                    )
                    logging.warning(
                        "%s (ausgeführt als: %s)",
                        message,
                        command_display,
                    )
                if message is not None:
                    flash(message)
            except subprocess.CalledProcessError as exc:
                failing_command = command
                if isinstance(exc.cmd, Sequence) and not isinstance(
                    exc.cmd, (str, bytes)
                ):
                    failing_command = exc.cmd
                if not failing_command:
                    failing_command = command
                command_display = _describe_command(failing_command)
                primary_command = _extract_primary_command(failing_command)
                if primary_command == "pactl":
                    _notify_missing_pactl()
                message = (
                    f"Kommando '{primary_command}' fehlgeschlagen (Code {exc.returncode})."
                )
                logging.warning(
                    "%s Ausgeführt als: %s stdout: %s stderr: %s",
                    message,
                    command_display,
                    exc.stdout or "",
                    exc.stderr or "",
                )
                flash(message)
            else:
                if primary_command in {"pactl", "amixer"}:
                    audio_command_success = True
                if command is persistent_command:
                    persistence_success = True
    except Exception as e:
        logging.error(f"Fehler beim Setzen der Lautstärke: {e}")
        flash("Fehler beim Setzen der Lautstärke")
    else:
        if audio_command_success:
            if persistence_success:
                logging.info(f"Lautstärke auf {int_vol}% gesetzt (persistent)")
                flash("Lautstärke persistent gesetzt")
                if info_on_missing_pygame and has_request_context():
                    # Nachricht bereits geflasht, kein weiterer Hinweis nötig
                    pass
            else:
                logging.warning(
                    "Lautstärke gesetzt, konnte aber nicht persistent gespeichert werden."
                )
                flash(
                    "Lautstärke gesetzt, konnte aber nicht persistent gespeichert werden"
                )
        else:
            logging.error("Lautstärke konnte mit den verfügbaren Werkzeugen nicht gesetzt werden.")
            flash("Lautstärke konnte nicht gesetzt werden")
    return redirect(url_for("index"))


def _read_log_tail(path: Path, *, max_bytes: int, max_lines: int) -> Tuple[List[str], bool]:
    effective_max_bytes = max_bytes if max_bytes > 0 else DEFAULT_LOG_VIEW_MAX_BYTES
    effective_max_lines = max_lines if max_lines > 0 else DEFAULT_LOG_VIEW_MAX_LINES

    file_size = path.stat().st_size
    truncated = False
    start_offset = 0

    if file_size > effective_max_bytes:
        truncated = True
        start_offset = file_size - effective_max_bytes

    with path.open("rb") as handle:
        if start_offset:
            handle.seek(start_offset)
        data = handle.read()

    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()

    if start_offset and data[:1] not in (b"\n", b"\r"):
        if lines:
            lines = lines[1:]

    if len(lines) > effective_max_lines:
        truncated = True
        lines = list(deque(lines, maxlen=effective_max_lines))
    else:
        lines = list(lines)

    return lines, truncated


@app.route("/logs")
@login_required
def logs():
    log_path = Path(current_app.config.get("LOG_VIEW_FILE", DEFAULT_LOG_FILE_NAME))
    max_bytes = int(current_app.config.get("LOG_VIEW_MAX_BYTES", DEFAULT_LOG_VIEW_MAX_BYTES))
    max_lines = int(current_app.config.get("LOG_VIEW_MAX_LINES", DEFAULT_LOG_VIEW_MAX_LINES))

    missing_file = False
    truncated = False
    log_lines: List[str]

    try:
        log_lines, truncated = _read_log_tail(
            log_path,
            max_bytes=max_bytes,
            max_lines=max_lines,
        )
    except FileNotFoundError:
        missing_file = True
        log_lines = []
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "Logdatei %s konnte nicht gelesen werden: %s",
            log_path,
            exc,
        )
        log_lines = []

    return render_template(
        "logs.html",
        logs=log_lines,
        missing_file=missing_file,
        truncated=truncated,
        max_lines=max_lines,
        max_bytes=max_bytes,
        max_bytes_label=f"{max_bytes / 1024:.1f}",
        log_path=str(log_path),
    )


@app.route("/change_password", methods=["GET", "POST"])
@login_required
def change_password():
    force_change = getattr(current_user, "must_change_password", False)
    if request.method == "POST":
        old_pass = request.form["old_password"]
        new_pass = request.form["new_password"]
        if not new_pass or len(new_pass) < 8:
            flash("Neues Passwort zu kurz")
            return render_template("change_password.html", force_change=force_change)
        if new_pass == old_pass:
            flash("Neues Passwort muss sich vom alten unterscheiden")
            return render_template("change_password.html", force_change=force_change)
        with get_db_connection() as (conn, cursor):
            cursor.execute("SELECT password FROM users WHERE id=?", (current_user.id,))
            result = cursor.fetchone()
            if result and check_password_hash(result["password"], old_pass):
                new_hashed = generate_password_hash(new_pass)
                cursor.execute(
                    "UPDATE users SET password=?, must_change_password=0 WHERE id=?",
                    (new_hashed, current_user.id),
                )
                conn.commit()
                current_user.must_change_password = False
                flash("Passwort geändert")
            else:
                flash("Falsches altes Passwort")
                return render_template("change_password.html", force_change=force_change)
        return redirect(url_for("index"))
    return render_template("change_password.html", force_change=force_change)


TIME_SYNC_INTERNET_SETTING_KEY = "time_sync_internet_default"


def perform_internet_time_sync():
    success = False
    messages: List[str] = []
    success_message: Optional[str] = None
    cleanup_failed = False
    extra_restart_cleanup_enabled = (
        os.environ.get("AUDIO_PI_FORCE_EXTRA_TIMESYNC_RESTART", "")
        .strip()
        .lower()
        in {"1", "true", "yes", "on"}
    )
    disable_command = privileged_command("timedatectl", "set-ntp", "false")
    enable_command = privileged_command("timedatectl", "set-ntp", "true")
    restart_command = privileged_command(
        "systemctl", "restart", "systemd-timesyncd"
    )
    commands_to_run = [disable_command, enable_command, restart_command]
    current_command = None
    disable_completed = False
    enable_completed = False
    restart_completed = False
    try:
        for current_command in commands_to_run:
            subprocess.run(
                current_command,
                check=True,
                capture_output=True,
                text=True,
            )
            if current_command is disable_command:
                disable_completed = True
            elif current_command is enable_command:
                enable_completed = True
            elif current_command is restart_command:
                restart_completed = True
    except FileNotFoundError as exc:
        primary_command = exc.filename
        if not primary_command and current_command:
            primary_command = _extract_primary_command(current_command)
        primary_command = primary_command or "unbekannt"
        logging.error(
            "Zeit-Synchronisation fehlgeschlagen, Kommando '%s' nicht gefunden: %s",
            primary_command,
            exc,
        )
        messages.append(
            f"Kommando '{primary_command}' nicht gefunden, Internet-Sync abgebrochen"
        )
    except subprocess.CalledProcessError as exc:
        failing_command = exc.cmd if exc.cmd else current_command
        primary_command = _extract_primary_command(failing_command or [])
        stderr_text = exc.stderr if isinstance(exc.stderr, str) else ""
        stdout_text = exc.stdout if isinstance(exc.stdout, str) else ""
        if _command_not_found(stderr_text, stdout_text, exc.returncode):
            logging.error(
                "Zeit-Synchronisation fehlgeschlagen, Kommando '%s' nicht gefunden: %s",
                primary_command,
                stderr_text or exc,
            )
            messages.append(
                f"Kommando '{primary_command}' nicht gefunden, Internet-Sync abgebrochen"
            )
        else:
            logging.error(
                "Zeit-Synchronisation fehlgeschlagen (%s): %s", failing_command, exc
            )
            messages.append("Fehler bei der Synchronisation")
    except Exception as exc:  # pragma: no cover - unerwartete Fehler
        logging.error("Unerwarteter Fehler bei der Zeit-Synchronisation: %s", exc)
        messages.append("Fehler bei der Synchronisation")
    else:
        success = True
        success_message = "Zeit vom Internet synchronisiert"
        try:
            set_rtc(datetime.now())
        except RTCWriteError as exc:
            logging.warning(
                "RTC konnte nach dem Internet-Sync nicht geschrieben werden: %s",
                exc,
            )
            messages.append("RTC konnte nicht aktualisiert werden (I²C-Schreibfehler)")
        except (RTCUnavailableError, UnsupportedRTCError) as exc:
            logging.warning(
                "RTC konnte nach dem Internet-Sync nicht gesetzt werden: %s", exc
            )
            messages.append("RTC konnte nicht aktualisiert werden")
    finally:
        if disable_completed and not enable_completed:
            try:
                subprocess.run(
                    enable_command,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                enable_completed = True
            except subprocess.CalledProcessError as exc:
                failing_command = exc.cmd if exc.cmd else enable_command
                primary_command = _extract_primary_command(failing_command or [])
                stderr_text = exc.stderr if isinstance(exc.stderr, str) else ""
                stdout_text = exc.stdout if isinstance(exc.stdout, str) else ""
                if _command_not_found(stderr_text, stdout_text, exc.returncode):
                    logging.warning(
                        "timedatectl konnte nach dem Internet-Sync nicht ausgeführt werden: %s",
                        stderr_text or exc,
                    )
                    messages.append(
                        f"Kommando '{primary_command}' nicht gefunden, NTP konnte nach dem Sync nicht reaktiviert werden"
                    )
                else:
                    logging.warning(
                        "timedatectl konnte nach dem Internet-Sync nicht auf 'true' gestellt werden (Exit-Code): %s",
                        exc,
                    )
                    messages.append(
                        "timedatectl konnte NTP nach dem Sync nicht wieder aktivieren (siehe Logs für Details)"
                    )
                cleanup_failed = True
                success = False
            except FileNotFoundError as exc:
                primary_command = exc.filename or _extract_primary_command(enable_command)
                logging.warning(
                    "timedatectl konnte nach dem Internet-Sync nicht ausgeführt werden: %s",
                    exc,
                )
                messages.append(
                    f"Kommando '{primary_command}' nicht gefunden, NTP konnte nach dem Sync nicht reaktiviert werden"
                )
                cleanup_failed = True
                success = False
            except Exception as exc:  # pragma: no cover - unerwartete Fehler
                logging.warning(
                    "Unerwarteter Fehler beim Aktivieren von timedatectl nach dem Internet-Sync: %s",
                    exc,
                )
                messages.append(
                    "timedatectl konnte NTP nach dem Sync nicht wieder aktivieren (unerwarteter Fehler, bitte Logs prüfen)"
                )
                cleanup_failed = True
                success = False
        if enable_completed and not restart_completed:
            try:
                subprocess.run(
                    restart_command,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                restart_completed = True
            except subprocess.CalledProcessError as exc:
                failing_command = exc.cmd if exc.cmd else restart_command
                primary_command = _extract_primary_command(failing_command or [])
                stderr_text = exc.stderr if isinstance(exc.stderr, str) else ""
                stdout_text = exc.stdout if isinstance(exc.stdout, str) else ""
                if _command_not_found(stderr_text, stdout_text, exc.returncode):
                    logging.warning(
                        "systemd-timesyncd konnte nicht neu gestartet werden, da Kommando '%s' fehlt: %s",
                        primary_command,
                        stderr_text or exc,
                    )
                    messages.append(
                        f"Kommando '{primary_command}' nicht gefunden, systemd-timesyncd konnte nicht neu gestartet werden"
                    )
                else:
                    logging.warning(
                        "systemd-timesyncd konnte nach dem Internet-Sync nicht neu gestartet werden (Exit-Code): %s",
                        exc,
                    )
                    messages.append(
                        "systemd-timesyncd konnte nicht neu gestartet werden (siehe Logs für Details)"
                    )
                cleanup_failed = True
                success = False
            except FileNotFoundError as exc:
                primary_command = exc.filename or _extract_primary_command(restart_command)
                logging.warning(
                    "systemd-timesyncd konnte nicht neu gestartet werden, da systemctl nicht verfügbar ist oder Berechtigungen fehlen: %s",
                    exc,
                )
                messages.append(
                    f"Kommando '{primary_command}' nicht gefunden, systemd-timesyncd konnte nicht neu gestartet werden"
                )
                cleanup_failed = True
                success = False
            except Exception as exc:  # pragma: no cover - unerwartete Fehler
                logging.warning(
                    "Unerwarteter Fehler beim Neustart von systemd-timesyncd nach dem Internet-Sync: %s",
                    exc,
                )
                messages.append(
                    "systemd-timesyncd konnte nicht neu gestartet werden (unerwarteter Fehler, bitte Logs prüfen)"
                )
                cleanup_failed = True
                success = False
        if success and not cleanup_failed and extra_restart_cleanup_enabled:
            try:
                subprocess.run(
                    restart_command,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as exc:
                failing_command = exc.cmd if exc.cmd else restart_command
                primary_command = _extract_primary_command(failing_command or [])
                stderr_text = exc.stderr if isinstance(exc.stderr, str) else ""
                stdout_text = exc.stdout if isinstance(exc.stdout, str) else ""
                if _command_not_found(stderr_text, stdout_text, exc.returncode):
                    logging.warning(
                        "systemd-timesyncd konnte nicht neu gestartet werden, da Kommando '%s' fehlt: %s",
                        primary_command,
                        stderr_text or exc,
                    )
                    messages.append(
                        f"Kommando '{primary_command}' nicht gefunden, systemd-timesyncd konnte nicht neu gestartet werden",
                    )
                else:
                    logging.warning(
                        "systemd-timesyncd konnte nach dem Internet-Sync nicht neu gestartet werden (Cleanup-Phase, Exit-Code): %s",
                        exc,
                    )
                    messages.append(
                        "systemd-timesyncd konnte nicht neu gestartet werden (siehe Logs für Details)",
                    )
                cleanup_failed = True
                success = False
            except FileNotFoundError as exc:
                primary_command = exc.filename or _extract_primary_command(restart_command)
                logging.warning(
                    "systemd-timesyncd konnte im Cleanup nicht neu gestartet werden, da systemctl nicht verfügbar ist oder Berechtigungen fehlen: %s",
                    exc,
                )
                messages.append(
                    f"Kommando '{primary_command}' nicht gefunden, systemd-timesyncd konnte nicht neu gestartet werden",
                )
                cleanup_failed = True
                success = False
            except Exception as exc:  # pragma: no cover - unerwartete Fehler
                logging.warning(
                    "Unerwarteter Fehler beim Neustart von systemd-timesyncd während des Cleanup-Schritts nach dem Internet-Sync: %s",
                    exc,
                )
                messages.append(
                    "systemd-timesyncd konnte nicht neu gestartet werden (unerwarteter Fehler, bitte Logs prüfen)",
                )
                cleanup_failed = True
                success = False
    final_success = success and not cleanup_failed
    if final_success and success_message:
        messages.append(success_message)
    return final_success, messages


def _is_checked(value):
    if value is None:
        return False
    return value.lower() in {"1", "true", "yes", "on"}


@app.route("/set_time", methods=["GET", "POST"])
@login_required
def set_time():
    stored_sync_default = _is_checked(get_setting(TIME_SYNC_INTERNET_SETTING_KEY, "0"))
    if request.method == "POST":
        time_str = request.form.get("datetime")
        sync_checkbox = _is_checked(request.form.get("sync_internet"))
        set_setting(TIME_SYNC_INTERNET_SETTING_KEY, "1" if sync_checkbox else "0")
        if not time_str:
            flash("Ungültiges Datums-/Zeitformat")
            return redirect(url_for("set_time"))
        try:
            dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        except ValueError:
            flash("Ungültiges Datums-/Zeitformat")
            return redirect(url_for("set_time"))
        else:
            time_value = dt.strftime("%Y-%m-%d %H:%M:%S")
            command = privileged_command("timedatectl", "set-time", time_value)
            try:
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except FileNotFoundError as exc:
                primary_command = exc.filename or _extract_primary_command(command)
                logging.error(
                    "timedatectl zum Setzen der Systemzeit nicht gefunden (%s): %s",
                    primary_command,
                    exc,
                )
                flash(
                    f"Kommando '{primary_command}' wurde nicht gefunden. Systemzeit konnte nicht gesetzt werden."
                )
                return redirect(url_for("set_time"))
            except subprocess.CalledProcessError as exc:
                failing_command = exc.cmd if exc.cmd else command
                primary_command = _extract_primary_command(failing_command or [])
                stderr_text = exc.stderr if isinstance(exc.stderr, str) else ""
                stdout_text = exc.stdout if isinstance(exc.stdout, str) else ""
                if _command_not_found(stderr_text, stdout_text, exc.returncode):
                    logging.error(
                        "timedatectl zum Setzen der Systemzeit nicht gefunden (%s): %s",
                        primary_command,
                        stderr_text or exc,
                    )
                    flash(
                        f"Kommando '{primary_command}' wurde nicht gefunden. Systemzeit konnte nicht gesetzt werden."
                    )
                else:
                    logging.error(
                        "Systemzeit setzen fehlgeschlagen (%s): %s",
                        failing_command,
                        exc,
                    )
                    executed_command = (
                        " ".join(map(str, failing_command))
                        if isinstance(failing_command, (list, tuple))
                        else str(failing_command)
                    )
                    flash(
                        f"Ausführung von '{executed_command}' ist fehlgeschlagen. Systemzeit konnte nicht gesetzt werden."
                    )
                return redirect(url_for("set_time"))
            except Exception as exc:  # Fallback, um unerwartete Fehler abzufangen
                logging.exception(
                    "Unerwarteter Fehler beim Setzen der Systemzeit (%s): %s",
                    command,
                    exc,
                )
                flash("Unerwarteter Fehler beim Setzen der Systemzeit.")
                return redirect(url_for("set_time"))
            else:
                try:
                    set_rtc(dt)
                except RTCWriteError as exc:
                    logging.error("RTC konnte nicht geschrieben werden: %s", exc)
                    flash("RTC konnte nicht gesetzt werden (I²C-Schreibfehler)")
                    return redirect(url_for("set_time"))
                except (RTCUnavailableError, UnsupportedRTCError) as exc:
                    logging.warning("RTC konnte nicht gesetzt werden: %s", exc)
                    flash(
                        "Warnung: RTC nicht verfügbar oder wird nicht unterstützt. Systemzeit wurde gesetzt, aber nicht auf die RTC geschrieben."
                    )
                flash("Datum und Uhrzeit gesetzt")
                if sync_checkbox or request.form.get("sync_internet_action"):
                    sync_success, messages = perform_internet_time_sync()
                    for message in messages:
                        flash(message)
                    if not sync_success:
                        return redirect(url_for("set_time"))
        return redirect(url_for("index"))
    return render_template(
        "set_time.html",
        sync_internet_default=stored_sync_default,
        rtc_state=get_rtc_configuration_state(),
        rtc_options=RTC_SUPPORTED_TYPES,
    )


@app.route("/rtc_settings", methods=["POST"])
@login_required
def save_rtc_settings():
    selected_module = (request.form.get("rtc_module") or "auto").strip().lower()
    if selected_module not in RTC_SUPPORTED_TYPES:
        flash("Unbekanntes RTC-Modul ausgewählt. Es wird zur automatischen Erkennung gewechselt.")
        selected_module = "auto"

    raw_addresses = (request.form.get("rtc_addresses") or "").strip()
    try:
        parsed_addresses = _parse_rtc_address_string(raw_addresses)
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("set_time"))

    set_setting(RTC_MODULE_SETTING_KEY, selected_module)
    if parsed_addresses:
        set_setting(RTC_ADDRESS_SETTING_KEY, _format_rtc_addresses(parsed_addresses))
    else:
        set_setting(RTC_ADDRESS_SETTING_KEY, "")

    load_rtc_configuration_from_settings()

    if RTC_AVAILABLE and RTC_DETECTED_ADDRESS is not None:
        flash(
            "RTC-Konfiguration gespeichert. Erkannt auf Adresse "
            f"0x{RTC_DETECTED_ADDRESS:02X}."
        )
    elif RTC_AVAILABLE:
        flash("RTC-Konfiguration gespeichert. RTC erkannt.")
    else:
        flash("RTC-Konfiguration gespeichert. Keine RTC gefunden.")
        if bus is None:
            flash("Hinweis: I²C-Bus nicht verfügbar oder Testmodus aktiv.")

    return redirect(url_for("set_time"))


@app.route("/sync_time_from_internet", methods=["POST"])
@login_required
def sync_time_from_internet():
    _, messages = perform_internet_time_sync()
    for message in messages:
        flash(message)
    return redirect(url_for("index"))


@app.route("/update", methods=["POST"])
@login_required
def update():
    try:
        subprocess.check_call(["git", "pull"])
        flash("Update erfolgreich")
    except FileNotFoundError as e:
        logging.error(f"Git nicht gefunden: {e}")
        flash("git nicht verfügbar")
    except subprocess.CalledProcessError as e:
        logging.error(f"Update fehlgeschlagen: {e}")
        flash("Update fehlgeschlagen")
    return redirect(url_for("index"))


BLUETOOTH_MISSING_CLI_FLASH_KEY = "_bluetooth_missing_cli_flashed"
BLUETOOTH_MISSING_CLI_MESSAGE = (
    "bluetoothctl nicht gefunden oder keine Berechtigung. Bitte Installation überprüfen."
)

_MISSING_BLUETOOTH_COMMAND_PATTERNS = _COMMAND_NOT_FOUND_PATTERNS


def _flash_missing_bluetooth_cli_message():
    if not has_request_context():
        return
    if getattr(g, BLUETOOTH_MISSING_CLI_FLASH_KEY, False):
        return
    flash(BLUETOOTH_MISSING_CLI_MESSAGE)
    setattr(g, BLUETOOTH_MISSING_CLI_FLASH_KEY, True)


def _handle_missing_bluetooth_command(
    error: FileNotFoundError, *, flash_user: bool = True, log_error: bool = True
) -> None:
    if log_error:
        logging.error("Bluetooth-Steuerung nicht verfügbar: %s", error)
    if flash_user:
        _flash_missing_bluetooth_cli_message()


BluetoothActionResult = Literal["success", "missing_cli", "error"]


def _missing_command_from_outputs(*outputs: Optional[str]) -> bool:
    return _contains_command_not_found_message(*outputs)


def _create_missing_command_error(
    primary_command: str, *outputs: Optional[str]
) -> FileNotFoundError:
    message = next(
        (output.strip() for output in outputs if isinstance(output, str) and output.strip()),
        "",
    )
    if not message:
        message = f"{primary_command}: command not found"
    return FileNotFoundError(message)


def enable_bluetooth() -> BluetoothActionResult:
    command = privileged_command("bluetoothctl", "power", "on")
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        _handle_missing_bluetooth_command(exc)
        return "missing_cli"
    except subprocess.CalledProcessError as exc:
        if isinstance(exc.cmd, (list, tuple)):
            failing_command: Sequence[str] = list(exc.cmd)
        elif isinstance(exc.cmd, str):
            failing_command = exc.cmd.split()
        else:
            failing_command = []
        if not failing_command:
            failing_command = command
        primary_command = _extract_primary_command(failing_command)
        if _missing_command_from_outputs(exc.stderr, exc.stdout):
            error = _create_missing_command_error(primary_command, exc.stderr, exc.stdout)
            _handle_missing_bluetooth_command(error)
            return "missing_cli"
        raise
    auto_accept_result = bluetooth_auto_accept()
    if auto_accept_result == "error":
        logging.error(
            "Bluetooth konnte nach dem Einschalten nicht vollständig eingerichtet werden"
        )
    return auto_accept_result


def disable_bluetooth() -> BluetoothActionResult:
    command = privileged_command("bluetoothctl", "power", "off")
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        _handle_missing_bluetooth_command(exc)
        return "missing_cli"
    except subprocess.CalledProcessError as exc:
        if isinstance(exc.cmd, (list, tuple)):
            failing_command: Sequence[str] = list(exc.cmd)
        elif isinstance(exc.cmd, str):
            failing_command = exc.cmd.split()
        else:
            failing_command = []
        if not failing_command:
            failing_command = command
        primary_command = _extract_primary_command(failing_command)
        if _missing_command_from_outputs(exc.stderr, exc.stdout):
            error = _create_missing_command_error(primary_command, exc.stderr, exc.stdout)
            _handle_missing_bluetooth_command(error)
            return "missing_cli"
        raise
    return "success"


@app.route("/bluetooth_on", methods=["POST"])
@login_required
def bluetooth_on():
    try:
        result = enable_bluetooth()
        if result == "success":
            flash("Bluetooth aktiviert")
        elif result == "missing_cli":
            _flash_missing_bluetooth_cli_message()
        else:
            flash("Bluetooth konnte nicht aktiviert werden (Auto-Accept fehlgeschlagen)")
    except subprocess.CalledProcessError as e:
        logging.error(f"Bluetooth einschalten fehlgeschlagen: {e}")
        flash("Bluetooth konnte nicht aktiviert werden")
    except FileNotFoundError as exc:
        _handle_missing_bluetooth_command(exc, log_error=False)
    return redirect(url_for("index"))


@app.route("/bluetooth_off", methods=["POST"])
@login_required
def bluetooth_off():
    try:
        result = disable_bluetooth()
        if result == "success":
            flash("Bluetooth deaktiviert")
        elif result == "missing_cli":
            _flash_missing_bluetooth_cli_message()
        else:
            logging.error("Bluetooth konnte nicht deaktiviert werden (unerwartetes Ergebnis)")
            flash("Bluetooth konnte nicht deaktiviert werden")
    except subprocess.CalledProcessError as e:
        logging.error(f"Bluetooth ausschalten fehlgeschlagen: {e}")
        flash("Bluetooth konnte nicht deaktiviert werden")
    except FileNotFoundError as exc:
        _handle_missing_bluetooth_command(exc, log_error=False)
    return redirect(url_for("index"))


def bluetooth_auto_accept() -> BluetoothActionResult:
    try:
        command = privileged_command("bluetoothctl")
        p = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
    except FileNotFoundError as exc:
        _handle_missing_bluetooth_command(exc)
        return "missing_cli"
    except Exception as exc:  # pragma: no cover - Schutz vor unerwarteten Fehlern
        logging.error("Bluetooth auto-accept konnte nicht gestartet werden: %s", exc)
        return "error"

    commands = [
        "power on",
        "discoverable on",
        "pairable on",
        "agent on",
        "default-agent",
    ]

    try:
        stdout, stderr = p.communicate("\n".join(commands) + "\nexit\n")
    except FileNotFoundError as exc:
        _handle_missing_bluetooth_command(exc)
        return "missing_cli"
    except Exception as exc:
        logging.error("Bluetooth auto-accept Kommunikation fehlgeschlagen: %s", exc)
        return "error"

    if p.returncode not in (None, 0):
        stderr_message = (stderr or "").strip()
        stdout_message = (stdout or "").strip()
        if _missing_command_from_outputs(stderr_message, stdout_message):
            primary_command = _extract_primary_command(command)
            error = _create_missing_command_error(
                primary_command,
                stderr_message,
                stdout_message,
            )
            _handle_missing_bluetooth_command(error)
            return "missing_cli"
        logging.error(
            "Bluetooth auto-accept beendete sich mit Code %s: %s",
            p.returncode,
            stderr_message or "Unbekannter Fehler",
        )
        return "error"

    if stderr:
        logging.info("Bluetooth auto-accept meldete Warnungen: %s", stderr.strip())

    logging.info("Bluetooth auto-accept setup: %s", stdout.strip())
    return "success"


# --- GPIO Button Monitor ------------------------------------------------------

button_monitor: Optional[ButtonMonitor] = None


def _get_env_value(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _parse_int_env(name: str) -> Optional[int]:
    value = _get_env_value(name)
    if value is None:
        return None
    try:
        return int(value, 0)
    except ValueError:
        logging.warning("GPIO-Button-Monitor: Ungültiger Integer-Wert für %s: %s", name, value)
        return None


def _parse_float_env(name: str) -> Optional[float]:
    value = _get_env_value(name)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        logging.warning("GPIO-Button-Monitor: Ungültiger Float-Wert für %s: %s", name, value)
        return None


def _normalize_pull(value: str) -> Optional[str]:
    mapping = {
        "up": "up",
        "pull_up": "up",
        "pull-up": "up",
        "high": "up",
        "down": "down",
        "pull_down": "down",
        "pull-down": "down",
        "low": "down",
        "none": "none",
        "off": "none",
        "float": "none",
        "floating": "none",
    }
    return mapping.get(value.lower())


def _normalize_edge(value: str) -> Optional[str]:
    mapping = {
        "rising": "rising",
        "falling": "falling",
        "both": "both",
        "toggle": "both",
        "change": "both",
        "up": "rising",
        "positive": "rising",
        "down": "falling",
        "negative": "falling",
    }
    return mapping.get(value.lower())


def _resolve_default_pull() -> str:
    value = _get_env_value("GPIO_BUTTON_DEFAULT_PULL")
    if value:
        normalized = _normalize_pull(value)
        if normalized:
            return normalized
        logging.warning(
            "GPIO-Button-Monitor: Ungültiger Default-Pull '%s', verwende 'up'",
            value,
        )
    return "up"


def _resolve_default_edge() -> str:
    value = _get_env_value("GPIO_BUTTON_DEFAULT_EDGE")
    if value:
        normalized = _normalize_edge(value)
        if normalized:
            return normalized
        logging.warning(
            "GPIO-Button-Monitor: Ungültiger Default-Flankentyp '%s', verwende 'falling'",
            value,
        )
    return "falling"


def _resolve_default_debounce() -> int:
    value = _parse_int_env("GPIO_BUTTON_DEFAULT_DEBOUNCE_MS")
    if value is None:
        return DEFAULT_BUTTON_DEBOUNCE_MS
    if value < 0:
        logging.warning(
            "GPIO-Button-Monitor: Negative Standard-Entprellzeit (%s ms) – verwende %s ms",
            value,
            DEFAULT_BUTTON_DEBOUNCE_MS,
        )
        return DEFAULT_BUTTON_DEBOUNCE_MS
    return value


def _resolve_pull(action: str, default_pull: str) -> str:
    value = _get_env_value(f"GPIO_BUTTON_{action}_PULL")
    if not value:
        return default_pull
    normalized = _normalize_pull(value)
    if normalized:
        return normalized
    logging.warning(
        "GPIO-Button-Monitor: Ungültiger Pull '%s' für Aktion %s – verwende %s",
        value,
        action,
        default_pull,
    )
    return default_pull


def _resolve_edge(action: str, default_edge: str) -> str:
    value = _get_env_value(f"GPIO_BUTTON_{action}_EDGE")
    if not value:
        return default_edge
    normalized = _normalize_edge(value)
    if normalized:
        return normalized
    logging.warning(
        "GPIO-Button-Monitor: Ungültiger Flankentyp '%s' für Aktion %s – verwende %s",
        value,
        action,
        default_edge,
    )
    return default_edge


def _resolve_debounce(action: str, default_debounce: int) -> int:
    value = _parse_int_env(f"GPIO_BUTTON_{action}_DEBOUNCE_MS")
    if value is None:
        return default_debounce
    if value < 0:
        logging.warning(
            "GPIO-Button-Monitor: Negative Entprellzeit (%s ms) für Aktion %s – verwende %s ms",
            value,
            action,
            default_debounce,
        )
        return default_debounce
    return value


def _stop_playback_from_button() -> None:
    if not _perform_stop_playback(flash_user=False):
        logging.info(
            "GPIO-Button-Monitor: Stop-Taster ausgelöst, aber keine Wiedergabe aktiv"
        )


def _enable_bluetooth_via_button() -> None:
    try:
        result = enable_bluetooth()
    except FileNotFoundError as exc:
        _handle_missing_bluetooth_command(exc, flash_user=False)
        return
    except subprocess.CalledProcessError as exc:
        logging.error("GPIO-Button-Monitor: Bluetooth-Aktivierung fehlgeschlagen: %s", exc)
        return

    if result == "success":
        logging.info("GPIO-Button-Monitor: Bluetooth per Taster aktiviert")
    elif result == "missing_cli":
        logging.warning(
            "GPIO-Button-Monitor: Bluetooth-Taster – benötigte Tools nicht verfügbar"
        )
    else:
        logging.error(
            "GPIO-Button-Monitor: Bluetooth konnte per Taster nicht vollständig eingerichtet werden"
        )


def _disable_bluetooth_via_button() -> None:
    try:
        result = disable_bluetooth()
    except FileNotFoundError as exc:
        _handle_missing_bluetooth_command(exc, flash_user=False)
        return
    except subprocess.CalledProcessError as exc:
        logging.error("GPIO-Button-Monitor: Bluetooth-Deaktivierung fehlgeschlagen: %s", exc)
        return

    if result == "success":
        logging.info("GPIO-Button-Monitor: Bluetooth per Taster deaktiviert")
    elif result == "missing_cli":
        logging.warning(
            "GPIO-Button-Monitor: Bluetooth-Taster – benötigte Tools nicht verfügbar"
        )
    else:
        logging.error(
            "GPIO-Button-Monitor: Bluetooth konnte per Taster nicht deaktiviert werden"
        )


def _build_button_assignments() -> List[ButtonAssignment]:
    if not GPIO_AVAILABLE:
        logging.info(
            "GPIO-Button-Monitor: lgpio nicht verfügbar, Taster-Steuerung deaktiviert"
        )
        return []

    assignments: List[ButtonAssignment] = []
    default_pull = _resolve_default_pull()
    default_edge = _resolve_default_edge()
    default_debounce = _resolve_default_debounce()

    used_pins: Set[int] = set()

    def _add_assignment(
        action: str,
        pin: int,
        callback: Callable[..., None],
        *,
        args: Tuple[Any, ...] = (),
        kwargs: Optional[dict] = None,
        debounce_override: Optional[int] = None,
        source: str = "ENV",
    ) -> None:
        if pin in used_pins:
            logging.warning(
                "GPIO-Button-Monitor: Pin %s ist bereits belegt – Eintrag aus %s wird ignoriert",
                pin,
                source,
            )
            return

        pull = _resolve_pull(action, default_pull)
        edge = _resolve_edge(action, default_edge)
        if debounce_override is None:
            debounce = _resolve_debounce(action, default_debounce)
        else:
            if debounce_override < 0:
                logging.warning(
                    "GPIO-Button-Monitor: Negative Entprellzeit (%s ms) für Quelle %s – verwende %s ms",
                    debounce_override,
                    source,
                    default_debounce,
                )
                debounce = default_debounce
            else:
                debounce = debounce_override
        try:
            assignment = ButtonAssignment(
                name=action.lower(),
                pin=pin,
                pull=pull,
                edge=edge,
                debounce_ms=debounce,
                callback=callback,
                args=args,
                kwargs=dict(kwargs or {}),
            )
        except ValueError as exc:
            logging.error(
                "GPIO-Button-Monitor: Konfiguration für Aktion %s ungültig: %s",
                action,
                exc,
            )
            return

        assignments.append(assignment)
        used_pins.add(pin)
        logging.info(
            "GPIO-Button-Monitor: Aktion '%s' → Pin %s (Pull=%s, Edge=%s, Debounce=%s ms, Quelle=%s)",
            assignment.name,
            pin,
            pull,
            edge,
            debounce,
            source,
        )

    for entry in get_hardware_button_config():
        source_label = f"DB#{entry.id}"

        if not entry.enabled:
            logging.info(
                "GPIO-Button-Monitor: Eintrag %s (GPIO %s) ist deaktiviert",
                source_label,
                entry.gpio_pin,
            )
            continue

        action = entry.action.upper()

        if action == "PLAY":
            if entry.item_id is None or not entry.item_type:
                logging.warning(
                    "GPIO-Button-Monitor: %s ignoriert – PLAY benötigt gültiges Ziel",
                    source_label,
                )
                continue
            item_type = entry.item_type.lower()
            if item_type not in PLAY_NOW_ALLOWED_TYPES:
                logging.warning(
                    "GPIO-Button-Monitor: %s ignoriert – unbekannter Item-Typ '%s'",
                    source_label,
                    entry.item_type,
                )
                continue

            delay = _parse_float_env("GPIO_BUTTON_PLAY_DELAY_SEC")
            if delay is None:
                delay = float(VERZOEGERUNG_SEC)
            elif delay < 0:
                logging.warning(
                    "GPIO-Button-Monitor: Negativer Verzögerungswert (%s) für %s – verwende 0",
                    delay,
                    source_label,
                )
                delay = 0.0

            volume_value = _parse_int_env("GPIO_BUTTON_PLAY_VOLUME_PERCENT")
            if volume_value is None:
                volume_percent = 100
            else:
                volume_percent = max(0, min(100, volume_value))
                if volume_value != volume_percent:
                    logging.warning(
                        "GPIO-Button-Monitor: Lautstärke außerhalb 0-100 (%s) für %s – gekappt auf %s",
                        volume_value,
                        source_label,
                        volume_percent,
                    )

            callback = functools.partial(
                play_item,
                entry.item_id,
                item_type,
                delay,
                False,
                volume_percent=volume_percent,
            )
            _add_assignment(
                "PLAY",
                entry.gpio_pin,
                callback,
                debounce_override=entry.debounce_ms,
                source=source_label,
            )
            continue

        if action == "STOP":
            _add_assignment(
                "STOP",
                entry.gpio_pin,
                _stop_playback_from_button,
                debounce_override=entry.debounce_ms,
                source=source_label,
            )
            continue

        if action == "BT_ON":
            _add_assignment(
                "BT_ON",
                entry.gpio_pin,
                _enable_bluetooth_via_button,
                debounce_override=entry.debounce_ms,
                source=source_label,
            )
            continue

        if action == "BT_OFF":
            _add_assignment(
                "BT_OFF",
                entry.gpio_pin,
                _disable_bluetooth_via_button,
                debounce_override=entry.debounce_ms,
                source=source_label,
            )
            continue

        logging.warning(
            "GPIO-Button-Monitor: %s ignoriert – unbekannte Aktion '%s'",
            source_label,
            entry.action,
        )

    play_pin = _parse_int_env("GPIO_BUTTON_PLAY_PIN")
    if play_pin is not None:
        item_id = _parse_int_env("GPIO_BUTTON_PLAY_ITEM_ID")
        item_type_raw = _get_env_value("GPIO_BUTTON_PLAY_ITEM_TYPE")
        if item_id is None or not item_type_raw:
            logging.warning(
                "GPIO-Button-Monitor: PLAY-Taster konfiguriert, aber Item-ID oder -Typ fehlen"
            )
        else:
            item_type = item_type_raw.lower()
            if item_type not in PLAY_NOW_ALLOWED_TYPES:
                logging.warning(
                    "GPIO-Button-Monitor: PLAY-Taster mit unbekanntem Item-Typ '%s' konfiguriert",
                    item_type_raw,
                )
            else:
                delay = _parse_float_env("GPIO_BUTTON_PLAY_DELAY_SEC")
                if delay is None:
                    delay = float(VERZOEGERUNG_SEC)
                elif delay < 0:
                    logging.warning(
                        "GPIO-Button-Monitor: Negativer Verzögerungswert für PLAY (%s) – verwende 0",
                        delay,
                    )
                    delay = 0.0

                volume_value = _parse_int_env("GPIO_BUTTON_PLAY_VOLUME_PERCENT")
                if volume_value is None:
                    volume_percent = 100
                else:
                    volume_percent = max(0, min(100, volume_value))
                    if volume_value != volume_percent:
                        logging.warning(
                            "GPIO-Button-Monitor: Lautstärke für PLAY außerhalb 0-100 (war %s) – gekappt auf %s",
                            volume_value,
                            volume_percent,
                        )

                callback = functools.partial(
                    play_item,
                    item_id,
                    item_type,
                    delay,
                    False,
                    volume_percent=volume_percent,
                )
                _add_assignment("PLAY", play_pin, callback)

    stop_pin = _parse_int_env("GPIO_BUTTON_STOP_PIN")
    if stop_pin is not None:
        _add_assignment("STOP", stop_pin, _stop_playback_from_button)

    bt_on_pin = _parse_int_env("GPIO_BUTTON_BT_ON_PIN")
    if bt_on_pin is not None:
        _add_assignment("BT_ON", bt_on_pin, _enable_bluetooth_via_button)

    bt_off_pin = _parse_int_env("GPIO_BUTTON_BT_OFF_PIN")
    if bt_off_pin is not None:
        _add_assignment("BT_OFF", bt_off_pin, _disable_bluetooth_via_button)

    return assignments


def _start_button_monitor() -> None:
    global button_monitor

    if TESTING:
        logging.debug("GPIO-Button-Monitor: Testmodus aktiv – Monitor wird nicht gestartet")
        return

    assignments = _build_button_assignments()
    if not assignments:
        logging.info("GPIO-Button-Monitor: Keine Taster-Konfiguration gefunden")
        return

    poll_interval = _parse_float_env("GPIO_BUTTON_POLL_INTERVAL_SEC")
    if poll_interval is None:
        poll_interval = 0.01
    elif poll_interval <= 0:
        logging.warning(
            "GPIO-Button-Monitor: Poll-Intervall %s ist ungültig – verwende 0.01 s",
            poll_interval,
        )
        poll_interval = 0.01

    monitor = ButtonMonitor(
        assignments,
        chip_id=gpio_chip_id,
        poll_interval=poll_interval,
        name="gpio-button-monitor",
    )
    if monitor.start():
        button_monitor = monitor
    else:
        logging.error("GPIO-Button-Monitor: Start fehlgeschlagen")


def _stop_button_monitor() -> None:
    global button_monitor
    monitor = button_monitor
    if monitor is None:
        return
    try:
        monitor.stop(timeout=2.0)
    finally:
        button_monitor = None


def _refresh_button_monitor_configuration() -> None:
    reload_hardware_button_config()
    if TESTING:
        return
    _stop_button_monitor()
    _start_button_monitor()


atexit.register(_stop_button_monitor)
atexit.register(stop_background_services)


if not TESTING and not SUPPRESS_AUTOSTART:
    try:
        start_background_services()
    except Exception:
        logging.getLogger(__name__).exception(
            "Autostart der Hintergrunddienste fehlgeschlagen."
        )

if __name__ == "__main__":
    dev_flag = os.environ.get("AUDIO_PI_USE_DEV_SERVER", "").strip().lower()
    dev_enabled = dev_flag in {"1", "true", "yes"}

    if not dev_enabled:
        message = (
            "Direkter Start über 'python app.py' ist deaktiviert. Bitte verwende den "
            "Gunicorn-Dienst (siehe README) oder setze AUDIO_PI_USE_DEV_SERVER=1 für "
            "den lokalen Entwicklungsserver."
        )
        logging.error(message)
        if getattr(scheduler, "running", False):
            stop_background_services()
        _stop_button_monitor()
        if not TESTING and GPIO_AVAILABLE and gpio_handle is not None:
            try:
                deactivate_amplifier()
                GPIO.gpiochip_close(gpio_handle)
            except GPIOError as e:
                logging.error(f"Fehler beim Schließen des GPIO-Handles: {e}")
        raise SystemExit(message)

    debug = os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true", "yes"}
    port_raw = os.environ.get("FLASK_PORT", "80")
    try:
        port = int(port_raw)
    except ValueError:
        logging.warning(
            "Ungültiger Wert für FLASK_PORT '%s'. Fallback auf Port 80.", port_raw
        )
        port = 80
    try:
        start_background_services()
        app.run(host="0.0.0.0", port=port, debug=debug)
    finally:
        # Scheduler nur stoppen, wenn er wirklich gestartet wurde (z.B. nicht im TESTING-Modus)
        if getattr(scheduler, "running", False):
            stop_background_services()
        _stop_button_monitor()
        if not TESTING and GPIO_AVAILABLE and gpio_handle is not None:
            try:
                deactivate_amplifier()
                GPIO.gpiochip_close(gpio_handle)
                logging.info("GPIO-Handle geschlossen")
            except GPIOError as e:
                logging.error(f"Fehler beim Schließen des GPIO-Handles: {e}")
