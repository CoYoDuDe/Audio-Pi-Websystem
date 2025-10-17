#!/bin/bash
set -e

echo "---- Audio Pi Websystem Installer ----"
echo "Starte als Root/Sudo empfohlen..."

# System-Update
sudo apt update
sudo apt upgrade -y

# Python-Basics & PIP
sudo apt install -y python3 python3-pip python3-venv sqlite3

# Virtuelle Umgebung einrichten
python3 -m venv venv
source venv/bin/activate


# Dev-Packages (für pydub/pygame etc.)
sudo apt install -y libasound2-dev libpulse-dev libportaudio2 ffmpeg libffi-dev libjpeg-dev libbluetooth-dev

# Python-Abhängigkeiten installieren
pip install -r requirements.txt

# Benutzer nach Secret fragen und in Profil speichern
SECRET=""
while [ -z "$SECRET" ]; do
    read -rp "FLASK_SECRET_KEY (darf nicht leer sein): " SECRET
done
ESCAPED_SECRET=$(printf '%s\n' "$SECRET" | sed 's/[&/]/\\&/g')
echo "export FLASK_SECRET_KEY=\"$SECRET\"" >> ~/.profile

# I²C für RTC aktivieren
sudo raspi-config nonint do_i2c 0
echo "i2c-dev" | sudo tee -a /etc/modules

# Werkzeuge für die automatische RTC-Erkennung bereitstellen
sudo apt install -y i2c-tools

detect_rtc_devices() {
    RTC_DETECTED_BUS=""
    RTC_DETECTED_ADDRESSES=()
    if ! command -v i2cdetect >/dev/null 2>&1; then
        return 1
    fi

    local bus
    for bus in 1 0; do
        local output
        output=$(sudo i2cdetect -y "$bus" 2>/dev/null || true)
        if [ -z "$output" ]; then
            continue
        fi
        mapfile -t addresses < <(printf '%s\n' "$output" | awk 'NR>1 {for (i=2; i<=NF; i++) if ($i != "--") printf("0x%s\n", $i)}')
        if [ "${#addresses[@]}" -gt 0 ]; then
            RTC_DETECTED_BUS="$bus"
            RTC_DETECTED_ADDRESSES=("${addresses[@]}")
            return 0
        fi
    done
    return 1
}

infer_rtc_from_addresses() {
    RTC_AUTODETECT_MODULE=""
    RTC_AUTODETECT_LABEL=""
    RTC_AUTODETECT_OVERLAY="pcf8563"

    local addr
    for addr in "${RTC_DETECTED_ADDRESSES[@]}"; do
        local addr_lower
        addr_lower=$(printf '%s' "$addr" | tr 'A-F' 'a-f')
        case "$addr_lower" in
            0x51)
                RTC_AUTODETECT_MODULE="pcf8563"
                RTC_AUTODETECT_LABEL="PCF8563 (0x51)"
                RTC_AUTODETECT_OVERLAY="pcf8563"
                return 0
                ;;
        esac
    done

    for addr in "${RTC_DETECTED_ADDRESSES[@]}"; do
        local addr_lower
        addr_lower=$(printf '%s' "$addr" | tr 'A-F' 'a-f')
        case "$addr_lower" in
            0x68|0x69|0x57|0x6f)
                RTC_AUTODETECT_MODULE="ds3231"
                RTC_AUTODETECT_LABEL="DS3231 / DS1307 (0x68)"
                RTC_AUTODETECT_OVERLAY="ds3231"
                return 0
                ;;
        esac
    done

    return 1
}

format_detected_addresses() {
    local formatted=""
    local addr
    for addr in "${RTC_DETECTED_ADDRESSES[@]}"; do
        local value formatted_addr
        value=$((16#${addr#0x}))
        printf -v formatted_addr '0x%02X' "$value"
        if [ -n "$formatted" ]; then
            formatted+=", "
        fi
        formatted+="$formatted_addr"
    done
    printf '%s' "$formatted"
}

echo ""
echo "RTC-Konfiguration"

RTC_MODULE="auto"
RTC_OVERLAY_DEFAULT="pcf8563"
RTC_ADDRESS_INPUT=""
RTC_AUTODETECT_ACCEPTED=0

if detect_rtc_devices; then
    RTC_DETECTED_ADDRESS_STRING=$(format_detected_addresses)
    echo "Automatische Erkennung: Gefundene I²C-Adresse(n) auf Bus ${RTC_DETECTED_BUS}: $RTC_DETECTED_ADDRESS_STRING"
    if infer_rtc_from_addresses; then
        echo "Vermutetes RTC-Modul: $RTC_AUTODETECT_LABEL"
    else
        echo "Hinweis: Gefundene Adresse(n) konnten keinem bekannten RTC-Typ eindeutig zugeordnet werden."
    fi
    read -rp "Automatische Erkennung übernehmen? [J/n]: " RTC_AUTODETECT_CONFIRM
    RTC_AUTODETECT_CONFIRM=${RTC_AUTODETECT_CONFIRM,,}
    if [ "$RTC_AUTODETECT_CONFIRM" != "n" ] && [ "$RTC_AUTODETECT_CONFIRM" != "nein" ]; then
        RTC_AUTODETECT_ACCEPTED=1
        if [ -n "$RTC_AUTODETECT_MODULE" ]; then
            RTC_MODULE="$RTC_AUTODETECT_MODULE"
        fi
        RTC_OVERLAY_DEFAULT="$RTC_AUTODETECT_OVERLAY"
        RTC_ADDRESS_INPUT="$RTC_DETECTED_ADDRESS_STRING"
    fi
fi

if [ "$RTC_AUTODETECT_ACCEPTED" -eq 0 ]; then
    echo "1) Automatische Erkennung (Standard)"
    echo "2) PCF8563 (0x51)"
    echo "3) DS3231 / DS1307 (0x68)"
    read -rp "Auswahl [1-3]: " RTC_CHOICE

    case "$RTC_CHOICE" in
        2)
            RTC_MODULE="pcf8563"
            RTC_OVERLAY_DEFAULT="pcf8563"
            ;;
        3)
            RTC_MODULE="ds3231"
            RTC_OVERLAY_DEFAULT="ds3231"
            ;;
        *)
            RTC_MODULE="auto"
            RTC_OVERLAY_DEFAULT="pcf8563"
            ;;
    esac

    read -rp "Eigene I²C-Adressen (optional, Kommagetrennt, z.B. 0x51,0x68): " RTC_ADDRESS_INPUT
