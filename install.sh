#!/bin/bash
set -e
set -o pipefail

usage() {
    cat <<'EOF'
Audio Pi Websystem Installer

Verwendung:
  ./install.sh [OPTIONEN]

Wichtige Optionen:
  --flask-secret-key VALUE        Flask Secret Key setzen (alternativ INSTALL_FLASK_SECRET_KEY).
  --rtc-mode MODE                 RTC-Modus: auto, pcf8563, ds3231, skip.
  --rtc-addresses LIST            Kommagetrennte I²C-Adressen (z.B. 0x51,0x68).
  --rtc-overlay VALUE             dtoverlay-Wert für die RTC ("-" oder "none" deaktiviert die Änderung).
  --rtc-accept-detection VALUE    Vorgabe für Auto-Detect (yes/no) ohne Rückfrage.
  --rtc-choice N                  Vorauswahl für das RTC-Menü (1=Auto,2=PCF8563,3=DS3231).
  --hat-model KEY                 Voreinstellung für Audio-HAT (z.B. hifiberry_dacplus, manual, skip).
  --hat-dtoverlay VALUE           dtoverlay bei manuellem HAT-Modus.
  --hat-options VALUE             Zusätzliche dtoverlay-Optionen im manuellen Modus.
  --hat-sink VALUE                PulseAudio Sink/Muster im manuellen Modus.
  --hat-disable-onboard VALUE     Deaktiviert Onboard-Audio im manuellen Modus (yes/no).
  --ap                            Access-Point-Modus ohne Rückfrage aktivieren.
  --no-ap                         Access-Point-Modus überspringen.
  --ap-ssid VALUE                 SSID für den Access Point.
  --ap-passphrase VALUE           WPA2-Passphrase (mindestens 8 Zeichen).
  --ap-channel VALUE              Funkkanal für hostapd.
  --ap-country VALUE              Ländercode (2 Buchstaben).
  --ap-interface VALUE            WLAN-Interface für den Access Point.
  --ap-ipv4 VALUE                 Statische IPv4-Adresse für den Access Point.
  --ap-prefix VALUE               IPv4-Präfix (CIDR) für die statische Adresse.
  --ap-dhcp-start VALUE           DHCP-Startadresse.
  --ap-dhcp-end VALUE             DHCP-Endadresse.
  --ap-dhcp-lease VALUE           DHCP-Lease-Zeit.
  --ap-wan VALUE                  Interface für den Internet-Uplink.
  --non-interactive               Keine Dialoge – fehlende Pflichtwerte führen zum Abbruch.
  -h, --help                      Diese Hilfe anzeigen.

Alle Optionen lassen sich alternativ über gleichnamige Umgebungsvariablen mit dem Präfix INSTALL_
(z.B. INSTALL_RTC_MODE) setzen. Einstellungen für Audio-HATs können außerdem über die bereits
unterstützten Variablen HAT_MODEL, HAT_DTOOVERLAY usw. vorgegeben werden.

Hinweis: Die Paketinstallation erfolgt unattended (DEBIAN_FRONTEND=noninteractive) über apt-get.
Über die Umgebungsvariablen INSTALL_APT_FRONTEND, INSTALL_APT_DPKG_OPTIONS und
INSTALL_APT_LOG_FILE lässt sich dieses Verhalten anpassen (siehe README).
EOF
}

