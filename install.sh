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

echo ""
echo "RTC-Konfiguration"
echo "1) Automatische Erkennung (Standard)"
echo "2) PCF8563 (0x51)"
echo "3) DS3231 / DS1307 (0x68)"
read -rp "Auswahl [1-3]: " RTC_CHOICE

RTC_MODULE="auto"
RTC_OVERLAY_DEFAULT="pcf8563"
case "$RTC_CHOICE" in
    2)
        RTC_MODULE="pcf8563"
        RTC_OVERLAY_DEFAULT="pcf8563"
        ;;
    3)
        RTC_MODULE="ds3231"
        RTC_OVERLAY_DEFAULT="ds3231"
        ;;
esac

read -rp "Eigene I²C-Adressen (optional, Kommagetrennt, z.B. 0x51,0x68): " RTC_ADDRESS_INPUT
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
