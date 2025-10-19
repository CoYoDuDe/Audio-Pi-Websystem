import os
import time
import subprocess
import threading
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
import sqlite3
import tempfile
import calendar
import fnmatch
import math
import logging
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
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

try:  # pragma: no cover - Import wird separat getestet
    import lgpio as GPIO
except ImportError:  # pragma: no cover - Verhalten wird in Tests geprüft
    GPIO = None  # type: ignore[assignment]
    GPIO_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "lgpio konnte nicht importiert werden, GPIO-Funktionen deaktiviert."
    )
else:
    GPIO_AVAILABLE = True

import pygame
from pydub import AudioSegment
from pydub.exceptions import CouldntDecodeError
try:  # pragma: no cover - Import wird separat getestet
    import smbus
except ImportError:  # pragma: no cover - Verhalten wird in Tests geprüft
    smbus = None  # type: ignore[assignment]
    SMBUS_AVAILABLE = False
    logging.getLogger(__name__).warning(
        "smbus konnte nicht importiert werden, I²C-Funktionen deaktiviert."
    )
else:
    SMBUS_AVAILABLE = True
import sys
import secrets
import re
from typing import Iterable, List, Optional, Tuple, Literal

if GPIO_AVAILABLE:
    GPIOError = GPIO.error
else:
    class GPIOError(Exception):
        """Platzhalter, wenn lgpio nicht verfügbar ist."""


app = Flask(__name__)
secret_key = os.environ.get("FLASK_SECRET_KEY")
if not secret_key:
    logging.error("FLASK_SECRET_KEY nicht gesetzt. Bitte Umgebungsvariable setzen.")
    sys.exit(1)
app.secret_key = secret_key
csrf = CSRFProtect()
csrf.init_app(app)
TESTING_RAW = os.getenv("TESTING")


def _env_to_bool(value):
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


TESTING = _env_to_bool(TESTING_RAW)
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

    allowed_endpoints = {"change_password", "logout"}
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
DB_FILE = os.getenv("DB_FILE", "audio.db")
GPIO_PIN_ENDSTUFE = 17
VERZOEGERUNG_SEC = 5
DEFAULT_MAX_SCHEDULE_DELAY_SECONDS = 60
DAC_SINK_SETTING_KEY = "dac_sink_name"
DAC_SINK_LABEL_SETTING_KEY = "dac_sink_label"
DEFAULT_DAC_SINK = os.getenv(
    "DAC_SINK_NAME",
    "alsa_output.platform-soc_107c000000_sound.stereo-fallback",
)
DEFAULT_DAC_SINK_LABEL = "Konfigurierter DAC"
DAC_SINK = DEFAULT_DAC_SINK
CONFIGURED_DAC_SINK: Optional[str] = None
DAC_SINK_LABEL: Optional[str] = None
NORMALIZATION_HEADROOM_SETTING_KEY = "normalization_headroom_db"
NORMALIZATION_HEADROOM_ENV_KEY = "NORMALIZATION_HEADROOM_DB"
DEFAULT_NORMALIZATION_HEADROOM_DB = 0.1
raw_max_schedule_delay = os.getenv(
    "MAX_SCHEDULE_DELAY_SECONDS", str(DEFAULT_MAX_SCHEDULE_DELAY_SECONDS)
)
try:
    MAX_SCHEDULE_DELAY_SECONDS = int(raw_max_schedule_delay)
except (TypeError, ValueError):
    MAX_SCHEDULE_DELAY_SECONDS = DEFAULT_MAX_SCHEDULE_DELAY_SECONDS
    logging.warning(
        "Ungültiger MAX_SCHEDULE_DELAY_SECONDS-Wert '%s'. Fallback auf %s Sekunden.",
        raw_max_schedule_delay,
        DEFAULT_MAX_SCHEDULE_DELAY_SECONDS,
    )