require_value() {
    if [ $# -lt 2 ] || [ -z "$2" ] || [[ "$2" == --* ]]; then
        echo "Fehlender Wert für Option '$1'" >&2
        usage
        exit 1
    fi
}

APT_FRONTEND="${INSTALL_APT_FRONTEND:-noninteractive}"
APT_DPKG_OPTIONS=(--force-confdef --force-confold)
if [ "${INSTALL_APT_DPKG_OPTIONS+x}" = x ]; then
    if [ -n "$INSTALL_APT_DPKG_OPTIONS" ]; then
        # shellcheck disable=SC2206
        APT_DPKG_OPTIONS=($INSTALL_APT_DPKG_OPTIONS)
    else
        APT_DPKG_OPTIONS=()
    fi
fi
APT_LOG_FILE="${INSTALL_APT_LOG_FILE:-/tmp/audio-pi-install-apt.log}"
APT_LOG_DIR="$(dirname "$APT_LOG_FILE")"
mkdir -p "$APT_LOG_DIR"
touch "$APT_LOG_FILE"

apt_get() {
    if [ $# -lt 1 ]; then
        echo "apt_get: fehlende Aktion" >&2
        exit 1
    fi

    local action
    action="$1"
    shift

    local -a cmd
    cmd=(sudo env "DEBIAN_FRONTEND=${APT_FRONTEND}" apt-get)

    case "$action" in
        install|upgrade|dist-upgrade|full-upgrade)
            for opt in "${APT_DPKG_OPTIONS[@]}"; do
                if [ -n "$opt" ]; then
                    cmd+=(-o "Dpkg::Options::=$opt")
                fi
            done
            ;;
    esac

    cmd+=("$action" "$@")

    if ! "${cmd[@]}" 2>&1 | tee -a "$APT_LOG_FILE"; then
        echo "Fehler bei apt-get ${action}. Details siehe ${APT_LOG_FILE}" >&2
        exit 1
    fi
}

validate_chmod_mode() {
    local value="$1"
    local var_name="$2"
    if [[ ! "$value" =~ ^[0-7]{3,4}$ ]]; then
        if [ -n "$var_name" ]; then
            echo "Ungültiger Wert für ${var_name}: '$value'. Erwartet wird eine oktale chmod-Angabe (z. B. 775 oder 660)." >&2
        else
            echo "Ungültige chmod-Angabe: '$value'. Erwartet werden drei oder vier oktale Ziffern." >&2
        fi
        exit 1
    fi
}

UPLOAD_DIR_MODE="${INSTALL_UPLOAD_DIR_MODE:-775}"
LOG_FILE_MODE="${INSTALL_LOG_FILE_MODE:-660}"
validate_chmod_mode "$UPLOAD_DIR_MODE" INSTALL_UPLOAD_DIR_MODE
validate_chmod_mode "$LOG_FILE_MODE" INSTALL_LOG_FILE_MODE

ARG_FLASK_SECRET_KEY=""
ARG_RTC_MODE=""
ARG_RTC_ADDRESSES=""
ARG_RTC_OVERLAY=""
ARG_RTC_ACCEPT=""
ARG_RTC_CHOICE=""
ARG_HAT_MODEL=""
ARG_HAT_DTO=""
ARG_HAT_OPTIONS=""
ARG_HAT_SINK=""
ARG_HAT_DISABLE=""
ARG_AP_SETUP=""
ARG_AP_SSID=""
ARG_AP_PASSPHRASE=""
ARG_AP_CHANNEL=""
ARG_AP_COUNTRY=""
ARG_AP_INTERFACE=""
ARG_AP_IPV4=""
ARG_AP_PREFIX=""
ARG_AP_DHCP_START=""
ARG_AP_DHCP_END=""
ARG_AP_DHCP_LEASE=""
ARG_AP_WAN=""
FORCE_NONINTERACTIVE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --flask-secret-key)
            require_value "$1" "$2"
            ARG_FLASK_SECRET_KEY="$2"
            shift 2
            ;;
        --rtc-mode)
            require_value "$1" "$2"
            ARG_RTC_MODE="$2"
            shift 2
            ;;
        --rtc-addresses)
            require_value "$1" "$2"
            ARG_RTC_ADDRESSES="$2"
            shift 2
            ;;
        --rtc-overlay)
            require_value "$1" "$2"
            ARG_RTC_OVERLAY="$2"
            shift 2
            ;;
        --rtc-accept-detection)
            require_value "$1" "$2"
            ARG_RTC_ACCEPT="$2"
            shift 2
            ;;
        --rtc-choice)
            require_value "$1" "$2"
            ARG_RTC_CHOICE="$2"
            shift 2
            ;;
        --hat-model)
            require_value "$1" "$2"
            ARG_HAT_MODEL="$2"
            shift 2
            ;;
        --hat-dtoverlay)
            require_value "$1" "$2"
            ARG_HAT_DTO="$2"
            shift 2
            ;;
        --hat-options)
            require_value "$1" "$2"
            ARG_HAT_OPTIONS="$2"
            shift 2
            ;;
        --hat-sink)
            require_value "$1" "$2"
            ARG_HAT_SINK="$2"
            shift 2
            ;;
        --hat-disable-onboard)
            require_value "$1" "$2"
            ARG_HAT_DISABLE="$2"
            shift 2
            ;;
        --ap)
            ARG_AP_SETUP="yes"
            shift
            ;;
        --no-ap)
            ARG_AP_SETUP="no"
            shift
            ;;
        --ap-ssid)
            require_value "$1" "$2"
            ARG_AP_SSID="$2"
            shift 2
            ;;
        --ap-passphrase)
            require_value "$1" "$2"
            ARG_AP_PASSPHRASE="$2"
            shift 2
            ;;
        --ap-channel)
            require_value "$1" "$2"
            ARG_AP_CHANNEL="$2"
            shift 2
            ;;
        --ap-country)
            require_value "$1" "$2"
            ARG_AP_COUNTRY="$2"
            shift 2
            ;;
        --ap-interface)
            require_value "$1" "$2"
            ARG_AP_INTERFACE="$2"
            shift 2
            ;;
        --ap-ipv4)
            require_value "$1" "$2"
            ARG_AP_IPV4="$2"
            shift 2
            ;;
        --ap-prefix)
            require_value "$1" "$2"
            ARG_AP_PREFIX="$2"
            shift 2
            ;;
        --ap-dhcp-start)
            require_value "$1" "$2"
            ARG_AP_DHCP_START="$2"
            shift 2
            ;;
        --ap-dhcp-end)
            require_value "$1" "$2"
            ARG_AP_DHCP_END="$2"
            shift 2
            ;;
        --ap-dhcp-lease)
            require_value "$1" "$2"
            ARG_AP_DHCP_LEASE="$2"
            shift 2
            ;;
        --ap-wan)
            require_value "$1" "$2"
            ARG_AP_WAN="$2"
            shift 2
            ;;
        --non-interactive)
            FORCE_NONINTERACTIVE=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unbekannte Option: $1" >&2
            usage
            exit 1
            ;;
    esac
done

echo "---- Audio Pi Websystem Installer ----"
echo "Starte als Root/Sudo empfohlen..."
echo "Nutze ./install.sh --help für alle nicht-interaktiven Optionen."
echo "APT-Ausgaben werden in ${APT_LOG_FILE} protokolliert (anpassbar via INSTALL_APT_LOG_FILE)."

PROMPT_ALLOWED=1
if [ ! -t 0 ]; then
    PROMPT_ALLOWED=0
fi
if [ "$FORCE_NONINTERACTIVE" -eq 1 ]; then
    PROMPT_ALLOWED=0
fi

# Werte aus Umgebungsvariablen mit INSTALL_-Präfix auflösen, falls gesetzt
if [ -z "$ARG_FLASK_SECRET_KEY" ] && [ -n "${INSTALL_FLASK_SECRET_KEY:-}" ]; then
    ARG_FLASK_SECRET_KEY="$INSTALL_FLASK_SECRET_KEY"
fi
if [ -z "$ARG_FLASK_SECRET_KEY" ] && [ -n "${FLASK_SECRET_KEY:-}" ]; then
    ARG_FLASK_SECRET_KEY="$FLASK_SECRET_KEY"