else
    if [ -n "$RTC_ADDRESS_INPUT" ]; then
        read -rp "Eigene I²C-Adressen (Enter übernimmt '$RTC_ADDRESS_INPUT'): " RTC_ADDRESS_OVERRIDE
        if [ -n "$RTC_ADDRESS_OVERRIDE" ]; then
            RTC_ADDRESS_INPUT="$RTC_ADDRESS_OVERRIDE"
        fi
    else
        read -rp "Eigene I²C-Adressen (optional, Kommagetrennt, z.B. 0x51,0x68): " RTC_ADDRESS_INPUT
    fi
fi

read -rp "dtoverlay für RTC (leer für '$RTC_OVERLAY_DEFAULT', '-' zum Überspringen): " RTC_OVERLAY_INPUT

if [ -z "$RTC_OVERLAY_INPUT" ]; then
    RTC_OVERLAY_INPUT="$RTC_OVERLAY_DEFAULT"
fi
if [ "$RTC_OVERLAY_INPUT" = "-" ] || [ "$RTC_OVERLAY_INPUT" = "none" ]; then
    RTC_OVERLAY_INPUT=""
fi

if [ -n "$RTC_OVERLAY_INPUT" ]; then
    sudo sed -i '/^dtoverlay=i2c-rtc/d' /boot/config.txt
    echo "dtoverlay=i2c-rtc,$RTC_OVERLAY_INPUT" | sudo tee -a /boot/config.txt
fi

RTC_MODULE_ESC=$(printf '%s' "$RTC_MODULE" | sed "s/'/''/g")
RTC_ADDRESS_ESC=$(printf '%s' "$RTC_ADDRESS_INPUT" | sed "s/'/''/g")

if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 audio.db <<SQL
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
INSERT OR REPLACE INTO settings (key, value) VALUES ('rtc_module_type', '$RTC_MODULE_ESC');
INSERT OR REPLACE INTO settings (key, value) VALUES ('rtc_addresses', '$RTC_ADDRESS_ESC');
SQL
    echo "RTC-Einstellungen wurden in audio.db übernommen."
else
    echo "Warnung: sqlite3 nicht verfügbar – RTC-Einstellungen konnten nicht gespeichert werden."
fi

# PulseAudio Setup für Pi (z.B. HiFiBerry DAC)
sudo apt install -y pulseaudio pulseaudio-utils
sudo usermod -aG pulse,pulse-access,audio "$USER"

# Bluetooth Audio Setup – Nur SINK (kein Agent)
sudo apt install -y pulseaudio-module-bluetooth bluez-tools bluez

# Hostapd & dnsmasq (WLAN AP)
sudo apt install -y hostapd dnsmasq wireless-tools iw wpasupplicant

# ALSA für Mixer/Fallback
sudo apt install -y alsa-utils

# Upload-Verzeichnis anlegen
mkdir -p uploads
chmod 777 uploads

# Logfile anlegen
touch app.log
chmod 666 app.log

# DB anlegen falls nicht da (Initialisierung passiert beim ersten Start)
[ -f audio.db ] || touch audio.db

# systemd-Dienst einrichten
sudo cp audio-pi.service /etc/systemd/system/
sudo sed -i "s|/opt/Audio-Pi-Websystem|$(pwd)|g" /etc/systemd/system/audio-pi.service
sudo sed -i "s|Environment=FLASK_SECRET_KEY=.*|Environment=FLASK_SECRET_KEY=$ESCAPED_SECRET|" /etc/systemd/system/audio-pi.service
sudo systemctl daemon-reload
sudo systemctl enable --now audio-pi.service

# Hinweis für Bluetooth-SINK Setup
echo ""
echo "Hinweis: Bluetooth-SINK wird automatisch mit PulseAudio bereitgestellt."
echo "Nach dem Pairing mit dem Handy kann Musik über den Pi abgespielt werden!"

# Optional: Reboot nach RTC/Overlay nötig
echo "Wenn RTC/I2C/Overlay neu eingerichtet wurden: Bitte RASPBERRY PI NEU STARTEN!"

echo ""
echo "Setup abgeschlossen! Starte mit:"
echo "source venv/bin/activate && python app.py"
echo ""
echo "Öffne im Browser: http://<RaspberryPi-IP>:8080"
echo ""
echo "Default Login: admin / password"
echo ""