else:
    if MAX_SCHEDULE_DELAY_SECONDS < 0:
        logging.warning(
            "MAX_SCHEDULE_DELAY_SECONDS-Wert '%s' ist negativ. Fallback auf %s Sekunden.",
            raw_max_schedule_delay,
            DEFAULT_MAX_SCHEDULE_DELAY_SECONDS,
        )
        MAX_SCHEDULE_DELAY_SECONDS = DEFAULT_MAX_SCHEDULE_DELAY_SECONDS
DEFAULT_DAC_SINK = "alsa_output.platform-soc_107c000000_sound.stereo-fallback"
DEFAULT_DAC_SINK_HINT = DEFAULT_DAC_SINK
DAC_SINK_SETTING_KEY = "dac_sink_name"
DAC_SINK_HINT = os.environ.get("DAC_SINK_NAME", DEFAULT_DAC_SINK_HINT)
DAC_SINK = DAC_SINK_HINT

SCHEDULE_VOLUME_PERCENT_SETTING_KEY = "schedule_default_volume_percent"
SCHEDULE_VOLUME_DB_SETTING_KEY = "schedule_default_volume_db"
SCHEDULE_DEFAULT_VOLUME_PERCENT_FALLBACK = 100
SCHEDULE_VOLUME_PERCENT_MIN = 0
SCHEDULE_VOLUME_PERCENT_MAX = 100

PLAY_NOW_ALLOWED_TYPES = {"file", "playlist"}

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
if not TESTING and GPIO_AVAILABLE:
    try:
        gpio_handle = GPIO.gpiochip_open(4)  # Pi 5 = Chip 4
    except (GPIOError, OSError) as exc:
        gpio_handle = None
        logging.warning(
            "GPIO-Chip konnte nicht geöffnet werden, starte ohne Verstärkersteuerung: %s",
            exc,
        )
    else:
        logging.info(
            "GPIO initialisiert für Verstärker (OUTPUT/HIGH = an, LOW = aus)"
        )
else:
    gpio_handle = None
    if not TESTING and not GPIO_AVAILABLE:
        logging.warning(
            "lgpio nicht verfügbar, starte ohne Verstärkersteuerung."
        )
amplifier_claimed = False

# Track pause status manually since pygame lacks a get_paused() helper
is_paused = False


# Globale Statusinformationen für Audiofunktionen
audio_status = {"dac_sink_detected": None}

