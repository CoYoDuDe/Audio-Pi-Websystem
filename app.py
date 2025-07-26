import os
import time
import subprocess
import threading
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
import sqlite3
import tempfile
from datetime import datetime, timedelta
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    has_request_context,
)
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    login_required,
    logout_user,
    current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import lgpio as GPIO
import pygame
from pydub import AudioSegment
import smbus
import sys
import logging
import re

app = Flask(__name__)
secret_key = os.environ.get("FLASK_SECRET_KEY")
if not secret_key:
    logging.error("FLASK_SECRET_KEY nicht gesetzt. Bitte Umgebungsvariable setzen.")
    sys.exit(1)
app.secret_key = secret_key
TESTING = os.getenv("TESTING")
login_manager = LoginManager(app)
login_manager.login_view = "login"

# Konfiguration
UPLOAD_FOLDER = "uploads"
ALLOWED_EXTENSIONS = {"wav", "mp3"}
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
DB_FILE = os.getenv("DB_FILE", "audio.db")
GPIO_PIN_ENDSTUFE = 17
VERZOEGERUNG_SEC = 5
DAC_SINK = "alsa_output.platform-soc_107c000000_sound.stereo-fallback"

# Logging
logging.basicConfig(
    filename="app.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
if not TESTING:
    gpio_handle = GPIO.gpiochip_open(4)  # Pi 5 = Chip 4
    logging.info("GPIO initialisiert für Verstärker (OUTPUT/HIGH = an, LOW = aus)")
else:
    gpio_handle = None
amplifier_claimed = False

# Track pause status manually since pygame lacks a get_paused() helper
is_paused = False

# Pygame Audio und Lautstärke nur initialisieren, wenn nicht im Test
if not TESTING:
    pygame.mixer.init()


    def load_initial_volume():
        output = subprocess.getoutput("pactl get-sink-volume @DEFAULT_SINK@")
        match = re.search(r"(\d+)%", output)
        if match:
            initial_vol = int(match.group(1))
            pygame.mixer.music.set_volume(initial_vol / 100.0)
            logging.info(f"Initiale Lautstärke geladen: {initial_vol}%")


    load_initial_volume()

# RTC (Echtzeituhr) Setup
class RTCUnavailableError(Exception):
    """RTC I²C-Bus konnte nicht initialisiert werden."""


try:
    bus = smbus.SMBus(1) if not TESTING else None
except (FileNotFoundError, OSError) as e:
    logging.warning(f"RTC SMBus nicht verfügbar: {e}")
    bus = None

RTC_ADDRESS = 0x51


def bcd_to_dec(val):
    return ((val >> 4) * 10) + (val & 0x0F)


def dec_to_bcd(val):
    return ((val // 10) << 4) | (val % 10)


def read_rtc():
    if bus is None:
        raise RTCUnavailableError("RTC-Bus nicht initialisiert")
    data = bus.read_i2c_block_data(RTC_ADDRESS, 0x04, 7)
    second = bcd_to_dec(data[0] & 0x7F)
    minute = bcd_to_dec(data[1] & 0x7F)
    hour = bcd_to_dec(data[2] & 0x3F)
    date = bcd_to_dec(data[3] & 0x3F)
    month = bcd_to_dec(data[5] & 0x1F)
    year = bcd_to_dec(data[6])
    if month < 1 or month > 12:
        raise ValueError("Ungültiger Monat von RTC – RTC evtl. initialisieren!")
    return datetime(2000 + year, month, date, hour, minute, second)


def set_rtc(dt):
    if bus is None:
        raise RTCUnavailableError("RTC-Bus nicht initialisiert")
    second = dec_to_bcd(dt.second)
    minute = dec_to_bcd(dt.minute)
    hour = dec_to_bcd(dt.hour)
    date = dec_to_bcd(dt.day)
    weekday = dec_to_bcd(dt.weekday())
    month = dec_to_bcd(dt.month)
    year = dec_to_bcd(dt.year - 2000)
    bus.write_i2c_block_data(
        RTC_ADDRESS, 0x04, [second, minute, hour, date, weekday, month, year]
    )
    logging.info(f'RTC gesetzt auf {dt.strftime("%Y-%m-%d %H:%M:%S")}')


def sync_rtc_to_system():
    try:
        rtc_time = read_rtc()
        subprocess.call(["sudo", "date", "-s", rtc_time.strftime("%Y-%m-%d %H:%M:%S")])
        logging.info("RTC auf Systemzeit synchronisiert")
    except (ValueError, OSError, RTCUnavailableError) as e:
        logging.warning(f"RTC-Sync übersprungen: {e}")


if not TESTING:
    sync_rtc_to_system()

# DB Setup
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cursor = conn.cursor()
cursor.execute(
    """CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, password TEXT)"""
)
cursor.execute(
    """CREATE TABLE IF NOT EXISTS audio_files (id INTEGER PRIMARY KEY, filename TEXT)"""
)
cursor.execute(
    """CREATE TABLE IF NOT EXISTS schedules (id INTEGER PRIMARY KEY, item_id INTEGER, item_type TEXT, time TEXT, repeat TEXT, delay INTEGER, executed INTEGER DEFAULT 0)"""
)
try:
    cursor.execute("ALTER TABLE schedules ADD COLUMN executed INTEGER DEFAULT 0")
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
if not cursor.execute("SELECT * FROM users").fetchone():
    cursor.execute(
        "INSERT INTO users (username, password) VALUES (?, ?)",
        ("admin", generate_password_hash("password")),
    )
conn.commit()

# Scheduler
scheduler = BackgroundScheduler()


def get_setting(key, default=None):
    row = cursor.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(key, value):
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()


RELAY_INVERT = get_setting("relay_invert", "0") == "1"
AMP_ON_LEVEL = 0 if RELAY_INVERT else 1
AMP_OFF_LEVEL = 1 if RELAY_INVERT else 0


def update_amp_levels():
    global AMP_ON_LEVEL, AMP_OFF_LEVEL
    AMP_ON_LEVEL = 0 if RELAY_INVERT else 1
    AMP_OFF_LEVEL = 1 if RELAY_INVERT else 0


class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username


@login_manager.user_loader
def load_user(user_id):
    cursor.execute("SELECT * FROM users WHERE id=?", (user_id,))
    user_data = cursor.fetchone()
    if user_data:
        return User(user_data[0], user_data[1])
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
    try:
        return datetime.fromisoformat(time_str.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(time_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Ungültige Zeitangabe: {time_str}")


# PulseAudio
def get_current_sink():
    return subprocess.getoutput("pactl get-default-sink")


def set_sink(sink_name):
    subprocess.call(["pactl", "set-default-sink", sink_name])
    logging.info(f"Switch zu Sink: {sink_name}")


# GPIO für Endstufe
def activate_amplifier():
    global amplifier_claimed
    if not amplifier_claimed:
        try:
            GPIO.gpio_claim_output(
                gpio_handle, GPIO_PIN_ENDSTUFE, lFlags=0, level=AMP_OFF_LEVEL
            )
            amplifier_claimed = True
            GPIO.gpio_write(gpio_handle, GPIO_PIN_ENDSTUFE, AMP_ON_LEVEL)
            logging.info("Endstufe EIN")
        except GPIO.error as e:
            if "GPIO busy" in str(e):
                logging.warning("GPIO bereits belegt, überspringe claim")
            else:
                raise e
    else:
        GPIO.gpio_write(gpio_handle, GPIO_PIN_ENDSTUFE, AMP_ON_LEVEL)
        logging.info("Endstufe EIN (bereits belegt)")


def deactivate_amplifier():
    global amplifier_claimed
    if amplifier_claimed:
        try:
            GPIO.gpio_write(gpio_handle, GPIO_PIN_ENDSTUFE, AMP_OFF_LEVEL)
            GPIO.gpio_free(gpio_handle, GPIO_PIN_ENDSTUFE)
            amplifier_claimed = False
            logging.info("Endstufe AUS")
        except GPIO.error as e:
            if "GPIO busy" in str(e):
                logging.warning("GPIO busy beim deaktivieren, ignoriere")
            else:
                raise e


# Endstufe beim Start aus
if not TESTING:
    deactivate_amplifier()

play_lock = threading.Lock()


# Wiedergabe Funktion
def play_item(item_id, item_type, delay, is_schedule=False):
    global is_paused
    with play_lock:
        if pygame.mixer.music.get_busy():
            logging.info(
                f"Skippe Wiedergabe für {item_type} {item_id}, da andere läuft"
            )
            return
        set_sink(DAC_SINK)
        activate_amplifier()
        time.sleep(delay)
        logging.info(f"Starte Wiedergabe für {item_type} {item_id}")
        tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        temp_path = tmp_file.name
        tmp_file.close()
        try:
            if item_type == "file":
                cursor.execute(
                    "SELECT filename FROM audio_files WHERE id=?", (item_id,)
                )
                row = cursor.fetchone()
                if not row:
                    logging.warning(f"Audio-Datei-ID {item_id} nicht gefunden")
                    return
                filename = row[0]
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
                sound = AudioSegment.from_file(file_path)
                normalized = sound.normalize(headroom=0.1)
                normalized.export(temp_path, format="wav")
                pygame.mixer.music.load(temp_path)
                pygame.mixer.music.play()
                is_paused = False
                while pygame.mixer.music.get_busy():
                    time.sleep(1)
            elif item_type == "playlist":
                cursor.execute(
                    "SELECT f.filename FROM playlist_files pf JOIN audio_files f ON pf.file_id = f.id WHERE pf.playlist_id=?",
                    (item_id,),
                )
                files = cursor.fetchall()
                for filename in files:
                    file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename[0])
                    if not os.path.exists(file_path):
                        logging.warning(f"Datei fehlt: {file_path}")
                        if not is_schedule:
                            try:
                                if has_request_context():
                                    flash("Audio-Datei nicht gefunden")
                            except Exception:
                                pass
                        continue
                    sound = AudioSegment.from_file(file_path)
                    normalized = sound.normalize(headroom=0.1)
                    normalized.export(temp_path, format="wav")
                    pygame.mixer.music.load(temp_path)
                    pygame.mixer.music.play()
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
    cursor.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,))
    sch = cursor.fetchone()
    if sch is None:
        logging.warning(f"Schedule {schedule_id} nicht gefunden")
        return
    item_id = sch[1]
    item_type = sch[2]
    delay = sch[5]
    repeat = sch[4]
    play_item(item_id, item_type, delay, is_schedule=True)
    if repeat == "once":
        cursor.execute(
            "UPDATE schedules SET executed=1 WHERE id=?",
            (schedule_id,),
        )
        conn.commit()
        load_schedules()


def skip_past_once_schedules():
    """Markiert abgelaufene Einmal-Zeitpläne als ausgeführt (Grace-Zeit)."""
    now = datetime.now()
    cursor.execute("SELECT id, time FROM schedules WHERE repeat='once' AND executed=0")
    for sch_id, sch_time in cursor.fetchall():
        try:
            run_time = parse_once_datetime(sch_time)
            if run_time < now + timedelta(seconds=1):
                cursor.execute("UPDATE schedules SET executed=1 WHERE id=?", (sch_id,))
                logging.info(f"Skippe überfälligen 'once' Schedule {sch_id}")
        except ValueError:
            logging.warning(f"Skippe Schedule {sch_id} mit ungültiger Zeit {sch_time}")
    conn.commit()


def load_schedules():
    scheduler.remove_all_jobs()
    cursor.execute("SELECT * FROM schedules")
    for sch in cursor.fetchall():
        sch_id = sch[0]
        time_str = sch[3]
        repeat = sch[4]
        executed = sch[6]
        if executed:
            continue
        misfire_grace_time = 1
        try:
            if repeat == "once":
                run_time = parse_once_datetime(time_str)
                trigger = DateTrigger(run_date=run_time)
            elif repeat == "daily":
                h, m, s = time_str.split(":")
                trigger = CronTrigger(hour=h, minute=m, second=s)
            elif repeat == "monthly":
                h, m, s = time_str.split(":")
                trigger = CronTrigger(day=1, hour=h, minute=m, second=s)
            else:
                logging.warning(f"Unbekannter Repeat-Typ {repeat} für Schedule {sch_id}")
                continue
            scheduler.add_job(
                schedule_job,
                trigger,
                args=[sch_id],
                misfire_grace_time=misfire_grace_time,
                id=str(sch_id),
            )
            logging.info(
                f"Geplanter Job {sch_id}: Repeat={repeat}, Time={time_str}, Misfire-Grace={misfire_grace_time}"
            )
        except ValueError:
            logging.warning(f"Ungültige Zeit {time_str} für Schedule {sch_id}")


if not TESTING:
    skip_past_once_schedules()
    load_schedules()
    scheduler.start()


# --- Bluetooth-Hilfsfunktionen ---
def is_bt_connected():
    """Prüft, ob ein Bluetooth-Gerät verbunden ist."""
    try:
        sinks = subprocess.getoutput("pactl list short sinks | grep bluez")
        return bool(sinks.strip())
    except Exception as e:
        logging.error(f"Fehler beim Prüfen der Bluetooth-Verbindung: {e}")
        return False


def resume_bt_audio():
    """Stellt den Bluetooth-Sink wieder als Standard ein."""
    try:
        sink_lines = subprocess.getoutput(
            "pactl list short sinks | grep bluez"
        ).splitlines()
        if not sink_lines:
            logging.info("Kein Bluetooth-Sink zum Resume gefunden")
            return
        bt_sink = sink_lines[0].split()[1]
        set_sink(bt_sink)
        logging.info(f"Bluetooth-Sink {bt_sink} wieder aktiv")
    except Exception as e:
        logging.error(f"Fehler beim Aktivieren des Bluetooth-Sinks: {e}")


def load_loopback():
    """Aktiviert PulseAudio-Loopback von der Bluetooth-Quelle zum DAC."""
    try:
        modules = subprocess.getoutput("pactl list short modules").splitlines()
        for mod in modules:
            if "module-loopback" in mod and DAC_SINK in mod:
                logging.info("Loopback bereits aktiv")
                return
        sources = subprocess.getoutput(
            "pactl list short sources | grep bluez"
        ).splitlines()
        if not sources:
            logging.info("Kein Bluetooth-Source für Loopback gefunden")
            return
        bt_source = sources[0].split()[1]
        subprocess.call(
            [
                "pactl",
                "load-module",
                "module-loopback",
                f"source={bt_source}",
                f"sink={DAC_SINK}",
                "latency_msec=30",
            ]
        )
        logging.info(f"Loopback geladen: {bt_source} -> {DAC_SINK}")
    except Exception as e:
        logging.error(f"Fehler beim Laden des Loopback-Moduls: {e}")


# --- Bluetooth Audio Monitor (A2DP-Sink Erkennung & Verstärkersteuerung) ---
def is_bt_audio_active():
    # Prüft, ob ein Bluetooth-Audio-Stream anliegt (A2DP)
    sinks = subprocess.getoutput("pactl list short sinks | grep bluez").splitlines()
    if not sinks:
        return False
    for sink in sinks:
        sink_name = sink.split()[1]
        # Gibt es einen aktiven Stream auf diesem Sink?
        inputs = subprocess.getoutput(
            f"pactl list short sink-inputs | grep {sink_name}"
        )
        if inputs.strip():
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
    if not has_network():
        logging.info("Kein Netzwerk – starte AP-Modus")
        subprocess.call(["sudo", "systemctl", "start", "dnsmasq"])
        subprocess.call(["sudo", "systemctl", "start", "hostapd"])
    else:
        disable_ap()


def disable_ap():
    subprocess.call(["sudo", "systemctl", "stop", "hostapd"])
    subprocess.call(["sudo", "systemctl", "stop", "dnsmasq"])
    logging.info("AP-Modus deaktiviert")


# ---- Flask Web-UI ----
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        cursor.execute("SELECT * FROM users WHERE username=?", (username,))
        user_data = cursor.fetchone()
        if user_data and check_password_hash(user_data[2], password):
            user = User(user_data[0], username)
            login_user(user)
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
    cursor.execute("SELECT * FROM audio_files")
    files = cursor.fetchall()
    cursor.execute("SELECT * FROM playlists")
    playlists = cursor.fetchall()
    cursor.execute(
        "SELECT s.id, CASE WHEN s.item_type='file' THEN f.filename ELSE p.name END as name, s.time, s.repeat, s.delay, s.item_type, s.executed FROM schedules s LEFT JOIN audio_files f ON s.item_id = f.id AND s.item_type='file' LEFT JOIN playlists p ON s.item_id = p.id AND s.item_type='playlist'"
    )
    schedules = cursor.fetchall()
    wlan_ssid = subprocess.getoutput("iwgetid wlan0 -r").strip() or "Nicht verbunden"
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    current_volume = (
        subprocess.getoutput(
            'pactl get-sink-volume @DEFAULT_SINK@ | grep -oP "\\d+%" | head -1'
        )
        or "Unbekannt"
    )
    status = {
        "playing": pygame.mixer.music.get_busy(),
        "bluetooth_status": "Verbunden" if is_bt_connected() else "Nicht verbunden",
        "wlan_status": wlan_ssid,
        "current_sink": get_current_sink(),
        "current_time": current_time,
        "amplifier_status": "An" if amplifier_claimed else "Aus",
        "relay_invert": RELAY_INVERT,
        "current_volume": current_volume,
    }
    return render_template(
        "index.html",
        files=files,
        playlists=playlists,
        schedules=schedules,
        status=status,
    )


@app.route("/upload", methods=["POST"])
@login_required
def upload():
    if "file" not in request.files:
        flash("Keine Datei ausgewählt")
        return redirect(request.url)
    file = request.files["file"]
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
        cursor.execute("INSERT INTO audio_files (filename) VALUES (?)", (filename,))
        conn.commit()
    return redirect(url_for("index"))


@app.route("/delete/<int:file_id>", methods=["POST"])
@login_required
def delete(file_id):
    cursor.execute("SELECT filename FROM audio_files WHERE id=?", (file_id,))
    row = cursor.fetchone()
    if not row:
        flash("Datei nicht gefunden")
        return redirect(url_for("index"))
    filename = row[0]
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
    cursor.execute("INSERT INTO playlists (name) VALUES (?)", (name,))
    conn.commit()
    flash("Playlist erstellt")
    return redirect(url_for("index"))


@app.route("/add_to_playlist", methods=["POST"])
@login_required
def add_to_playlist():
    playlist_id = request.form["playlist_id"]
    file_id = request.form["file_id"]
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
    cursor.execute("DELETE FROM playlists WHERE id=?", (playlist_id,))
    cursor.execute("DELETE FROM playlist_files WHERE playlist_id=?", (playlist_id,))
    cursor.execute(
        "DELETE FROM schedules WHERE item_id=? AND item_type='playlist'", (playlist_id,)
    )
    conn.commit()
    flash("Playlist gelöscht")
    return redirect(url_for("index"))


@app.route("/play_now/<string:item_type>/<int:item_id>")
@login_required
def play_now(item_type, item_id):
    delay = VERZOEGERUNG_SEC
    threading.Thread(target=play_item, args=(item_id, item_type, delay, False)).start()
    flash("Abspielen gestartet")
    return redirect(url_for("index"))


@app.route("/toggle_pause", methods=["POST"])
@login_required
def toggle_pause():
    global is_paused
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
    try:
        activate_amplifier()
        flash("Endstufe aktiviert")
    except GPIO.error as e:
        flash(f"Fehler beim Aktivieren der Endstufe: {str(e)}")
    return redirect(url_for("index"))


@app.route("/deactivate_amp", methods=["POST"])
@login_required
def deactivate_amp():
    try:
        deactivate_amplifier()
        flash("Endstufe deaktiviert")
    except GPIO.error as e:
        flash(f"Fehler beim Deaktivieren der Endstufe: {str(e)}")
    return redirect(url_for("index"))


@app.route("/set_relay_invert", methods=["POST"])
@login_required
def set_relay_invert():
    global RELAY_INVERT
    RELAY_INVERT = "invert" in request.form
    set_setting("relay_invert", "1" if RELAY_INVERT else "0")
    update_amp_levels()
    flash("Relais-Logik invertiert" if RELAY_INVERT else "Relais-Logik normal")
    return redirect(url_for("index"))


@app.route("/schedule", methods=["POST"])
@login_required
def add_schedule():
    item_type = request.form["item_type"]
    item_id = request.form["item_id"]
    time_str = request.form["time"]  # Erwarte Format YYYY-MM-DDTHH:MM
    repeat = request.form["repeat"]
    delay = int(request.form["delay"])

    try:
        dt = parse_once_datetime(time_str) if repeat == "once" else datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        if repeat == "once":
            time_only = dt.strftime("%Y-%m-%d %H:%M:%S")
        else:
            time_only = dt.strftime("%H:%M:%S")
            if not validate_time(time_only):
                flash("Ungültiges Zeitformat")
                return redirect(url_for("index"))
    except ValueError:
        flash("Ungültiges Datums-/Zeitformat")
        return redirect(url_for("index"))

    if item_type not in ("file", "playlist"):
        flash("Ungültiger Typ ausgewählt")
        return redirect(url_for("index"))

    if not item_id:
        flash("Kein Element gewählt")
        return redirect(url_for("index"))

    cursor.execute(
        "INSERT INTO schedules (item_id, item_type, time, repeat, delay, executed) VALUES (?, ?, ?, ?, ?, 0)",
        (item_id, item_type, time_only, repeat, delay),
    )
    conn.commit()
    load_schedules()
    flash("Zeitplan hinzugefügt")
    return redirect(url_for("index"))


@app.route("/delete_schedule/<int:sch_id>", methods=["POST"])
@login_required
def delete_schedule(sch_id):
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


@app.route("/wlan_connect", methods=["POST"])
@login_required
def wlan_connect():
    ssid = request.form["ssid"]
    password = request.form["password"]
    # Escape special characters so wpa_cli parses them correctly
    ssid_escaped = ssid.encode("unicode_escape").decode()
    password_escaped = password.encode("unicode_escape").decode()
    try:
        net_id = (
            subprocess.check_output(["sudo", "wpa_cli", "-i", "wlan0", "add_network"])
            .decode()
            .strip()
        )
        subprocess.check_call(
            [
                "sudo",
                "wpa_cli",
                "-i",
                "wlan0",
                "set_network",
                net_id,
                "ssid",
                f'"{ssid_escaped}"',
            ]
        )
        subprocess.check_call(
            [
                "sudo",
                "wpa_cli",
                "-i",
                "wlan0",
                "set_network",
                net_id,
                "psk",
                f'"{password_escaped}"',
            ]
        )
        subprocess.check_call(
            ["sudo", "wpa_cli", "-i", "wlan0", "enable_network", net_id]
        )
        subprocess.check_call(["sudo", "wpa_cli", "-i", "wlan0", "save_config"])
        subprocess.check_call(["sudo", "wpa_cli", "-i", "wlan0", "reconfigure"])
        flash("Versuche, mit WLAN zu verbinden")
    except subprocess.CalledProcessError as e:
        logging.error(f"Fehler beim WLAN-Verbindungsaufbau: {e}")
        flash("Fehler beim WLAN-Verbindungsaufbau")
    return redirect(url_for("index"))


@app.route("/volume", methods=["POST"])
@login_required
def set_volume():
    vol = request.form["volume"]
    try:
        int_vol = int(vol)
        if not 0 <= int_vol <= 100:
            raise ValueError
        pygame.mixer.music.set_volume(int_vol / 100.0)
        current_sink = get_current_sink()
        subprocess.call(["pactl", "set-sink-volume", current_sink, f"{int_vol}%"])
        subprocess.call(["amixer", "sset", "Master", f"{int_vol}%"])
        subprocess.call(["sudo", "alsactl", "store"])
        logging.info(f"Lautstärke auf {int_vol}% gesetzt (persistent)")
        flash("Lautstärke persistent gesetzt")
    except ValueError:
        flash("Ungültiger Lautstärke-Wert")
    except Exception as e:
        logging.error(f"Fehler beim Setzen der Lautstärke: {e}")
        flash("Fehler beim Setzen der Lautstärke")
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
    if request.method == "POST":
        old_pass = request.form["old_password"]
        new_pass = request.form["new_password"]
        if not new_pass or len(new_pass) < 4:
            flash("Neues Passwort zu kurz")
            return render_template("change_password.html")
        cursor.execute("SELECT password FROM users WHERE id=?", (current_user.id,))
        hashed = cursor.fetchone()[0]
        if check_password_hash(hashed, old_pass):
            new_hashed = generate_password_hash(new_pass)
            cursor.execute(
                "UPDATE users SET password=? WHERE id=?", (new_hashed, current_user.id)
            )
            conn.commit()
            flash("Passwort geändert")
        else:
            flash("Falsches altes Passwort")
    return render_template("change_password.html")


@app.route("/set_time", methods=["GET", "POST"])
@login_required
def set_time():
    if request.method == "POST":
        time_str = request.form["datetime"]
        try:
            dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            subprocess.call(["sudo", "date", "-s", dt.strftime("%Y-%m-%d %H:%M:%S")])
            set_rtc(dt)
            flash("Datum und Uhrzeit gesetzt")
        except (ValueError, RTCUnavailableError):
            flash("Ungültiges Datums-/Zeitformat oder RTC nicht verfügbar")
        return redirect(url_for("index"))
    return render_template("set_time.html")


@app.route("/sync_time_from_internet")
@login_required
def sync_time_from_internet():
    try:
        subprocess.call(["sudo", "systemctl", "stop", "systemd-timesyncd"])
        subprocess.call(["sudo", "ntpdate", "pool.ntp.org"])
        subprocess.call(["sudo", "systemctl", "start", "systemd-timesyncd"])
        set_rtc(datetime.now())
        flash("Zeit vom Internet synchronisiert")
    except Exception as e:
        logging.error(f"Fehler bei Zeit-Sync: {e}")
        flash("Fehler bei der Synchronisation")
    return redirect(url_for("index"))


@app.route("/update", methods=["POST"])
@login_required
def update():
    try:
        subprocess.check_call(["git", "pull"])
        flash("Update erfolgreich")
    except subprocess.CalledProcessError as e:
        logging.error(f"Update fehlgeschlagen: {e}")
        flash("Update fehlgeschlagen")
    return redirect(url_for("index"))


def enable_bluetooth():
    subprocess.check_call(["sudo", "bluetoothctl", "power", "on"])
    bluetooth_auto_accept()


def disable_bluetooth():
    subprocess.check_call(["sudo", "bluetoothctl", "power", "off"])


@app.route("/bluetooth_on", methods=["POST"])
@login_required
def bluetooth_on():
    try:
        enable_bluetooth()
        flash("Bluetooth aktiviert")
    except subprocess.CalledProcessError as e:
        logging.error(f"Bluetooth einschalten fehlgeschlagen: {e}")
        flash("Bluetooth konnte nicht aktiviert werden")
    return redirect(url_for("index"))


@app.route("/bluetooth_off", methods=["POST"])
@login_required
def bluetooth_off():
    try:
        disable_bluetooth()
        flash("Bluetooth deaktiviert")
    except subprocess.CalledProcessError as e:
        logging.error(f"Bluetooth ausschalten fehlgeschlagen: {e}")
        flash("Bluetooth konnte nicht deaktiviert werden")
    return redirect(url_for("index"))


def bluetooth_auto_accept():
    p = subprocess.Popen(
        ["sudo", "bluetoothctl"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    commands = [
        "power on",
        "discoverable on",
        "pairable on",
        "agent on",
        "default-agent",
    ]
    stdout, stderr = p.communicate("\n".join(commands) + "\nexit\n")
    logging.info(f"Bluetooth auto-accept setup: {stdout} {stderr}")


if not TESTING:
    threading.Thread(target=bluetooth_auto_accept, daemon=True).start()
    threading.Thread(target=bt_audio_monitor, daemon=True).start()

if __name__ == "__main__":
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    try:
        app.run(host="0.0.0.0", port=8080, debug=debug)
    finally:
        scheduler.shutdown()