fi
if [ -z "$ARG_RTC_MODE" ] && [ -n "${INSTALL_RTC_MODE:-}" ]; then
    ARG_RTC_MODE="$INSTALL_RTC_MODE"
fi
if [ -z "$ARG_RTC_MODE" ] && [ -n "${RTC_MODE:-}" ]; then
    ARG_RTC_MODE="$RTC_MODE"
fi
if [ -z "$ARG_RTC_ADDRESSES" ] && [ -n "${INSTALL_RTC_ADDRESSES:-}" ]; then
    ARG_RTC_ADDRESSES="$INSTALL_RTC_ADDRESSES"
fi
if [ -z "$ARG_RTC_ADDRESSES" ] && [ -n "${RTC_ADDRESSES:-}" ]; then
    ARG_RTC_ADDRESSES="$RTC_ADDRESSES"
fi
if [ -z "$ARG_RTC_OVERLAY" ] && [ -n "${INSTALL_RTC_OVERLAY:-}" ]; then
    ARG_RTC_OVERLAY="$INSTALL_RTC_OVERLAY"
fi
if [ -z "$ARG_RTC_OVERLAY" ] && [ -n "${RTC_OVERLAY:-}" ]; then
    ARG_RTC_OVERLAY="$RTC_OVERLAY"
fi
if [ -z "$ARG_RTC_ACCEPT" ] && [ -n "${INSTALL_RTC_ACCEPT_DETECTION:-}" ]; then
    ARG_RTC_ACCEPT="$INSTALL_RTC_ACCEPT_DETECTION"
fi
if [ -z "$ARG_RTC_ACCEPT" ] && [ -n "${RTC_ACCEPT_DETECTION:-}" ]; then
    ARG_RTC_ACCEPT="$RTC_ACCEPT_DETECTION"
fi
if [ -z "$ARG_RTC_CHOICE" ] && [ -n "${INSTALL_RTC_CHOICE:-}" ]; then
    ARG_RTC_CHOICE="$INSTALL_RTC_CHOICE"
fi
if [ -z "$ARG_RTC_CHOICE" ] && [ -n "${RTC_CHOICE:-}" ]; then
    ARG_RTC_CHOICE="$RTC_CHOICE"
fi
if [ -z "$ARG_AP_SETUP" ] && [ -n "${INSTALL_AP_SETUP:-}" ]; then
    ARG_AP_SETUP="$INSTALL_AP_SETUP"
fi
if [ -z "$ARG_AP_SETUP" ] && [ -n "${AP_SETUP:-}" ]; then
    ARG_AP_SETUP="$AP_SETUP"
fi
if [ -z "$ARG_AP_SSID" ] && [ -n "${INSTALL_AP_SSID:-}" ]; then
    ARG_AP_SSID="$INSTALL_AP_SSID"
fi
if [ -z "$ARG_AP_SSID" ] && [ -n "${AP_SSID:-}" ]; then
    ARG_AP_SSID="$AP_SSID"
fi
if [ -z "$ARG_AP_PASSPHRASE" ] && [ -n "${INSTALL_AP_PASSPHRASE:-}" ]; then
    ARG_AP_PASSPHRASE="$INSTALL_AP_PASSPHRASE"
fi
if [ -z "$ARG_AP_PASSPHRASE" ] && [ -n "${AP_PASSPHRASE:-}" ]; then
    ARG_AP_PASSPHRASE="$AP_PASSPHRASE"
fi
if [ -z "$ARG_AP_CHANNEL" ] && [ -n "${INSTALL_AP_CHANNEL:-}" ]; then
    ARG_AP_CHANNEL="$INSTALL_AP_CHANNEL"
fi
if [ -z "$ARG_AP_CHANNEL" ] && [ -n "${AP_CHANNEL:-}" ]; then
    ARG_AP_CHANNEL="$AP_CHANNEL"
fi
if [ -z "$ARG_AP_COUNTRY" ] && [ -n "${INSTALL_AP_COUNTRY:-}" ]; then
    ARG_AP_COUNTRY="$INSTALL_AP_COUNTRY"
fi
if [ -z "$ARG_AP_COUNTRY" ] && [ -n "${AP_COUNTRY:-}" ]; then
    ARG_AP_COUNTRY="$AP_COUNTRY"
fi
if [ -z "$ARG_AP_INTERFACE" ] && [ -n "${INSTALL_AP_INTERFACE:-}" ]; then
    ARG_AP_INTERFACE="$INSTALL_AP_INTERFACE"
fi
if [ -z "$ARG_AP_INTERFACE" ] && [ -n "${AP_INTERFACE:-}" ]; then
    ARG_AP_INTERFACE="$AP_INTERFACE"
fi
if [ -z "$ARG_AP_IPV4" ] && [ -n "${INSTALL_AP_IPV4:-}" ]; then
    ARG_AP_IPV4="$INSTALL_AP_IPV4"
fi
if [ -z "$ARG_AP_IPV4" ] && [ -n "${AP_IPV4:-}" ]; then
    ARG_AP_IPV4="$AP_IPV4"
fi
if [ -z "$ARG_AP_PREFIX" ] && [ -n "${INSTALL_AP_PREFIX:-}" ]; then
    ARG_AP_PREFIX="$INSTALL_AP_PREFIX"
fi
if [ -z "$ARG_AP_PREFIX" ] && [ -n "${AP_PREFIX:-}" ]; then
    ARG_AP_PREFIX="$AP_PREFIX"
fi
if [ -z "$ARG_AP_DHCP_START" ] && [ -n "${INSTALL_AP_DHCP_START:-}" ]; then
    ARG_AP_DHCP_START="$INSTALL_AP_DHCP_START"
