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
TARGET_USER=${SUDO_USER:-$USER}
TARGET_UID=$(id -u "$TARGET_USER")
TARGET_GROUP=$(id -gn "$TARGET_USER")
TARGET_HOME=$(eval echo "~$TARGET_USER")
printf 'export FLASK_SECRET_KEY=%q\n' "$SECRET" | sudo tee -a "$TARGET_HOME/.profile"
echo "FLASK_SECRET_KEY wurde in $TARGET_HOME/.profile hinterlegt."
SED_ESCAPED_SECRET=$(printf '%s' "$SECRET" | sed -e 's/[&/]/\\&/g')

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
    if [ -f audio.db ]; then
        sudo chown "$TARGET_USER:$TARGET_GROUP" audio.db
        sudo chmod 660 audio.db
    fi
    echo "RTC-Einstellungen wurden in audio.db übernommen."
else
    echo "Warnung: sqlite3 nicht verfügbar – RTC-Einstellungen konnten nicht gespeichert werden."
fi

# PulseAudio Setup für Pi (z.B. HiFiBerry DAC)
sudo apt install -y pulseaudio pulseaudio-utils
sudo usermod -aG pulse "$TARGET_USER"
sudo usermod -aG pulse-access "$TARGET_USER"
sudo usermod -aG audio "$TARGET_USER"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HAT_DEFAULT_SINK_HINT="alsa_output.platform-soc_107c000000_sound.stereo-fallback"

apply_hat_overlay() {
    local overlay_name="$1"
    local overlay_opts="$2"
    if [ -z "$overlay_name" ]; then
        return 0
    fi

    local overlay_line="dtoverlay=${overlay_name}"
    if [ -n "$overlay_opts" ]; then
        overlay_line+=",$overlay_opts"
    fi

    local tmp_file
    tmp_file=$(mktemp)
    sudo cp /boot/config.txt "/boot/config.txt.hat.bak.$(date +%s)"
    sudo awk -v overlay="$overlay_name" '
        BEGIN { pattern = "^dtoverlay=" overlay "([[:space:]],|$)" }
        $0 ~ pattern { next }
        { print }
    ' /boot/config.txt > "$tmp_file"
    sudo mv "$tmp_file" /boot/config.txt
    sudo chmod 644 /boot/config.txt
    echo "$overlay_line" | sudo tee -a /boot/config.txt >/dev/null
    echo "dtoverlay in /boot/config.txt gesetzt: $overlay_line"
}

ensure_audio_dtparam() {
    local disable="$1"
    if [ "$disable" = "1" ]; then
        sudo sed -i '/^dtparam=audio=/d' /boot/config.txt
        if ! sudo grep -q '^dtparam=audio=off' /boot/config.txt; then
            echo "dtparam=audio=off" | sudo tee -a /boot/config.txt >/dev/null
        fi
        echo "Onboard-Audio (dtparam=audio=off) gesetzt."
    else
        sudo sed -i '/^dtparam=audio=/d' /boot/config.txt
        echo "dtparam=audio=on" | sudo tee -a /boot/config.txt >/dev/null
        echo "Onboard-Audio aktiviert (dtparam=audio=on)."
    fi
}

HAT_SELECTED_KEY=""
HAT_SELECTED_LABEL=""
HAT_SELECTED_OVERLAY=""
HAT_SELECTED_OPTIONS=""
HAT_SELECTED_SINK_HINT="$HAT_DEFAULT_SINK_HINT"
HAT_SELECTED_DISABLE_ONBOARD=0
HAT_SELECTED_NOTES=""

if [ -f "$SCRIPT_DIR/scripts/hat_config.sh" ]; then
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/scripts/hat_config.sh"
    hat_select_profile
else
    echo "Warnung: scripts/hat_config.sh nicht gefunden – HAT-Auswahl übersprungen."
    HAT_SELECTED_KEY="skip"
    HAT_SELECTED_LABEL="Nicht verfügbar"
    HAT_SELECTED_NOTES="Hilfsskript fehlt."
fi

if [ "$HAT_SELECTED_KEY" != "skip" ]; then
    if [ -n "$HAT_SELECTED_OVERLAY" ]; then
        apply_hat_overlay "$HAT_SELECTED_OVERLAY" "$HAT_SELECTED_OPTIONS"
    fi
    ensure_audio_dtparam "$HAT_SELECTED_DISABLE_ONBOARD"