pygame_available = TESTING


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
if not TESTING:
    try:
        pygame.mixer.init()
    except pygame.error as exc:
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

    date_command = ["sudo", "date", "-s", rtc_time.strftime("%Y-%m-%d %H:%M:%S")]

    try:
        subprocess.check_call(date_command)
    except FileNotFoundError as exc:
        logging.error("RTC-Sync fehlgeschlagen: 'date'-Kommando nicht gefunden (%s)", exc)
        _update_rtc_sync_status(False, str(exc))
        return False
    except subprocess.CalledProcessError as exc:
        logging.error(
            "RTC-Sync fehlgeschlagen: Kommando %s lieferte Rückgabecode %s",
            " ".join(map(str, date_command)),
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


AUTO_REBOOT_DEFAULTS = {
    "auto_reboot_enabled": "0",
    "auto_reboot_mode": "daily",
    "auto_reboot_time": "03:00",
    "auto_reboot_weekday": "monday",
}


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
            """CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"""
        )
        for key, value in AUTO_REBOOT_DEFAULTS.items():
            cursor.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
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
                logging.warning(
                    "Initialpasswort für 'admin' generiert. Bitte sicher verwahren: %s",
                    initial_password,
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


def _parse_headroom_value(raw_value: Optional[str], source: str) -> Optional[float]:
    if raw_value is None:
        return None
    normalized = str(raw_value).strip()
    if not normalized:
        return None
    try:
        return float(normalized)
    except (TypeError, ValueError):
        logging.warning(
            "Ungültiger Headroom-Wert '%s' aus %s. Wert wird ignoriert.",
            raw_value,
            source,
        )
        return None


def get_normalization_headroom_details() -> dict:
    stored_raw = get_setting(NORMALIZATION_HEADROOM_SETTING_KEY, None)
    env_raw = os.environ.get(NORMALIZATION_HEADROOM_ENV_KEY)

    stored_value = _parse_headroom_value(
        stored_raw, f"Einstellung '{NORMALIZATION_HEADROOM_SETTING_KEY}'"
    )
    env_value = _parse_headroom_value(
        env_raw, f"Umgebungsvariable {NORMALIZATION_HEADROOM_ENV_KEY}"
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


def load_dac_sink_from_settings():
    global DAC_SINK, CONFIGURED_DAC_SINK, DAC_SINK_LABEL

    stored_value = get_setting(DAC_SINK_SETTING_KEY, None)
    normalized_value = stored_value.strip() if stored_value else ""
    previous_sink = DAC_SINK

    if normalized_value:
        DAC_SINK = normalized_value
        CONFIGURED_DAC_SINK = normalized_value
    else:
        DAC_SINK = DEFAULT_DAC_SINK
        CONFIGURED_DAC_SINK = None

    if DAC_SINK != previous_sink:
        logging.info("DAC-Sink aktualisiert: %s", DAC_SINK)

    DAC_SINK_LABEL = _load_configured_dac_label()
    audio_status["dac_sink_detected"] = None


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
    return subprocess.getoutput("pactl get-default-sink")


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


def _is_sink_available(sink_name):
    sinks = _list_pulse_sinks()
    resolved = _resolve_sink_name(sink_name, sinks=sinks)
    return resolved is not None


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
    global DAC_SINK, DAC_SINK_HINT, DAC_SINK_LABEL

    env_value = os.environ.get("DAC_SINK_NAME")
    if env_value:
        DAC_SINK_HINT = env_value.strip()
        logging.info("DAC_SINK_NAME aus Umgebungsvariable übernommen: %s", DAC_SINK_HINT)
    else:
        stored_value = get_setting(DAC_SINK_SETTING_KEY, None)
        if stored_value and stored_value.strip():
            DAC_SINK_HINT = stored_value.strip()
            logging.info("DAC-Sink aus Einstellungen geladen: %s", DAC_SINK_HINT)
        elif stored_value is None:
            logging.info(
                "Kein gespeicherter DAC-Sink gefunden. Verwende Standard: %s",
                DEFAULT_DAC_SINK_HINT,
            )
            DAC_SINK_HINT = DEFAULT_DAC_SINK_HINT
        else:
            DAC_SINK_HINT = DEFAULT_DAC_SINK_HINT

    resolved = _resolve_sink_name(DAC_SINK_HINT)
    if resolved:
        DAC_SINK = resolved
    else:
        DAC_SINK = DAC_SINK_HINT

    DAC_SINK_LABEL = _load_configured_dac_label()


load_dac_sink_configuration()


def _sink_is_configured(sink_name: str) -> bool:
    if not sink_name:
        return False
    if sink_name == DAC_SINK:
        return True
    return _sink_matches_hint(sink_name, DAC_SINK_HINT)


def gather_status():
    wlan_ssid = subprocess.getoutput("iwgetid wlan0 -r").strip() or "Nicht verbunden"
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    current_volume = (
        subprocess.getoutput(
            'pactl get-sink-volume @DEFAULT_SINK@ | grep -oP "\\d+%" | head -1'
        )
        or "Unbekannt"
    )
    if audio_status.get("dac_sink_detected") is None and DAC_SINK:
        audio_status["dac_sink_detected"] = _is_sink_available(DAC_SINK)

    effective_label = DAC_SINK_LABEL or DEFAULT_DAC_SINK_LABEL
    target_dac_sink = DAC_SINK or DAC_SINK_HINT
    is_playing = pygame.mixer.music.get_busy() if pygame_available else False
    headroom_details = get_normalization_headroom_details()
    schedule_default_volume = get_schedule_default_volume_details()

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
        result = subprocess.call(["sudo", "reboot"])
        if result != 0:
            logging.error(
                "Automatischer Neustart fehlgeschlagen – Rückgabewert %s", result
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


if not TESTING:
    skip_past_once_schedules()
    load_schedules()
    update_auto_reboot_job()
    scheduler.start()


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
        flash(_PACTL_MISSING_MESSAGE)


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
def is_bt_audio_active():
    # Prüft, ob ein Bluetooth-Audio-Stream anliegt (A2DP)
    sinks_output = _run_pactl_command("list", "short", "sinks")
    if sinks_output is None:
        return False

    sinks = [line for line in sinks_output.splitlines() if "bluez" in line]
    if not sinks:
        return False

    sink_inputs_output = _run_pactl_command("list", "short", "sink-inputs")
    if sink_inputs_output is None:
        return False

    for sink in sinks:
        sink_name = sink.split()[1]
        for sink_input in sink_inputs_output.splitlines():
            if sink_name in sink_input:
                return True
    return False


def bt_audio_monitor():
    was_active = False
    while True:
        active = is_bt_audio_active()
        if active and not was_active:
            activate_amplifier()
            was_active = True
            logging.info("Bluetooth Audio erkannt, Verstärker EIN")
        elif not active and was_active:
            deactivate_amplifier()
            was_active = False
            logging.info("Bluetooth Audio gestoppt, Verstärker AUS")
        time.sleep(3)


# AP-Modus
def has_network():
    return "default" in subprocess.getoutput("ip route")


def setup_ap():
    try:
        if not has_network():
            logging.info("Kein Netzwerk – starte AP-Modus")
            subprocess.call(["sudo", "systemctl", "start", "dnsmasq"])
            subprocess.call(["sudo", "systemctl", "start", "hostapd"])
        else:
            disable_ap()
    except (FileNotFoundError, OSError) as exc:
        logging.error("sudo oder systemctl nicht gefunden: %s", exc)
        if has_request_context():
            flash("sudo oder systemctl nicht gefunden")
        return False


def disable_ap():
    try:
        subprocess.call(["sudo", "systemctl", "stop", "hostapd"])
        subprocess.call(["sudo", "systemctl", "stop", "dnsmasq"])
    except (FileNotFoundError, OSError) as exc:
        logging.error("sudo oder systemctl nicht gefunden: %s", exc)
        if has_request_context():
            flash("sudo oder systemctl nicht gefunden")
        return False
    logging.info("AP-Modus deaktiviert")


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


@app.route("/logout")
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
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        if os.path.exists(file_path):
            base, ext = os.path.splitext(filename)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{base}_{timestamp}{ext}"
            flash(f"Dateiname bereits vorhanden, gespeichert als {filename}")
            file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        else:
            flash("Datei hochgeladen")
        file.save(file_path)
        try:
            sound = AudioSegment.from_file(file_path)
            duration_seconds = len(sound) / 1000.0
        except Exception as exc:
            logging.error("Fehler beim Auslesen der Audiodauer von %s: %s", filename, exc)
            try:
                os.remove(file_path)
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
    name = request.form["name"]
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


@app.route("/stop_playback", methods=["POST"])
@login_required
def stop_playback():
    if not pygame_available:
        _notify_audio_unavailable("Wiedergabe kann nicht gestoppt werden")
        return redirect(url_for("index"))
    pygame.mixer.music.stop()
    global is_paused
    is_paused = False
    if not is_bt_connected():
        deactivate_amplifier()
    logging.info("Wiedergabe gestoppt")
    if is_bt_connected():
        resume_bt_audio()
        load_loopback()
    flash("Wiedergabe gestoppt")
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
    try:
        subprocess.Popen(command)
    except Exception as exc:  # pragma: no cover - Fehlerfall hardwareabhängig
        logging.exception("Systemkommando %s fehlgeschlagen", command)
        flash(f"{error_message}: {exc}")
    else:
        flash(success_message)
    return redirect(url_for("index"))


@app.route("/system/reboot", methods=["POST"])
@login_required
def system_reboot():
    return _execute_system_command(
        ["sudo", "reboot"],
        "Systemneustart eingeleitet.",
        "Neustart konnte nicht gestartet werden",
    )


@app.route("/system/shutdown", methods=["POST"])
@login_required
def system_shutdown():
    return _execute_system_command(
        ["sudo", "poweroff"],
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
        flash("Ungültiger Zielpegel/Headroom-Wert. Bitte eine Zahl eingeben.")
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


@app.route("/wlan_scan")
@login_required
def wlan_scan():
    result = subprocess.getoutput("sudo iwlist wlan0 scan")
    return render_template("scan.html", networks=result)


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


def _run_wpa_cli(args, expect_ok=True):
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
    )

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    combined = "\n".join(filter(None, [stdout, stderr]))

    if result.returncode != 0 or "FAIL" in stdout or "FAIL" in stderr:
        logging.error(
            "wpa_cli-Aufruf fehlgeschlagen (%s): %s",
            " ".join(args),
            combined,
        )
        raise subprocess.CalledProcessError(
            result.returncode or 1,
            args,
            output=stdout,
            stderr=stderr,
        )

    if expect_ok and "OK" not in stdout:
        logging.error(
            "wpa_cli-Antwort ohne OK (%s): %s",
            " ".join(args),
            combined,
        )
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
    password = request.form.get("password", "")
    formatted_ssid = _format_ssid_for_wpa_cli(ssid)
    is_blank_password = password.strip() == ""
    is_open_network = password == "" or (
        is_blank_password and len(password) < 8
    )
    try:
        base_cmd = ["sudo", "wpa_cli", "-i", "wlan0"]
        net_id = _run_wpa_cli(base_cmd + ["add_network"], expect_ok=False).strip()
        _run_wpa_cli(base_cmd + ["set_network", net_id, "ssid", formatted_ssid])
        if is_open_network:
            _run_wpa_cli(base_cmd + ["set_network", net_id, "key_mgmt", "NONE"])
            _run_wpa_cli(base_cmd + ["set_network", net_id, "auth_alg", "OPEN"])
        else:
            if _is_hex_psk(password):
                psk_value = password
            else:
                psk_value = _quote_wpa_cli(password)
            _run_wpa_cli(base_cmd + ["set_network", net_id, "psk", psk_value])
        _run_wpa_cli(base_cmd + ["enable_network", net_id])
        _run_wpa_cli(base_cmd + ["save_config"])
        _run_wpa_cli(base_cmd + ["reconfigure"])
        flash("Versuche, mit WLAN zu verbinden")
    except FileNotFoundError as e:
        logging.error("wpa_cli oder sudo nicht gefunden: %s", e)
        flash("wpa_cli oder sudo nicht gefunden. Bitte Installation überprüfen.")
    except subprocess.CalledProcessError as e:
        logging.error(
            "Fehler beim WLAN-Verbindungsaufbau: %s (stdout: %s, stderr: %s)",
            e,
            getattr(e, "output", ""),
            getattr(e, "stderr", ""),
        )
        flash("Fehler beim WLAN-Verbindungsaufbau. Details im Log einsehbar.")
    return redirect(url_for("index"))


@app.route("/volume", methods=["POST"])
@login_required
def set_volume():
    if not pygame_available:
        _notify_audio_unavailable("Lautstärke kann nicht gesetzt werden")
        return redirect(url_for("index"))

    vol = request.form.get("volume")
    try:
        int_vol = int(vol)
    except (TypeError, ValueError):
        flash("Ungültiger Lautstärke-Wert")
        return redirect(url_for("index"))

    if not 0 <= int_vol <= 100:
        flash("Ungültiger Lautstärke-Wert")
        return redirect(url_for("index"))

    try:
        pygame.mixer.music.set_volume(int_vol / 100.0)
        current_sink = get_current_sink()
        commands = [
            ["pactl", "set-sink-volume", current_sink, f"{int_vol}%"],
            ["amixer", "sset", "Master", f"{int_vol}%"],
            ["sudo", "alsactl", "store"],
        ]
        any_success = False
        for command in commands:
            try:
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except FileNotFoundError:
                cmd_name = command[0] if command else "Befehl"
                if cmd_name == "pactl":
                    _notify_missing_pactl()
                else:
                    message = f"Kommando '{cmd_name}' wurde nicht gefunden."
                    logging.warning(message)
                    flash(message)
            except subprocess.CalledProcessError as exc:
                cmd_name = command[0] if command else str(exc.cmd)
                message = (
                    f"Kommando '{cmd_name}' fehlgeschlagen (Code {exc.returncode})."
                )
                logging.warning(
                    "%s stdout: %s stderr: %s",
                    message,
                    exc.stdout or "",
                    exc.stderr or "",
                )
                flash(message)
            else:
                any_success = True
    except Exception as e:
        logging.error(f"Fehler beim Setzen der Lautstärke: {e}")
        flash("Fehler beim Setzen der Lautstärke")
    else:
        if any_success:
            logging.info(f"Lautstärke auf {int_vol}% gesetzt (persistent)")
            flash("Lautstärke persistent gesetzt")
        else:
            logging.error("Lautstärke konnte mit den verfügbaren Werkzeugen nicht gesetzt werden.")
            flash("Lautstärke konnte nicht gesetzt werden")
    return redirect(url_for("index"))


@app.route("/logs")
@login_required
def logs():
    try:
        with open("app.log", "r") as f:
            logs = f.read()
    except FileNotFoundError:
        logs = "Keine Logdatei vorhanden"
    return render_template("logs.html", logs=logs)


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
    messages = []
    try:
        subprocess.check_call(["sudo", "systemctl", "stop", "systemd-timesyncd"])
        subprocess.check_call(["sudo", "ntpdate", "pool.ntp.org"])
    except subprocess.CalledProcessError as exc:
        logging.error("Zeit-Synchronisation fehlgeschlagen (%s): %s", exc.cmd, exc)
        messages.append("Fehler bei der Synchronisation")
    except Exception as exc:  # pragma: no cover - unerwartete Fehler
        logging.error("Unerwarteter Fehler bei der Zeit-Synchronisation: %s", exc)
        messages.append("Fehler bei der Synchronisation")
    else:
        try:
            set_rtc(datetime.now())
        except RTCWriteError as exc:
            logging.error(
                "RTC konnte nach dem Internet-Sync nicht geschrieben werden: %s",
                exc,
            )
            messages.append("RTC konnte nicht aktualisiert werden (I²C-Schreibfehler)")
        except (RTCUnavailableError, UnsupportedRTCError) as exc:
            logging.error(
                "RTC konnte nach dem Internet-Sync nicht gesetzt werden: %s", exc
            )
            messages.append("RTC konnte nicht aktualisiert werden")
        else:
            messages.append("Zeit vom Internet synchronisiert")
            success = True
    finally:
        try:
            subprocess.check_call(["sudo", "systemctl", "start", "systemd-timesyncd"])
        except subprocess.CalledProcessError as exc:
            logging.warning(
                "systemd-timesyncd konnte nach dem Internet-Sync nicht gestartet werden (Exit-Code): %s",
                exc,
            )
            messages.append(
                "systemd-timesyncd konnte nicht gestartet werden (siehe Logs für Details)"
            )
            success = False
        except FileNotFoundError as exc:
            logging.warning(
                "systemd-timesyncd konnte nicht gestartet werden, da sudo/systemctl fehlen: %s",
                exc,
            )
            messages.append(
                "systemd-timesyncd konnte nicht gestartet werden, da sudo oder systemctl nicht verfügbar sind"
            )
            success = False
        except Exception as exc:  # pragma: no cover - unerwartete Fehler
            logging.warning(
                "Unerwarteter Fehler beim Starten von systemd-timesyncd nach dem Internet-Sync: %s",
                exc,
            )
            messages.append(
                "systemd-timesyncd konnte nicht gestartet werden (unerwarteter Fehler, bitte Logs prüfen)"
            )
            success = False
    return success, messages


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
            return redirect(url_for("index"))
        try:
            dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        except ValueError:
            flash("Ungültiges Datums-/Zeitformat")
        else:
            command = ["sudo", "date", "-s", dt.strftime("%Y-%m-%d %H:%M:%S")]
            try:
                subprocess.run(command, check=True)
            except FileNotFoundError as exc:
                missing_command = exc.filename or command[0]
                logging.error(
                    "Kommando zum Setzen der Systemzeit nicht gefunden (%s): %s",
                    missing_command,
                    exc,
                )
                flash(
                    f"Kommando '{missing_command}' wurde nicht gefunden. Systemzeit konnte nicht gesetzt werden."
                )
            except subprocess.CalledProcessError as exc:
                logging.error("Systemzeit setzen fehlgeschlagen (%s): %s", exc.cmd, exc)
                executed_command = (
                    " ".join(map(str, exc.cmd))
                    if isinstance(exc.cmd, (list, tuple))
                    else str(exc.cmd)
                )
                flash(
                    f"Ausführung von '{executed_command}' ist fehlgeschlagen. Systemzeit konnte nicht gesetzt werden."
                )
            except Exception as exc:  # Fallback, um unerwartete Fehler abzufangen
                logging.exception(
                    "Unerwarteter Fehler beim Setzen der Systemzeit (%s): %s",
                    command,
                    exc,
                )
                flash("Unerwarteter Fehler beim Setzen der Systemzeit.")
            else:
                try:
                    set_rtc(dt)
                except RTCWriteError as exc:
                    logging.error("RTC konnte nicht geschrieben werden: %s", exc)
                    flash("RTC konnte nicht gesetzt werden (I²C-Schreibfehler)")
                except (RTCUnavailableError, UnsupportedRTCError) as exc:
                    logging.error("RTC konnte nicht gesetzt werden: %s", exc)
                    flash("RTC nicht verfügbar oder wird nicht unterstützt")
                else:
                    flash("Datum und Uhrzeit gesetzt")
                    if sync_checkbox or request.form.get("sync_internet_action"):
                        _, messages = perform_internet_time_sync()
                        for message in messages:
                            flash(message)
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
    "sudo oder bluetoothctl nicht gefunden. Bitte Installation überprüfen."
)


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


def enable_bluetooth() -> BluetoothActionResult:
    try:
        subprocess.check_call(["sudo", "bluetoothctl", "power", "on"])
    except FileNotFoundError as exc:
        _handle_missing_bluetooth_command(exc)
        return "missing_cli"
    auto_accept_result = bluetooth_auto_accept()
    if auto_accept_result == "error":
        logging.error(
            "Bluetooth konnte nach dem Einschalten nicht vollständig eingerichtet werden"
        )
    return auto_accept_result


def disable_bluetooth() -> BluetoothActionResult:
    try:
        subprocess.check_call(["sudo", "bluetoothctl", "power", "off"])
    except FileNotFoundError as exc:
        _handle_missing_bluetooth_command(exc)
        return "missing_cli"
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
        p = subprocess.Popen(
            ["sudo", "bluetoothctl"],
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


if not TESTING:
    threading.Thread(target=bluetooth_auto_accept, daemon=True).start()
    threading.Thread(target=bt_audio_monitor, daemon=True).start()

if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    port_raw = os.environ.get("FLASK_PORT", "80")
    try:
        port = int(port_raw)
    except ValueError:
        logging.warning(
            "Ungültiger Wert für FLASK_PORT '%s'. Fallback auf Port 80.", port_raw
        )
        port = 80
    try:
        app.run(host="0.0.0.0", port=port, debug=debug)
    finally:
        # Scheduler nur stoppen, wenn er wirklich gestartet wurde (z.B. nicht im TESTING-Modus)
        if getattr(scheduler, "running", False):
            scheduler.shutdown()
        if not TESTING and GPIO_AVAILABLE and gpio_handle is not None:
            try:
                deactivate_amplifier()
                GPIO.gpiochip_close(gpio_handle)
                logging.info("GPIO-Handle geschlossen")
            except GPIOError as e:
                logging.error(f"Fehler beim Schließen des GPIO-Handles: {e}")