fi
if [ -z "$ARG_AP_DHCP_START" ] && [ -n "${AP_DHCP_START:-}" ]; then
    ARG_AP_DHCP_START="$AP_DHCP_START"
fi
if [ -z "$ARG_AP_DHCP_END" ] && [ -n "${INSTALL_AP_DHCP_END:-}" ]; then
    ARG_AP_DHCP_END="$INSTALL_AP_DHCP_END"
fi
if [ -z "$ARG_AP_DHCP_END" ] && [ -n "${AP_DHCP_END:-}" ]; then
    ARG_AP_DHCP_END="$AP_DHCP_END"
fi
if [ -z "$ARG_AP_DHCP_LEASE" ] && [ -n "${INSTALL_AP_DHCP_LEASE:-}" ]; then
    ARG_AP_DHCP_LEASE="$INSTALL_AP_DHCP_LEASE"
fi
if [ -z "$ARG_AP_DHCP_LEASE" ] && [ -n "${AP_DHCP_LEASE:-}" ]; then
    ARG_AP_DHCP_LEASE="$AP_DHCP_LEASE"
fi
if [ -z "$ARG_AP_WAN" ] && [ -n "${INSTALL_AP_WAN:-}" ]; then
    ARG_AP_WAN="$INSTALL_AP_WAN"
fi
if [ -z "$ARG_AP_WAN" ] && [ -n "${AP_WAN:-}" ]; then
    ARG_AP_WAN="$AP_WAN"
fi

if [ -n "$ARG_AP_SSID" ]; then
    HOSTAPD_SSID="$ARG_AP_SSID"
fi
if [ -n "$ARG_AP_PASSPHRASE" ]; then
    HOSTAPD_PASSPHRASE="$ARG_AP_PASSPHRASE"
fi
if [ -n "$ARG_AP_CHANNEL" ]; then
    HOSTAPD_CHANNEL="$ARG_AP_CHANNEL"
fi
if [ -n "$ARG_AP_COUNTRY" ]; then
    HOSTAPD_COUNTRY="$ARG_AP_COUNTRY"
fi
if [ -n "$ARG_AP_INTERFACE" ]; then
    AP_INTERFACE="$ARG_AP_INTERFACE"
fi
if [ -n "$ARG_AP_IPV4" ]; then
    AP_IPV4="$ARG_AP_IPV4"
fi
if [ -n "$ARG_AP_PREFIX" ]; then
    AP_IPV4_PREFIX="$ARG_AP_PREFIX"
fi
if [ -n "$ARG_AP_DHCP_START" ]; then
    DHCP_RANGE_START="$ARG_AP_DHCP_START"
fi
if [ -n "$ARG_AP_DHCP_END" ]; then
    DHCP_RANGE_END="$ARG_AP_DHCP_END"
fi
if [ -n "$ARG_AP_DHCP_LEASE" ]; then
    DHCP_LEASE_TIME="$ARG_AP_DHCP_LEASE"
fi
if [ -n "$ARG_AP_WAN" ]; then
    WAN_INTERFACE="$ARG_AP_WAN"
fi

if [ -z "$ARG_HAT_MODEL" ] && [ -n "${INSTALL_HAT_MODEL:-}" ]; then
    ARG_HAT_MODEL="$INSTALL_HAT_MODEL"
fi
if [ -z "$ARG_HAT_MODEL" ] && [ -n "${HAT_MODEL:-}" ]; then
    ARG_HAT_MODEL="$HAT_MODEL"
fi
if [ -z "$ARG_HAT_DTO" ] && [ -n "${INSTALL_HAT_DTOOVERLAY:-}" ]; then
    ARG_HAT_DTO="$INSTALL_HAT_DTOOVERLAY"
fi
if [ -z "$ARG_HAT_DTO" ] && [ -n "${HAT_DTOOVERLAY:-}" ]; then
    ARG_HAT_DTO="$HAT_DTOOVERLAY"
fi
if [ -z "$ARG_HAT_OPTIONS" ] && [ -n "${INSTALL_HAT_OPTIONS:-}" ]; then
    ARG_HAT_OPTIONS="$INSTALL_HAT_OPTIONS"
fi
if [ -z "$ARG_HAT_OPTIONS" ] && [ -n "${HAT_OPTIONS:-}" ]; then
    ARG_HAT_OPTIONS="$HAT_OPTIONS"
fi
if [ -z "$ARG_HAT_SINK" ] && [ -n "${INSTALL_HAT_SINK:-}" ]; then
    ARG_HAT_SINK="$INSTALL_HAT_SINK"
fi
if [ -z "$ARG_HAT_SINK" ] && [ -n "${HAT_SINK_NAME:-}" ]; then
    ARG_HAT_SINK="$HAT_SINK_NAME"
fi
if [ -z "$ARG_HAT_DISABLE" ] && [ -n "${INSTALL_HAT_DISABLE_ONBOARD:-}" ]; then
    ARG_HAT_DISABLE="$INSTALL_HAT_DISABLE_ONBOARD"
fi
if [ -z "$ARG_HAT_DISABLE" ] && [ -n "${HAT_DISABLE_ONBOARD_AUDIO:-}" ]; then
    ARG_HAT_DISABLE="$HAT_DISABLE_ONBOARD_AUDIO"
fi

if [ -n "$ARG_HAT_MODEL" ]; then
    export HAT_MODEL="$ARG_HAT_MODEL"
fi
if [ -n "$ARG_HAT_DTO" ]; then
    export HAT_DTOOVERLAY="$ARG_HAT_DTO"
fi
if [ -n "$ARG_HAT_OPTIONS" ]; then
    export HAT_OPTIONS="$ARG_HAT_OPTIONS"
fi
if [ -n "$ARG_HAT_SINK" ]; then
    export HAT_SINK_NAME="$ARG_HAT_SINK"