else
    echo "Audio-HAT-Konfiguration unverändert."
fi

echo "--- Zusammenfassung Audio-HAT ---"
echo "Auswahl: $HAT_SELECTED_LABEL"
if [ -n "$HAT_SELECTED_OVERLAY" ]; then
    if [ -n "$HAT_SELECTED_OPTIONS" ]; then
        echo "dtoverlay: ${HAT_SELECTED_OVERLAY}, Optionen: ${HAT_SELECTED_OPTIONS}"
    else
        echo "dtoverlay: ${HAT_SELECTED_OVERLAY}"
    fi
else
    echo "dtoverlay: (keiner)"
fi
echo "PulseAudio-Sink/Muster: $HAT_SELECTED_SINK_HINT"
if [ -n "$HAT_SELECTED_NOTES" ]; then
    echo "Hinweis: $HAT_SELECTED_NOTES"
fi
echo "Nicht-interaktiv: setze z.B. HAT_MODEL=hifiberry_dacplus oder HAT_MODEL=manual mit HAT_DTOOVERLAY/HAT_SINK_NAME."
echo "Anpassung später: /boot/config.txt und sqlite3 audio.db 'UPDATE settings SET value=... WHERE key=\'dac_sink_name\';'"
echo "Alternativ kann DAC_SINK_NAME in ~/.profile überschrieben werden."

if [ "$HAT_SELECTED_KEY" != "skip" ]; then
    if command -v sqlite3 >/dev/null 2>&1; then
        DAC_SINK_ESC=$(printf '%s' "$HAT_SELECTED_SINK_HINT" | sed "s/'/''/g")
        sqlite3 audio.db <<SQL
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
INSERT OR REPLACE INTO settings (key, value) VALUES ('dac_sink_name', '$DAC_SINK_ESC');
SQL
        if [ -f audio.db ]; then
            sudo chown "$TARGET_USER:$TARGET_GROUP" audio.db
            sudo chmod 660 audio.db
        fi
        echo "DAC-Sink-Vorgabe wurde in audio.db gespeichert."
    else
        echo "Warnung: sqlite3 nicht verfügbar – DAC-Sink konnte nicht gespeichert werden."
    fi
fi

# Bluetooth Audio Setup – Nur SINK (kein Agent)
sudo apt install -y pulseaudio-module-bluetooth bluez-tools bluez

# Hostapd & dnsmasq (WLAN AP)
sudo apt install -y hostapd dnsmasq wireless-tools iw wpasupplicant

