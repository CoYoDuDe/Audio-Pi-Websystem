#!/bin/bash
set -e

echo "---- Audio Pi Websystem Installer ----"
echo "Starte als Root/Sudo empfohlen..."

# System-Update
sudo apt update
sudo apt upgrade -y

# Python-Basics & PIP
sudo apt install -y python3 python3-pip python3-venv

# Virtuelle Umgebung einrichten
python3 -m venv venv
source venv/bin/activate


# Dev-Packages (für pydub/pygame etc.)
sudo apt install -y libasound2-dev libpulse-dev libportaudio2 ffmpeg libffi-dev libjpeg-dev libbluetooth-dev

# Python-Abhängigkeiten installieren
pip install -r requirements.txt

# Benutzer nach Secret fragen und in Profil speichern
read -rp "FLASK_SECRET_KEY: " SECRET
echo "export FLASK_SECRET_KEY=\"$SECRET\"" >> ~/.profile

# I²C für RTC aktivieren
sudo raspi-config nonint do_i2c 0
echo "i2c-dev" | sudo tee -a /etc/modules

# RTC-Modul PCF8563 für Pi (oder DS3231 anpassen!)
if ! grep -q "dtoverlay=i2c-rtc,pcf8563" /boot/config.txt; then
    echo "dtoverlay=i2c-rtc,pcf8563" | sudo tee -a /boot/config.txt
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
sudo sed -i "s|FLASK_SECRET_KEY=|FLASK_SECRET_KEY=$SECRET|" /etc/systemd/system/audio-pi.service
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