fi
if [ -n "$ARG_HAT_DISABLE" ]; then
    export HAT_DISABLE_ONBOARD_AUDIO="$ARG_HAT_DISABLE"
fi
if [ "$PROMPT_ALLOWED" -eq 0 ] && [ -z "${HAT_MODEL:-}" ]; then
    export HAT_NONINTERACTIVE=1
fi

# System-Update
apt_get update
apt_get upgrade -y

# Python-Basics & PIP
apt_get install -y python3 python3-pip python3-venv sqlite3

# Virtuelle Umgebung einrichten
python3 -m venv venv
source venv/bin/activate

# Dev-Packages (für pydub/pygame etc.)
apt_get install -y libasound2-dev libpulse-dev libportaudio2 ffmpeg libffi-dev libjpeg-dev libbluetooth-dev

# Python-Abhängigkeiten installieren
pip install -r requirements.txt

# Benutzer nach Secret fragen und in Profil speichern
SECRET="$ARG_FLASK_SECRET_KEY"
if [ -z "$SECRET" ] && [ -n "${FLASK_SECRET_KEY:-}" ]; then
    SECRET="$FLASK_SECRET_KEY"
fi
while [ -z "$SECRET" ]; do
    if [ "$PROMPT_ALLOWED" -eq 0 ]; then
        echo "Fehler: FLASK_SECRET_KEY muss via --flask-secret-key oder INSTALL_FLASK_SECRET_KEY gesetzt werden." >&2
        exit 1
    fi
    IFS= read -r -p "FLASK_SECRET_KEY (darf nicht leer sein): " SECRET
done
TARGET_USER=${SUDO_USER:-$USER}
TARGET_UID=$(id -u "$TARGET_USER")
TARGET_GROUP=$(id -gn "$TARGET_USER")
TARGET_HOME=$(eval echo "~$TARGET_USER")
PROFILE_FILE="$TARGET_HOME/.profile"
PROFILE_EXPORT_LINE=$(printf 'export FLASK_SECRET_KEY=%q' "$SECRET")
if sudo test -f "$PROFILE_FILE" && sudo grep -q '^export FLASK_SECRET_KEY=' "$PROFILE_FILE"; then
    sudo env PROFILE_REPLACEMENT="$PROFILE_EXPORT_LINE" perl -0pi -e 's/^export FLASK_SECRET_KEY=.*$/\Q$ENV{PROFILE_REPLACEMENT}\E/m' "$PROFILE_FILE"
    echo "FLASK_SECRET_KEY wurde in $TARGET_HOME/.profile aktualisiert."
else
    printf '%s\n' "$PROFILE_EXPORT_LINE" | sudo tee -a "$PROFILE_FILE"
    echo "FLASK_SECRET_KEY wurde in $TARGET_HOME/.profile hinterlegt."
fi
SYSTEMD_QUOTED_SECRET=$(printf '%s' "$SECRET" | sed -e 's/[\\$"]/\\&/g')
SYSTEMD_SED_SAFE_SECRET=$(printf '%s' "$SYSTEMD_QUOTED_SECRET" | sed -e 's/[&|]/\\&/g')

# I²C für RTC aktivieren
sudo raspi-config nonint do_i2c 0
if sudo grep -q '^i2c-dev$' /etc/modules; then
    echo "i2c-dev ist bereits in /etc/modules eingetragen – überspringe."
else
    echo "Füge i2c-dev zu /etc/modules hinzu."
    printf 'i2c-dev\n' | sudo tee -a /etc/modules >/dev/null
fi

# Werkzeuge für die automatische RTC-Erkennung bereitstellen
apt_get install -y i2c-tools

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
RTC_AUTODETECT_AVAILABLE=0
RTC_MODE_INPUT=""
if [ -n "$ARG_RTC_MODE" ]; then
    RTC_MODE_INPUT=$(printf '%s' "$ARG_RTC_MODE" | tr 'A-Z' 'a-z')
fi

if detect_rtc_devices; then
    RTC_AUTODETECT_AVAILABLE=1
    RTC_DETECTED_ADDRESS_STRING=$(format_detected_addresses)
    echo "Automatische Erkennung: Gefundene I²C-Adresse(n) auf Bus ${RTC_DETECTED_BUS}: $RTC_DETECTED_ADDRESS_STRING"
    if infer_rtc_from_addresses; then
        echo "Vermutetes RTC-Modul: $RTC_AUTODETECT_LABEL"
    else
        echo "Hinweis: Gefundene Adresse(n) konnten keinem bekannten RTC-Typ eindeutig zugeordnet werden."
    fi
fi

if [ -n "$RTC_MODE_INPUT" ]; then
    case "$RTC_MODE_INPUT" in
        auto)
            if [ "$RTC_AUTODETECT_AVAILABLE" -eq 1 ] && [ -n "$RTC_AUTODETECT_MODULE" ]; then
                RTC_MODULE="$RTC_AUTODETECT_MODULE"
                RTC_OVERLAY_DEFAULT="$RTC_AUTODETECT_OVERLAY"
                if [ -z "$ARG_RTC_ADDRESSES" ] && [ -n "$RTC_DETECTED_ADDRESS_STRING" ]; then
                    RTC_ADDRESS_INPUT="$RTC_DETECTED_ADDRESS_STRING"
                fi
                RTC_AUTODETECT_ACCEPTED=1
            else
                RTC_MODULE="auto"
                RTC_OVERLAY_DEFAULT="pcf8563"
            fi
            ;;
        pcf8563)
            RTC_MODULE="pcf8563"
            RTC_OVERLAY_DEFAULT="pcf8563"
            ;;
        ds3231)
            RTC_MODULE="ds3231"
            RTC_OVERLAY_DEFAULT="ds3231"
            ;;
        skip|none)
            RTC_MODULE="auto"
            RTC_OVERLAY_DEFAULT=""
            ;;
        *)
            echo "Fehler: Unbekannter RTC-Modus '$ARG_RTC_MODE'. Erlaubt sind auto, pcf8563, ds3231 oder skip." >&2
            exit 1
            ;;
    esac
    if [ -n "$ARG_RTC_ADDRESSES" ]; then
        RTC_ADDRESS_INPUT="$ARG_RTC_ADDRESSES"
    fi