create_hostapd_conf() {
    local default_ssid="AudioPiAP"
    local default_channel="6"
    local default_country="DE"
    local default_interface="wlan0"

    read -rp "Access-Point-SSID [${default_ssid}]: " HOSTAPD_SSID
    HOSTAPD_SSID=${HOSTAPD_SSID:-$default_ssid}

    HOSTAPD_PASSPHRASE=""
    while [ ${#HOSTAPD_PASSPHRASE} -lt 8 ]; do
        read -rsp "WPA2-Passphrase (mind. 8 Zeichen): " HOSTAPD_PASSPHRASE
        echo ""
        if [ ${#HOSTAPD_PASSPHRASE} -lt 8 ]; then
            echo "Passphrase zu kurz, bitte erneut eingeben."
        fi
    done

    read -rp "Funkkanal [${default_channel}]: " HOSTAPD_CHANNEL
    HOSTAPD_CHANNEL=${HOSTAPD_CHANNEL:-$default_channel}

    read -rp "Ländercode (2-stellig) [${default_country}]: " HOSTAPD_COUNTRY
    HOSTAPD_COUNTRY=${HOSTAPD_COUNTRY:-$default_country}

    read -rp "WLAN-Interface für den AP [${default_interface}]: " AP_INTERFACE
    AP_INTERFACE=${AP_INTERFACE:-$default_interface}

    sudo mkdir -p /etc/hostapd
    sudo tee /etc/hostapd/hostapd.conf >/dev/null <<EOF
interface=${AP_INTERFACE}
driver=nl80211
ssid=${HOSTAPD_SSID}
hw_mode=g
channel=${HOSTAPD_CHANNEL}
wmm_enabled=1
ieee80211n=1
ieee80211d=1
country_code=${HOSTAPD_COUNTRY}
auth_algs=1
wpa=2
wpa_passphrase=${HOSTAPD_PASSPHRASE}
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
EOF

    echo "hostapd.conf wurde mit SSID '${HOSTAPD_SSID}' erstellt."
}

create_dnsmasq_conf() {
    local default_ap_ip="192.168.50.1"
    local default_dhcp_start="192.168.50.50"
    local default_dhcp_end="192.168.50.150"
    local default_dhcp_time="24h"

    read -rp "Statische AP-IP [${default_ap_ip}]: " AP_IPV4
    AP_IPV4=${AP_IPV4:-$default_ap_ip}

    read -rp "DHCP-Startadresse [${default_dhcp_start}]: " DHCP_RANGE_START
    DHCP_RANGE_START=${DHCP_RANGE_START:-$default_dhcp_start}

    read -rp "DHCP-Endadresse [${default_dhcp_end}]: " DHCP_RANGE_END
    DHCP_RANGE_END=${DHCP_RANGE_END:-$default_dhcp_end}

    read -rp "DHCP-Lease-Zeit [${default_dhcp_time}]: " DHCP_LEASE_TIME
    DHCP_LEASE_TIME=${DHCP_LEASE_TIME:-$default_dhcp_time}

    sudo mkdir -p /etc/dnsmasq.d
    sudo tee /etc/dnsmasq.d/audio-pi.conf >/dev/null <<EOF
interface=${AP_INTERFACE}
bind-interfaces
domain-needed
bogus-priv
server=1.1.1.1
server=8.8.8.8
listen-address=127.0.0.1,${AP_IPV4}
dhcp-range=${DHCP_RANGE_START},${DHCP_RANGE_END},${DHCP_LEASE_TIME}
dhcp-option=3,${AP_IPV4}
dhcp-option=6,${AP_IPV4}
EOF

    if ! sudo grep -q '^conf-file=/etc/dnsmasq.d/audio-pi.conf' /etc/dnsmasq.conf; then
        sudo cp /etc/dnsmasq.conf "/etc/dnsmasq.conf.bak.$(date +%s)"
        echo "conf-file=/etc/dnsmasq.d/audio-pi.conf" | sudo tee -a /etc/dnsmasq.conf >/dev/null
    fi

    echo "dnsmasq-Konfiguration /etc/dnsmasq.d/audio-pi.conf wurde erstellt."
}

configure_ap_networking() {
    local default_wan_interface="eth0"
    read -rp "Uplink-Interface für NAT (z.B. eth0) [${default_wan_interface}]: " WAN_INTERFACE
    WAN_INTERFACE=${WAN_INTERFACE:-$default_wan_interface}

    echo "Aktiviere WLAN-Sendeeinheit..."
    sudo rfkill unblock wlan || true

    echo "Aktiviere IPv4-Forwarding..."
    if sudo grep -q '^[[:space:]]*#\?net\.ipv4\.ip_forward' /etc/sysctl.conf; then
        sudo sed -i 's|^[[:space:]]*#\?net\.ipv4\.ip_forward=.*|net.ipv4.ip_forward=1|' /etc/sysctl.conf
    else
        echo 'net.ipv4.ip_forward=1' | sudo tee -a /etc/sysctl.conf >/dev/null
    fi
    sudo sysctl -w net.ipv4.ip_forward=1 >/dev/null

    echo "Setze iptables-Regeln für NAT..."
    sudo iptables -t nat -C POSTROUTING -o "$WAN_INTERFACE" -j MASQUERADE 2>/dev/null || sudo iptables -t nat -A POSTROUTING -o "$WAN_INTERFACE" -j MASQUERADE
    sudo iptables -C FORWARD -i "$WAN_INTERFACE" -o "$AP_INTERFACE" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || sudo iptables -A FORWARD -i "$WAN_INTERFACE" -o "$AP_INTERFACE" -m state --state RELATED,ESTABLISHED -j ACCEPT
    sudo iptables -C FORWARD -i "$AP_INTERFACE" -o "$WAN_INTERFACE" -j ACCEPT 2>/dev/null || sudo iptables -A FORWARD -i "$AP_INTERFACE" -o "$WAN_INTERFACE" -j ACCEPT
    sudo sh -c 'iptables-save > /etc/iptables.ipv4.nat'

    if [ -f /etc/rc.local ]; then
        if ! sudo grep -q 'iptables-restore < /etc/iptables.ipv4.nat' /etc/rc.local; then
            sudo sed -i 's|^exit 0||' /etc/rc.local
            echo 'iptables-restore < /etc/iptables.ipv4.nat' | sudo tee -a /etc/rc.local >/dev/null
            echo 'exit 0' | sudo tee -a /etc/rc.local >/dev/null
        fi
    fi

    echo "NAT-Konfiguration abgeschlossen (WAN: ${WAN_INTERFACE}, AP: ${AP_INTERFACE})."
}

ensure_hostapd_daemon_conf() {
    if sudo grep -q '^DAEMON_CONF=' /etc/default/hostapd; then
        sudo sed -i 's|^#\?DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd
    else
        echo 'DAEMON_CONF="/etc/hostapd/hostapd.conf"' | sudo tee -a /etc/default/hostapd >/dev/null
    fi
}

AP_CONFIGURED=0
read -rp "Soll der Access-Point-Modus eingerichtet werden? [j/N]: " AP_CONFIRM
AP_CONFIRM=${AP_CONFIRM,,}
if [ "$AP_CONFIRM" = "j" ] || [ "$AP_CONFIRM" = "ja" ]; then
    create_hostapd_conf
    ensure_hostapd_daemon_conf
    create_dnsmasq_conf
    configure_ap_networking
    sudo systemctl enable --now hostapd
    sudo systemctl enable --now dnsmasq
    AP_CONFIGURED=1
else
    echo "Access-Point-Konfiguration wurde übersprungen."
fi

# ALSA für Mixer/Fallback
sudo apt install -y alsa-utils

# Upload-Verzeichnis anlegen
mkdir -p uploads
chmod 777 uploads

# Logfile anlegen
touch app.log
chmod 666 app.log

# DB anlegen falls nicht da (Initialisierung passiert beim ersten Start)
if [ ! -f audio.db ]; then
    touch audio.db
fi
if [ -f audio.db ]; then
    sudo chown "$TARGET_USER:$TARGET_GROUP" audio.db
    sudo chmod 660 audio.db
fi

# systemd-Dienst einrichten
sudo cp audio-pi.service /etc/systemd/system/
sudo sed -i "s|/opt/Audio-Pi-Websystem|$(pwd)|g" /etc/systemd/system/audio-pi.service
sudo sed -i "s|Environment=FLASK_SECRET_KEY=.*|Environment=FLASK_SECRET_KEY=$SED_ESCAPED_SECRET|" /etc/systemd/system/audio-pi.service
sudo sed -i "s|^User=.*|User=$TARGET_USER|" /etc/systemd/system/audio-pi.service
sudo sed -i "s|^Group=.*|Group=$TARGET_GROUP|" /etc/systemd/system/audio-pi.service
sudo sed -i "s/__UID__/$TARGET_UID/" /etc/systemd/system/audio-pi.service
echo "Systemd-Dienst wird für Benutzer $TARGET_USER und Gruppe $TARGET_GROUP konfiguriert."
sudo install -d -m 0700 -o "$TARGET_USER" -g "$TARGET_GROUP" "/run/user/$TARGET_UID"
echo "Hinweis: systemd legt /run/user/$TARGET_UID beim Boot automatisch neu an."
sudo systemctl daemon-reload
sudo systemctl enable audio-pi.service
sudo systemctl restart audio-pi.service
echo "Aktuelle audio-pi.service Unit-Datei:"
sudo systemctl cat audio-pi.service
echo "Journal-Einträge für audio-pi.service:"
sudo journalctl -u audio-pi.service --no-pager | tail -n 20

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
if [ "$AP_CONFIGURED" -eq 1 ]; then
    echo "Hinweis: WLAN-Access-Point ist aktiv."
    echo "Passe SSID/Passwort in /etc/hostapd/hostapd.conf sowie DHCP-Einstellungen in /etc/dnsmasq.d/audio-pi.conf an."
    echo "Die NAT-Regeln wurden nach /etc/iptables.ipv4.nat geschrieben und können dort angepasst werden."
fi

echo ""