else
    if [ "$RTC_AUTODETECT_AVAILABLE" -eq 1 ]; then
        AUTODETECT_DECISION="${ARG_RTC_ACCEPT,,}"
        if [ -z "$AUTODETECT_DECISION" ] && [ "$PROMPT_ALLOWED" -eq 0 ]; then
            AUTODETECT_DECISION="yes"
        fi
        case "$AUTODETECT_DECISION" in
            yes|y|ja|j|true|1)
                RTC_AUTODETECT_ACCEPTED=1
                ;;
            no|n|nein|false|0)
                RTC_AUTODETECT_ACCEPTED=0
                ;;
            "")
                if [ "$PROMPT_ALLOWED" -eq 1 ]; then
                    read -rp "Automatische Erkennung übernehmen? [J/n]: " RTC_AUTODETECT_CONFIRM
                    RTC_AUTODETECT_CONFIRM=${RTC_AUTODETECT_CONFIRM,,}
                    if [ "$RTC_AUTODETECT_CONFIRM" != "n" ] && [ "$RTC_AUTODETECT_CONFIRM" != "nein" ]; then
                        RTC_AUTODETECT_ACCEPTED=1
                    fi
                else
                    RTC_AUTODETECT_ACCEPTED=1
                fi
                ;;
            *)
                echo "Fehler: Ungültiger Wert für --rtc-accept-detection: '$AUTODETECT_DECISION'" >&2
                exit 1
                ;;
        esac
        if [ "$RTC_AUTODETECT_ACCEPTED" -eq 1 ]; then
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
        if [ -n "$ARG_RTC_CHOICE" ]; then
            RTC_CHOICE="$ARG_RTC_CHOICE"
        elif [ "$PROMPT_ALLOWED" -eq 1 ]; then
            read -rp "Auswahl [1-3]: " RTC_CHOICE
        else
            RTC_CHOICE="1"
        fi

        case "$RTC_CHOICE" in
            2)
                RTC_MODULE="pcf8563"
                RTC_OVERLAY_DEFAULT="pcf8563"
                ;;
            3)
                RTC_MODULE="ds3231"
                RTC_OVERLAY_DEFAULT="ds3231"
                ;;
            1|"" )
                RTC_MODULE="auto"
                RTC_OVERLAY_DEFAULT="pcf8563"
                ;;
            *)
                echo "Fehler: Ungültige RTC-Auswahl '$RTC_CHOICE'." >&2
                exit 1
                ;;
        esac

        if [ -n "$ARG_RTC_ADDRESSES" ]; then
            RTC_ADDRESS_INPUT="$ARG_RTC_ADDRESSES"
        elif [ "$PROMPT_ALLOWED" -eq 1 ]; then
            read -rp "Eigene I²C-Adressen (optional, Kommagetrennt, z.B. 0x51,0x68): " RTC_ADDRESS_INPUT
        else
            RTC_ADDRESS_INPUT=""
        fi
    else
        if [ -n "$ARG_RTC_ADDRESSES" ]; then
            RTC_ADDRESS_INPUT="$ARG_RTC_ADDRESSES"
        elif [ "$PROMPT_ALLOWED" -eq 1 ] && [ -n "$RTC_ADDRESS_INPUT" ]; then
            read -rp "Eigene I²C-Adressen (Enter übernimmt '$RTC_ADDRESS_INPUT'): " RTC_ADDRESS_OVERRIDE
            if [ -n "$RTC_ADDRESS_OVERRIDE" ]; then
                RTC_ADDRESS_INPUT="$RTC_ADDRESS_OVERRIDE"
            fi
        elif [ "$PROMPT_ALLOWED" -eq 1 ]; then
            read -rp "Eigene I²C-Adressen (optional, Kommagetrennt, z.B. 0x51,0x68): " RTC_ADDRESS_INPUT
        fi
    fi
fi

if [ -n "$ARG_RTC_OVERLAY" ]; then
    RTC_OVERLAY_INPUT="$ARG_RTC_OVERLAY"
elif [ "$PROMPT_ALLOWED" -eq 1 ]; then
    read -rp "dtoverlay für RTC (leer für '$RTC_OVERLAY_DEFAULT', '-' zum Überspringen): " RTC_OVERLAY_INPUT
else
    RTC_OVERLAY_INPUT="$RTC_OVERLAY_DEFAULT"
fi

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
apt_get install -y pulseaudio pulseaudio-utils
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
echo "Nicht-interaktiv: nutze z.B. --hat-model=hifiberry_dacplus oder --hat-model=manual mit --hat-dtoverlay/--hat-sink."
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
apt_get install -y pulseaudio-module-bluetooth bluez-tools bluez

# Hostapd & dnsmasq (WLAN AP)
apt_get install -y hostapd dnsmasq wireless-tools iw wpasupplicant

create_hostapd_conf() {
    local default_ssid="AudioPiAP"
    local default_channel="6"
    local default_country="DE"
    local default_interface="wlan0"

    if [ -z "${HOSTAPD_SSID:-}" ]; then
        if [ "$PROMPT_ALLOWED" -eq 1 ]; then
            read -rp "Access-Point-SSID [${default_ssid}]: " HOSTAPD_SSID
        fi
        HOSTAPD_SSID=${HOSTAPD_SSID:-$default_ssid}
    fi

    if [ -z "${HOSTAPD_PASSPHRASE:-}" ]; then
        if [ -n "$ARG_AP_PASSPHRASE" ]; then
            HOSTAPD_PASSPHRASE="$ARG_AP_PASSPHRASE"
        elif [ "$PROMPT_ALLOWED" -eq 1 ]; then
            while [ ${#HOSTAPD_PASSPHRASE} -lt 8 ]; do
                read -rsp "WPA2-Passphrase (mind. 8 Zeichen): " HOSTAPD_PASSPHRASE
                echo ""
                if [ ${#HOSTAPD_PASSPHRASE} -lt 8 ]; then
                    echo "Passphrase zu kurz, bitte erneut eingeben."
                fi
            done
        else
            echo "Fehler: Für den Access Point muss eine WPA2-Passphrase mit mindestens 8 Zeichen gesetzt werden." >&2
            echo "Verwende --ap-passphrase oder INSTALL_AP_PASSPHRASE." >&2
            exit 1
        fi
    fi
    if [ ${#HOSTAPD_PASSPHRASE} -lt 8 ]; then
        echo "Fehler: Die vorgegebene WPA2-Passphrase ist kürzer als 8 Zeichen." >&2
        exit 1
    fi

    if [ -z "${HOSTAPD_CHANNEL:-}" ]; then
        if [ "$PROMPT_ALLOWED" -eq 1 ]; then
            read -rp "Funkkanal [${default_channel}]: " HOSTAPD_CHANNEL
        fi
        HOSTAPD_CHANNEL=${HOSTAPD_CHANNEL:-$default_channel}
    fi

    if [ -z "${HOSTAPD_COUNTRY:-}" ]; then
        if [ "$PROMPT_ALLOWED" -eq 1 ]; then
            read -rp "Ländercode (2-stellig) [${default_country}]: " HOSTAPD_COUNTRY
        fi
        HOSTAPD_COUNTRY=${HOSTAPD_COUNTRY:-$default_country}
    fi

    if [ -z "${AP_INTERFACE:-}" ]; then
        if [ "$PROMPT_ALLOWED" -eq 1 ]; then
            read -rp "WLAN-Interface für den AP [${default_interface}]: " AP_INTERFACE
        fi
        AP_INTERFACE=${AP_INTERFACE:-$default_interface}
    fi

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
    local default_ap_prefix="24"
    local default_dhcp_start="192.168.50.50"
    local default_dhcp_end="192.168.50.150"
    local default_dhcp_time="24h"

    if [ -z "${AP_IPV4:-}" ]; then
        if [ "$PROMPT_ALLOWED" -eq 1 ]; then
            read -rp "Statische AP-IP [${default_ap_ip}]: " AP_IPV4
        fi
        AP_IPV4=${AP_IPV4:-$default_ap_ip}
    fi

    if [ -z "${AP_IPV4_PREFIX:-}" ]; then
        if [ "$PROMPT_ALLOWED" -eq 1 ]; then
            read -rp "AP-Subnetz-Präfix (CIDR, z.B. 24) [${default_ap_prefix}]: " AP_IPV4_PREFIX
        fi
        AP_IPV4_PREFIX=${AP_IPV4_PREFIX:-$default_ap_prefix}
    fi
    AP_IPV4_PREFIX=${AP_IPV4_PREFIX//[^0-9]/}
    if [ -z "$AP_IPV4_PREFIX" ]; then
        AP_IPV4_PREFIX=$default_ap_prefix
    fi
    if ! [[ $AP_IPV4_PREFIX =~ ^([0-9]|[1-2][0-9]|3[0-2])$ ]]; then
        echo "Warnung: Ungültiger Präfix '$AP_IPV4_PREFIX', verwende ${default_ap_prefix}."
        AP_IPV4_PREFIX=$default_ap_prefix
    fi

    if [ -z "${DHCP_RANGE_START:-}" ]; then
        if [ "$PROMPT_ALLOWED" -eq 1 ]; then
            read -rp "DHCP-Startadresse [${default_dhcp_start}]: " DHCP_RANGE_START
        fi
        DHCP_RANGE_START=${DHCP_RANGE_START:-$default_dhcp_start}
    fi

    if [ -z "${DHCP_RANGE_END:-}" ]; then
        if [ "$PROMPT_ALLOWED" -eq 1 ]; then
            read -rp "DHCP-Endadresse [${default_dhcp_end}]: " DHCP_RANGE_END
        fi
        DHCP_RANGE_END=${DHCP_RANGE_END:-$default_dhcp_end}
    fi

    if [ -z "${DHCP_LEASE_TIME:-}" ]; then
        if [ "$PROMPT_ALLOWED" -eq 1 ]; then
            read -rp "DHCP-Lease-Zeit [${default_dhcp_time}]: " DHCP_LEASE_TIME
        fi
        DHCP_LEASE_TIME=${DHCP_LEASE_TIME:-$default_dhcp_time}
    fi

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
    if [ -z "${WAN_INTERFACE:-}" ]; then
        if [ "$PROMPT_ALLOWED" -eq 1 ]; then
            read -rp "Uplink-Interface für NAT (z.B. eth0) [${default_wan_interface}]: " WAN_INTERFACE
        fi
        WAN_INTERFACE=${WAN_INTERFACE:-$default_wan_interface}
    fi

    echo "Aktiviere WLAN-Sendeeinheit..."
    sudo rfkill unblock wlan || true

    local cidr_suffix="${AP_IPV4_PREFIX:-24}"
    if ! [[ $cidr_suffix =~ ^([0-9]|[1-2][0-9]|3[0-2])$ ]]; then
        echo "Warnung: Ungültiger Präfix '$cidr_suffix', verwende 24."
        cidr_suffix="24"
    fi
    if [ -n "$AP_IPV4" ]; then
        echo "Setze statische IP ${AP_IPV4}/${cidr_suffix} auf ${AP_INTERFACE}..."
        sudo ip addr replace "${AP_IPV4}/${cidr_suffix}" dev "$AP_INTERFACE"
        sudo ip link set "$AP_INTERFACE" up

        echo "Aktualisiere /etc/dhcpcd.conf für die AP-Schnittstelle..."
        local dhcpcd_conf="/etc/dhcpcd.conf"
        local timestamp
        timestamp=$(date +%s)
        if [ -f "$dhcpcd_conf" ]; then
            local dhcpcd_backup="${dhcpcd_conf}.bak.${timestamp}"
            sudo cp "$dhcpcd_conf" "$dhcpcd_backup"
            echo "Backup erstellt: ${dhcpcd_backup}"
        else
            echo "Hinweis: ${dhcpcd_conf} existierte nicht und wird neu angelegt."
            sudo touch "$dhcpcd_conf"
        fi

        local tmp_file
        tmp_file=$(mktemp)
        sudo awk '
            BEGIN { skip=0 }
            /^# Audio-Pi Access Point configuration$/ { skip=1; next }
            /^# Audio-Pi Access Point configuration end$/ { skip=0; next }
            skip { next }
            { print }
        ' "$dhcpcd_conf" > "$tmp_file"
        sudo mv "$tmp_file" "$dhcpcd_conf"
        sudo chmod 644 "$dhcpcd_conf"
        sudo tee -a "$dhcpcd_conf" >/dev/null <<EOF
# Audio-Pi Access Point configuration
interface ${AP_INTERFACE}
static ip_address=${AP_IPV4}/${cidr_suffix}
nohook wpa_supplicant
# Audio-Pi Access Point configuration end
EOF
        echo "Statische Adresse ${AP_IPV4}/${cidr_suffix} dauerhaft in ${dhcpcd_conf} eingetragen."
    fi

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
AP_CONFIRM_INPUT=""
if [ -n "$ARG_AP_SETUP" ]; then
    AP_CONFIRM_INPUT=$(printf '%s' "$ARG_AP_SETUP" | tr 'A-Z' 'a-z')
fi
if [ -z "$AP_CONFIRM_INPUT" ]; then
    if [ "$PROMPT_ALLOWED" -eq 1 ]; then
        read -rp "Soll der Access-Point-Modus eingerichtet werden? [j/N]: " AP_CONFIRM_INPUT
        AP_CONFIRM_INPUT=${AP_CONFIRM_INPUT,,}
    else
        AP_CONFIRM_INPUT="n"
    fi
fi

if [[ "$AP_CONFIRM_INPUT" =~ ^(j|ja|y|yes|1|true)$ ]]; then
    create_hostapd_conf
    ensure_hostapd_daemon_conf
    create_dnsmasq_conf
    configure_ap_networking
    if sudo systemctl unmask hostapd; then
        echo "hostapd-Service erfolgreich entmaskiert."
    fi
    if sudo systemctl unmask dnsmasq; then
        echo "dnsmasq-Service erfolgreich entmaskiert."
    fi
    sudo systemctl enable --now hostapd
    sudo systemctl enable --now dnsmasq
    AP_CONFIGURED=1
else
    echo "Access-Point-Konfiguration wurde übersprungen."
fi

# ALSA für Mixer/Fallback
apt_get install -y alsa-utils

# Upload-Verzeichnis anlegen
mkdir -p uploads
sudo chown "$TARGET_USER:$TARGET_GROUP" uploads
sudo chmod "$UPLOAD_DIR_MODE" uploads

# Logfile anlegen
touch app.log
sudo chown "$TARGET_USER:$TARGET_GROUP" app.log
sudo chmod "$LOG_FILE_MODE" app.log

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
SYSTEMD_SAFE_PWD=$(printf '%s' "$(pwd)" | sed -e 's/[\\&|]/\\&/g')
sudo sed -i "s|/opt/Audio-Pi-Websystem|$SYSTEMD_SAFE_PWD|g" /etc/systemd/system/audio-pi.service
sudo sed -i "s|^Environment=.*FLASK_SECRET_KEY=.*|Environment=\"FLASK_SECRET_KEY=$SYSTEMD_SED_SAFE_SECRET\"|" /etc/systemd/system/audio-pi.service
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
echo "Setup abgeschlossen! Der systemd-Dienst 'audio-pi.service' wurde gestartet."
echo "Status prüfen: sudo systemctl status audio-pi.service"
echo ""
echo "Optionaler Entwicklungsstart:"
echo "source venv/bin/activate && export AUDIO_PI_USE_DEV_SERVER=1 && python app.py"
echo ""
echo "Öffne im Browser: http://<RaspberryPi-IP>/"
echo ""
echo "Beim ersten Start wird der Benutzer 'admin' automatisch angelegt."
echo "Setze optional INITIAL_ADMIN_PASSWORD, um das Startpasswort festzulegen."
echo "Ohne Vorgabe erzeugt die App ein zufälliges Passwort und schreibt es in $(pwd)/app.log."
echo "Bitte direkt nach der ersten Anmeldung über die Weboberfläche das Passwort ändern."
echo ""
if [ "$AP_CONFIGURED" -eq 1 ]; then
    echo "Hinweis: WLAN-Access-Point ist aktiv."
    echo "Passe SSID/Passwort in /etc/hostapd/hostapd.conf sowie DHCP-Einstellungen in /etc/dnsmasq.d/audio-pi.conf an."
    echo "Die NAT-Regeln wurden nach /etc/iptables.ipv4.nat geschrieben und können dort angepasst werden."
fi

echo ""
