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
  --generate-secret               Stellt automatisch einen starken Secret Key bereit (alternativ INSTALL_GENERATE_SECRET=1).
  --flask-port VALUE              HTTP-Port für Gunicorn/Flask (Standard: 80; alternativ INSTALL_FLASK_PORT).
  --log-file-mode MODE            chmod-Modus für app.log (Standard: 666; alternativ INSTALL_LOG_FILE_MODE).
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
  --dry-run                       Führt keine Änderungen durch und zeigt nur die Abschluss-Hinweise an.
  -h, --help                      Diese Hilfe anzeigen.

Alle Optionen lassen sich alternativ über gleichnamige Umgebungsvariablen mit dem Präfix INSTALL_
(z.B. INSTALL_RTC_MODE) setzen. Einstellungen für Audio-HATs können außerdem über die bereits
unterstützten Variablen HAT_MODEL, HAT_DTOOVERLAY usw. vorgegeben werden.

Sudo/Polkit-Verhalten:
  INSTALL_DISABLE_SUDO=1          Standard (Polkit-Regel aktiv, `sudo`-Aufrufe entfallen).
  INSTALL_DISABLE_SUDO=0          Opt-out: `sudo`-Aufrufe beibehalten (nur falls Polkit/Caps nicht verfügbar sind).

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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLKIT_RULE_TEMPLATE="$SCRIPT_DIR/scripts/polkit/49-audio-pi.rules"
POLKIT_RULE_TARGET="/etc/polkit-1/rules.d/49-audio-pi.rules"
AUDIO_PI_ALSACTL_UNIT_TEMPLATE="$SCRIPT_DIR/scripts/systemd/audio-pi-alsactl.service"
AUDIO_PI_ALSACTL_UNIT_TARGET="/etc/systemd/system/audio-pi-alsactl.service"

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

validate_port() {
    local value="$1"
    local source="$2"
    if ! [[ "$value" =~ ^[0-9]+$ ]]; then
        if [ -n "$source" ]; then
            echo "Ungültiger Portwert (${source}): '$value'. Erlaubt sind Ganzzahlen zwischen 1 und 65535." >&2
        else
            echo "Ungültiger Portwert: '$value'. Erlaubt sind Ganzzahlen zwischen 1 und 65535." >&2
        fi
        exit 1
    fi
    if [ "$value" -lt 1 ] || [ "$value" -gt 65535 ]; then
        if [ -n "$source" ]; then
            echo "Port außerhalb des gültigen Bereichs (${source}): '$value'. Erlaubt sind 1 bis 65535." >&2
        else
            echo "Port außerhalb des gültigen Bereichs: '$value'. Erlaubt sind 1 bis 65535." >&2
        fi
        exit 1
    fi
}

validate_secret_strength() {
    local secret="$1"
    local source="$2"

    if [ -z "$secret" ]; then
        if [ -n "$source" ]; then
            echo "Ungültiger Secret-Key (${source}): Wert darf nicht leer sein." >&2
        else
            echo "Ungültiger Secret-Key: Wert darf nicht leer sein." >&2
        fi
        return 1
    fi

    local length=${#secret}
    if [ "$length" -lt 32 ]; then
        if [ -n "$source" ]; then
            echo "Ungültiger Secret-Key (${source}): ${length} Zeichen sind zu wenig – mindestens 32 Zeichen erforderlich." >&2
        else
            echo "Ungültiger Secret-Key: ${length} Zeichen sind zu wenig – mindestens 32 Zeichen erforderlich." >&2
        fi
        return 1
    fi

    local classes=0
    [[ "$secret" =~ [[:lower:]] ]] && classes=$((classes + 1))
    [[ "$secret" =~ [[:upper:]] ]] && classes=$((classes + 1))
    [[ "$secret" =~ [[:digit:]] ]] && classes=$((classes + 1))
    [[ "$secret" =~ [^[:alnum:]] ]] && classes=$((classes + 1))

    if [ "$classes" -lt 3 ]; then
        local message="Ungültiger Secret-Key"
        if [ -n "$source" ]; then
            message+=" (${source})"
        fi
        message+=": Mindestens drei Zeichengruppen (Großbuchstaben, Kleinbuchstaben, Ziffern, Sonderzeichen) werden benötigt."
        echo "$message" >&2
        return 1
    fi

    return 0
}

generate_secret_value() {
    if ! command -v python3 >/dev/null 2>&1; then
        echo "Fehler: python3 wird zum Generieren eines Secret-Keys benötigt." >&2
        return 1
    fi

    python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
}

install_tmpfiles_rule() {
    local tmpfiles_conf="/etc/tmpfiles.d/audio-pi.conf"
    local runtime_user="$1"
    local runtime_group="$2"
    local runtime_uid="$3"

    if [ -z "$runtime_user" ] || [ -z "$runtime_group" ] || [ -z "$runtime_uid" ]; then
        echo "install_tmpfiles_rule: Benutzer, Gruppe oder UID fehlen." >&2
        return 1
    fi

    sudo install -d -m 0755 /etc/tmpfiles.d
    sudo tee "$tmpfiles_conf" >/dev/null <<EOF_CONF
# Audio-Pi Websystem: Laufzeitverzeichnisse für systemd und PulseAudio
d /run/audio-pi 0700 ${runtime_user} ${runtime_group} -
d /run/user/${runtime_uid} 0700 ${runtime_user} ${runtime_group} -
EOF_CONF

    sudo systemd-tmpfiles --create "$tmpfiles_conf"
    echo "Tmpfiles-Regel in $tmpfiles_conf angelegt und angewendet."
}

print_post_install_instructions() {
    local flask_port="$1"
    local ap_configured="$2"

    echo ""
    echo "Setup abgeschlossen! Der systemd-Dienst 'audio-pi.service' wurde gestartet."
    echo "Status prüfen: sudo systemctl status audio-pi.service"
    echo ""
    echo "Optionaler Entwicklungsstart:"
    echo "source venv/bin/activate && export AUDIO_PI_USE_DEV_SERVER=1 && python app.py"
    echo ""
    if [ "$flask_port" = "80" ]; then
        echo "Öffne im Browser: http://<RaspberryPi-IP>/"
    else
        echo "Öffne im Browser: http://<RaspberryPi-IP>:${flask_port}/"
    fi
    echo ""
    echo "Beim ersten Start wird der Benutzer 'admin' automatisch angelegt."
    echo "Setze optional INITIAL_ADMIN_PASSWORD, um das Startpasswort festzulegen."
    echo "Ohne Vorgabe erzeugt die App ein zufälliges Passwort und speichert es als initial_admin_password.txt neben der Datenbank (0600-Rechte)."
    echo "Bitte direkt nach der ersten Anmeldung über die Weboberfläche das Passwort ändern."
    echo ""
    if [ "$ap_configured" -eq 1 ]; then
        echo "Hinweis: WLAN-Access-Point ist aktiv."
        echo "Passe SSID/Passwort in /etc/hostapd/hostapd.conf sowie DHCP-Einstellungen in /etc/dnsmasq.d/audio-pi.conf an."
        echo "Die NAT-Regeln wurden nach /etc/iptables.ipv4.nat geschrieben und können dort angepasst werden."
        echo ""
    fi
    echo ""
}

UPLOAD_DIR_MODE="${INSTALL_UPLOAD_DIR_MODE:-775}"
DEFAULT_LOG_FILE_MODE="${INSTALL_LOG_FILE_MODE:-666}"
validate_chmod_mode "$UPLOAD_DIR_MODE" INSTALL_UPLOAD_DIR_MODE
validate_chmod_mode "$DEFAULT_LOG_FILE_MODE" INSTALL_LOG_FILE_MODE

ARG_FLASK_SECRET_KEY=""
ARG_FLASK_SECRET_SOURCE=""
ARG_FLASK_PORT=""
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
ARG_LOG_FILE_MODE=""
ARG_GENERATE_SECRET=0
FORCE_NONINTERACTIVE=0
INSTALL_DRY_RUN=${INSTALL_DRY_RUN:-0}

while [ $# -gt 0 ]; do
    case "$1" in
        --flask-secret-key)
            require_value "$1" "$2"
            ARG_FLASK_SECRET_KEY="$2"
            ARG_FLASK_SECRET_SOURCE="CLI (--flask-secret-key)"
            shift 2
            ;;
        --generate-secret)
            ARG_GENERATE_SECRET=1
            shift
            ;;
        --flask-port)
            require_value "$1" "$2"
            ARG_FLASK_PORT="$2"
            shift 2
            ;;
        --log-file-mode)
            require_value "$1" "$2"
            ARG_LOG_FILE_MODE="$2"
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
        --dry-run)
            INSTALL_DRY_RUN=1
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
    ARG_FLASK_SECRET_SOURCE="INSTALL_FLASK_SECRET_KEY"
fi
if [ -z "$ARG_FLASK_SECRET_KEY" ] && [ -n "${FLASK_SECRET_KEY:-}" ]; then
    ARG_FLASK_SECRET_KEY="$FLASK_SECRET_KEY"
    ARG_FLASK_SECRET_SOURCE="FLASK_SECRET_KEY"
fi

GENERATE_SECRET=0
if [ -n "${INSTALL_GENERATE_SECRET:-}" ]; then
    case "${INSTALL_GENERATE_SECRET,,}" in
        1|true|yes|on)
            GENERATE_SECRET=1
            ;;
        0|false|no|off|'')
            GENERATE_SECRET=0
            ;;
        *)
            echo "Ungültiger Wert für INSTALL_GENERATE_SECRET: '${INSTALL_GENERATE_SECRET}'. Erlaubt sind 0/1 bzw. yes/no." >&2
            exit 1
            ;;
    esac
fi
if [ "$ARG_GENERATE_SECRET" -eq 1 ]; then
    GENERATE_SECRET=1
fi
FLASK_PORT_SOURCE="Standard (80)"
if [ -n "$ARG_FLASK_PORT" ]; then
    FLASK_PORT_SOURCE="CLI (--flask-port)"
elif [ -n "${INSTALL_FLASK_PORT:-}" ]; then
    ARG_FLASK_PORT="$INSTALL_FLASK_PORT"
    FLASK_PORT_SOURCE="INSTALL_FLASK_PORT"
elif [ -n "${FLASK_PORT:-}" ]; then
    ARG_FLASK_PORT="$FLASK_PORT"
    FLASK_PORT_SOURCE="FLASK_PORT"
fi
CONFIGURED_FLASK_PORT="${ARG_FLASK_PORT:-80}"
validate_port "$CONFIGURED_FLASK_PORT" "$FLASK_PORT_SOURCE"
if [ "$CONFIGURED_FLASK_PORT" = "80" ]; then
    echo "HTTP-Port für Gunicorn/Flask: ${CONFIGURED_FLASK_PORT}"
else
    echo "HTTP-Port für Gunicorn/Flask: ${CONFIGURED_FLASK_PORT} (Quelle: ${FLASK_PORT_SOURCE})"
fi
export FLASK_PORT="$CONFIGURED_FLASK_PORT"

LOG_FILE_MODE="$DEFAULT_LOG_FILE_MODE"
LOG_FILE_MODE_SOURCE="Standard (666)"
if [ -n "${INSTALL_LOG_FILE_MODE:-}" ]; then
    LOG_FILE_MODE_SOURCE="INSTALL_LOG_FILE_MODE"
fi
if [ -n "$ARG_LOG_FILE_MODE" ]; then
    LOG_FILE_MODE="$ARG_LOG_FILE_MODE"
    LOG_FILE_MODE_SOURCE="CLI (--log-file-mode)"
fi
validate_chmod_mode "$LOG_FILE_MODE" LOG_FILE_MODE
if [ "$LOG_FILE_MODE_SOURCE" = "Standard (666)" ]; then
    echo "Logfile-Modus: ${LOG_FILE_MODE}"
else
    echo "Logfile-Modus: ${LOG_FILE_MODE} (Quelle: ${LOG_FILE_MODE_SOURCE})"
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

enable_i2c_support() {
    local config_file=""

    if command -v raspi-config >/dev/null 2>&1; then
        if [ "$INSTALL_DRY_RUN" -eq 1 ]; then
            echo "[Dry-Run] Würde 'raspi-config nonint do_i2c 0' ausführen."
        else
            echo "Aktiviere I²C über raspi-config."
            sudo raspi-config nonint do_i2c 0
        fi
    else
        echo "raspi-config nicht gefunden – aktiviere I²C über config.txt."
        local candidates=("/boot/firmware/config.txt" "/boot/config.txt")
        # Siehe device-tree.adoc (Abschnitt \"Shortcuts\"): dtparam=i2c_arm=on aktiviert den I²C-Bus.
        for candidate in "${candidates[@]}"; do
            if [ -f "$candidate" ]; then
                config_file="$candidate"
                break
            fi
        done

        if [ -n "$config_file" ]; then
            if sudo grep -Eq '^[[:space:]]*dtparam=i2c_arm=on([[:space:]]|$)' "$config_file"; then
                echo "dtparam=i2c_arm=on ist bereits in ${config_file} aktiv."
            else
                if [ "$INSTALL_DRY_RUN" -eq 1 ]; then
                    echo "[Dry-Run] Würde dtparam=i2c_arm=on in ${config_file} ergänzen."
                else
                    echo "Aktiviere I²C über Device-Tree-Parameter in ${config_file}."
                    printf '\n%s\n' "dtparam=i2c_arm=on" | sudo tee -a "$config_file" >/dev/null
                fi
            fi
        else
            echo "Warnung: Keine config.txt unter /boot/firmware oder /boot gefunden – bitte I²C manuell aktivieren." >&2
        fi
    fi

    if sudo grep -q '^i2c-dev$' /etc/modules; then
        echo "i2c-dev ist bereits in /etc/modules eingetragen – überspringe."
    else
        if [ "$INSTALL_DRY_RUN" -eq 1 ]; then
            echo "[Dry-Run] Würde i2c-dev zu /etc/modules hinzufügen."
        else
            echo "Füge i2c-dev zu /etc/modules hinzu."
            printf 'i2c-dev\n' | sudo tee -a /etc/modules >/dev/null
        fi
    fi
}

resolve_config_txt_path() {
    local purpose="$1"
    local config_file=""
    local -a candidates=("/boot/firmware/config.txt" "/boot/config.txt")

    for candidate in "${candidates[@]}"; do
        if [ -f "$candidate" ]; then
            config_file="$candidate"
            break
        fi
    done

    if [ -n "$config_file" ]; then
        printf '%s\n' "$config_file"
        return 0
    fi

    if [ -n "$purpose" ]; then
        echo "Warnung: Keine config.txt unter /boot/firmware oder /boot gefunden – ${purpose} kann nicht durchgeführt werden." >&2
    else
        echo "Warnung: Keine config.txt unter /boot/firmware oder /boot gefunden." >&2
    fi

    return 1
}

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

    local config_file
    if ! config_file=$(resolve_config_txt_path "Audio-HAT-Overlay (${overlay_name}) setzen"); then
        return 0
    fi

    local timestamp
    timestamp=$(date +%s)

    if [ "$INSTALL_DRY_RUN" -eq 1 ]; then
        echo "[Dry-Run] Würde ${config_file} nach ${config_file}.hat.bak.${timestamp} sichern."
        echo "[Dry-Run] Würde vorhandene dtoverlay=${overlay_name} Einträge aus ${config_file} entfernen."
        echo "[Dry-Run] Würde '${overlay_line}' an ${config_file} anhängen."
        return 0
    fi

    local tmp_file
    tmp_file=$(mktemp)
    sudo cp "$config_file" "${config_file}.hat.bak.${timestamp}"
    sudo awk -v overlay="$overlay_name" '
        BEGIN { pattern = "^dtoverlay=" overlay "([[:space:]]|,|$)" }
        $0 ~ pattern { next }
        { print }
    ' "$config_file" >"$tmp_file"
    sudo mv "$tmp_file" "$config_file"
    sudo chmod 644 "$config_file"
    printf '%s\n' "$overlay_line" | sudo tee -a "$config_file" >/dev/null
    echo "dtoverlay in ${config_file} gesetzt: $overlay_line"
}

ensure_audio_dtparam() {
    local disable="$1"
    local config_file
    if ! config_file=$(resolve_config_txt_path "dtparam=audio anpassen"); then
        return 0
    fi

    if [ "$disable" = "1" ]; then
        if [ "$INSTALL_DRY_RUN" -eq 1 ]; then
            echo "[Dry-Run] Würde dtparam=audio Einträge aus ${config_file} entfernen."
            if sudo grep -q '^dtparam=audio=off' "$config_file"; then
                echo "[Dry-Run] 'dtparam=audio=off' ist bereits vorhanden – kein erneutes Anhängen erforderlich."
            else
                echo "[Dry-Run] Würde 'dtparam=audio=off' an ${config_file} anhängen."
            fi
        else
            sudo sed -i '/^dtparam=audio=/d' "$config_file"
            if ! sudo grep -q '^dtparam=audio=off' "$config_file"; then
                printf 'dtparam=audio=off\n' | sudo tee -a "$config_file" >/dev/null
            fi
            echo "Onboard-Audio (dtparam=audio=off) gesetzt in ${config_file}."
        fi
    else
        if [ "$INSTALL_DRY_RUN" -eq 1 ]; then
            echo "[Dry-Run] Würde dtparam=audio Einträge aus ${config_file} entfernen."
            echo "[Dry-Run] Würde 'dtparam=audio=on' an ${config_file} anhängen."
        else
            sudo sed -i '/^dtparam=audio=/d' "$config_file"
            printf 'dtparam=audio=on\n' | sudo tee -a "$config_file" >/dev/null
            echo "Onboard-Audio aktiviert (dtparam=audio=on) in ${config_file}."
        fi
    fi
}

print_audio_hat_summary() {
    local config_summary_path=""
    if ! config_summary_path=$(resolve_config_txt_path "Audio-HAT-Zusammenfassung"); then
        config_summary_path=""
    fi

    local config_summary_message
    if [ -n "$config_summary_path" ]; then
        config_summary_message="Konfigurationsdatei: ${config_summary_path}"
    else
        config_summary_message="Keine config.txt gefunden – bitte Pfad (z. B. /boot/config.txt oder /boot/firmware/config.txt) manuell prüfen"
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
    echo "Anpassung später: ${config_summary_message} und sqlite3 audio.db 'UPDATE settings SET value=... WHERE key=\\'dac_sink_name\\';'"
    echo "Alternativ kann DAC_SINK_NAME in ~/.profile überschrieben werden."
}

if [ "${INSTALL_LIBRARY_ONLY:-0}" = "1" ]; then
    return 0 2>/dev/null || exit 0
fi

# Flask-Secret vorbereiten und Zielbenutzer ermitteln
SECRET="$ARG_FLASK_SECRET_KEY"
SECRET_SOURCE="$ARG_FLASK_SECRET_SOURCE"
if [ -z "$SECRET" ] && [ -n "${FLASK_SECRET_KEY:-}" ]; then
    SECRET="$FLASK_SECRET_KEY"
    SECRET_SOURCE="FLASK_SECRET_KEY"
fi

if [ -n "$SECRET" ]; then
    if ! validate_secret_strength "$SECRET" "$SECRET_SOURCE"; then
        if [ "$GENERATE_SECRET" -eq 1 ]; then
            echo "Hinweis: Übergebener Secret-Key (${SECRET_SOURCE:-unbekannt}) erfüllt nicht die Mindestanforderungen und wird ersetzt." >&2
            SECRET=""
        else
            exit 1
        fi
    fi
fi

if [ -z "$SECRET" ] && [ "$GENERATE_SECRET" -eq 1 ]; then
    echo "Generiere automatischen Secret-Key via secrets.token_urlsafe(48)."
    if ! SECRET=$(generate_secret_value); then
        exit 1
    fi
    SECRET_SOURCE="Automatisch generiert (--generate-secret)"
fi

while [ -z "$SECRET" ]; do
    if [ "$GENERATE_SECRET" -eq 1 ]; then
        echo "Generiere automatischen Secret-Key via secrets.token_urlsafe(48)."
        if ! SECRET=$(generate_secret_value); then
            exit 1
        fi
        SECRET_SOURCE="Automatisch generiert (--generate-secret)"
        break
    fi

    if [ "$PROMPT_ALLOWED" -eq 0 ]; then
        echo "Fehler: Es wurde kein gültiger FLASK_SECRET_KEY übergeben. Bitte --flask-secret-key setzen oder INSTALL_FLASK_SECRET_KEY verwenden (≥32 Zeichen, mindestens drei Zeichengruppen)." >&2
        exit 1
    fi

    IFS= read -r -p "FLASK_SECRET_KEY (≥32 Zeichen, Mischung aus Groß-/Kleinbuchstaben, Ziffern, Sonderzeichen): " SECRET
    SECRET_SOURCE="Interaktive Eingabe"
    if ! validate_secret_strength "$SECRET" "$SECRET_SOURCE"; then
        echo "Der eingegebene Secret-Key erfüllt die Mindestanforderungen nicht. Bitte erneut versuchen." >&2
        SECRET=""
    fi
done

if ! validate_secret_strength "$SECRET" "$SECRET_SOURCE"; then
    exit 1
fi

TARGET_USER=${SUDO_USER:-$USER}
if [ -z "$TARGET_USER" ]; then
    TARGET_USER=$(id -un)
fi
TARGET_UID=$(id -u "$TARGET_USER")
TARGET_GROUP=$(id -gn "$TARGET_USER")
TARGET_HOME=$(eval echo "~$TARGET_USER")
PROFILE_FILE="$TARGET_HOME/.profile"
AUDIO_PI_ENV_DIR="${INSTALL_ENV_DIR:-/etc/audio-pi}"
AUDIO_PI_ENV_FILE="${INSTALL_ENV_FILE:-$AUDIO_PI_ENV_DIR/audio-pi.env}"
# INSTALL_ENV_DIR/INSTALL_ENV_FILE/INSTALL_TARGET_HOME/INSTALL_PROFILE_FILE sind
# optionale Hilfen für automatisierte Tests oder Spezial-Deployments.
PROFILE_SOURCE_LINE="if [ -f \"${AUDIO_PI_ENV_FILE}\" ]; then . \"${AUDIO_PI_ENV_FILE}\"; fi"

if [ -n "${INSTALL_TARGET_HOME:-}" ]; then
    TARGET_HOME="$INSTALL_TARGET_HOME"
    PROFILE_FILE="$TARGET_HOME/.profile"
fi
if [ -n "${INSTALL_PROFILE_FILE:-}" ]; then
    PROFILE_FILE="$INSTALL_PROFILE_FILE"
    TARGET_HOME="$(dirname "$PROFILE_FILE")"
fi

if [ "$INSTALL_DRY_RUN" -eq 1 ]; then
    echo "[Dry-Run] Würde ${AUDIO_PI_ENV_DIR} (root:${TARGET_GROUP}, 0750) anlegen."
    echo "[Dry-Run] Würde Besitzrechte per 'sudo chown root:${TARGET_GROUP} ${AUDIO_PI_ENV_DIR}' sicherstellen."
    echo "[Dry-Run] Würde Secret in ${AUDIO_PI_ENV_FILE} (0640, root:${TARGET_GROUP}) speichern."
    echo "[Dry-Run] Würde ${PROFILE_FILE} so anpassen, dass ${PROFILE_SOURCE_LINE}."
else
    sudo install -d -o root -g "$TARGET_GROUP" -m 0750 "$AUDIO_PI_ENV_DIR"
    sudo chown root:"$TARGET_GROUP" "$AUDIO_PI_ENV_DIR"
    tmp_env_file=$(mktemp)
    printf 'FLASK_SECRET_KEY=%s\n' "$SECRET" >"$tmp_env_file"
    sudo install -o root -g "$TARGET_GROUP" -m 0640 "$tmp_env_file" "$AUDIO_PI_ENV_FILE"
    rm -f "$tmp_env_file"

    if ! sudo test -f "$PROFILE_FILE"; then
        sudo install -o "$TARGET_USER" -g "$TARGET_GROUP" -m 0644 /dev/null "$PROFILE_FILE"
    fi

    if sudo grep -q '^export FLASK_SECRET_KEY=' "$PROFILE_FILE"; then
        sudo env PROFILE_SOURCE_LINE="$PROFILE_SOURCE_LINE" perl -0pi -e 's/^export FLASK_SECRET_KEY=.*$/\Q$ENV{PROFILE_SOURCE_LINE}\E/m' "$PROFILE_FILE"
        echo "Alter FLASK_SECRET_KEY Eintrag in $TARGET_HOME/.profile wurde ersetzt."
    fi

    if sudo grep -Fxq "$PROFILE_SOURCE_LINE" "$PROFILE_FILE"; then
        echo "$PROFILE_FILE lädt bereits ${AUDIO_PI_ENV_FILE}."
    else
        printf '%s\n' "$PROFILE_SOURCE_LINE" | sudo tee -a "$PROFILE_FILE" >/dev/null
        echo "$PROFILE_FILE lädt nun ${AUDIO_PI_ENV_FILE}."
    fi
fi

if [ "${INSTALL_EXIT_AFTER_SECRET:-0}" = "1" ]; then
    echo "INSTALL_EXIT_AFTER_SECRET=1 gesetzt – breche nach Secret-Provisioning ab."
    exit 0
fi

if [ "$INSTALL_DRY_RUN" -eq 1 ]; then
    enable_i2c_support
    echo "[Dry-Run] Würde 'sudo usermod -aG pulse \"$TARGET_USER\"' ausführen."
    echo "[Dry-Run] Würde 'sudo usermod -aG pulse-access \"$TARGET_USER\"' ausführen."
    echo "[Dry-Run] Würde 'sudo usermod -aG audio \"$TARGET_USER\"' ausführen."
    echo "[Dry-Run] Würde 'sudo usermod -aG netdev \"$TARGET_USER\"' ausführen."
    echo "[Dry-Run] Würde 'sudo usermod -aG gpio \"$TARGET_USER\"' ausführen."
    echo "[Dry-Run] Würde 'sudo usermod -aG bluetooth \"$TARGET_USER\"' ausführen."
    echo "[Dry-Run] Würde 'sudo usermod -aG i2c \"$TARGET_USER\"' ausführen."
    if [ -f "$POLKIT_RULE_TEMPLATE" ]; then
        POLKIT_RULE_DIR="$(dirname "$POLKIT_RULE_TARGET")"
        echo "[Dry-Run] Würde ${POLKIT_RULE_DIR} (root:root, 0755) anlegen."
        echo "[Dry-Run] Würde Rechte per 'sudo chmod 0755 ${POLKIT_RULE_DIR}' sicherstellen."
        echo "[Dry-Run] Würde Polkit-Regel für ${TARGET_USER} nach ${POLKIT_RULE_TARGET} (0644, root:root) kopieren."
        echo "[Dry-Run] Würde Rechte per 'sudo chmod 0644 ${POLKIT_RULE_TARGET}' sicherstellen."
    else
        echo "[Dry-Run] Warnung: Polkit-Vorlage ${POLKIT_RULE_TEMPLATE} nicht gefunden – Berechtigungen manuell prüfen."
    fi
    echo ""
    echo "[Dry-Run] Installation wurde nicht ausgeführt. Folgende Abschluss-Hinweise würden angezeigt:"
    print_post_install_instructions "$CONFIGURED_FLASK_PORT" 0
    exit 0
fi

# System-Update
apt_get update
apt_get upgrade -y

# Python-Basics & PIP
apt_get install -y python3 python3-pip python3-venv sqlite3

# I²C-Bindings für Python und Diagnose-Tools
apt_get install -y python3-smbus i2c-tools

# Virtuelle Umgebung einrichten
python3 -m venv venv
source venv/bin/activate

# Dev-Packages (für pydub/pygame etc.)
apt_get install -y libasound2-dev libpulse-dev libportaudio2 ffmpeg libffi-dev libjpeg-dev libbluetooth-dev

# Python-Abhängigkeiten installieren
pip install -r requirements.txt

# I²C für RTC aktivieren (raspi-config oder Fallback)
enable_i2c_support

# Werkzeuge für die automatische RTC-Erkennung bereitstellen
if ! command -v i2cdetect >/dev/null 2>&1; then
    apt_get install -y i2c-tools
fi

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
    if config_file=$(resolve_config_txt_path "RTC-Overlay (dtoverlay=i2c-rtc) setzen"); then
        if [ "$INSTALL_DRY_RUN" -eq 1 ]; then
            echo "[Dry-Run] Würde vorhandene 'dtoverlay=i2c-rtc' Einträge aus ${config_file} entfernen."
            echo "[Dry-Run] Würde 'dtoverlay=i2c-rtc,${RTC_OVERLAY_INPUT}' an ${config_file} anhängen."
        else
            sudo sed -i '/^dtoverlay=i2c-rtc/d' "$config_file"
            printf 'dtoverlay=i2c-rtc,%s\n' "$RTC_OVERLAY_INPUT" | sudo tee -a "$config_file" >/dev/null
        fi
    fi
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
sudo usermod -aG netdev "$TARGET_USER"
sudo usermod -aG gpio "$TARGET_USER"
sudo usermod -aG bluetooth "$TARGET_USER"
sudo usermod -aG i2c "$TARGET_USER"

HAT_DEFAULT_SINK_HINT="alsa_output.platform-soc_107c000000_sound.stereo-fallback"

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

print_audio_hat_summary

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

LOGROTATE_TEMPLATE="$SCRIPT_DIR/scripts/logrotate/audio-pi"
LOGROTATE_TARGET="/etc/logrotate.d/audio-pi"
if [ -f "$LOGROTATE_TEMPLATE" ]; then
    LOGROTATE_CREATE_MODE="$LOG_FILE_MODE"
    if [ ${#LOGROTATE_CREATE_MODE} -eq 3 ]; then
        LOGROTATE_CREATE_MODE="0${LOGROTATE_CREATE_MODE}"
    fi
    tmp_lr=$(mktemp)
    APP_LOG_PATH="$(pwd)/app.log" \
    TARGET_USER="$TARGET_USER" \
    TARGET_GROUP="$TARGET_GROUP" \
    LOGROTATE_CREATE_MODE="$LOGROTATE_CREATE_MODE" \
    python3 - "$LOGROTATE_TEMPLATE" "$tmp_lr" <<'PY'
import os
import sys

src, dest = sys.argv[1:3]
with open(src, encoding='utf-8') as fh:
    data = fh.read()

replacements = {
    '@APP_LOG_PATH@': os.environ['APP_LOG_PATH'],
    '@TARGET_USER@': os.environ['TARGET_USER'],
    '@TARGET_GROUP@': os.environ['TARGET_GROUP'],
    '@LOG_FILE_MODE@': os.environ['LOGROTATE_CREATE_MODE'],
}

for key, value in replacements.items():
    data = data.replace(key, value)

with open(dest, 'w', encoding='utf-8') as fh:
    fh.write(data)
PY
    sudo install -d -m 0755 /etc/logrotate.d
    sudo install -m 0644 "$tmp_lr" "$LOGROTATE_TARGET"
    rm -f "$tmp_lr"
    echo "Logrotate-Konfiguration nach $LOGROTATE_TARGET geschrieben."
else
    echo "Keine Logrotate-Vorlage ($LOGROTATE_TEMPLATE) gefunden – Rotation übersprungen."
fi

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
if sudo grep -q '^EnvironmentFile=' /etc/systemd/system/audio-pi.service; then
    sudo sed -i "s|^EnvironmentFile=.*|EnvironmentFile=$AUDIO_PI_ENV_FILE|" /etc/systemd/system/audio-pi.service
else
    sudo sed -i "/^Environment=FLASK_DEBUG=/a EnvironmentFile=$AUDIO_PI_ENV_FILE" /etc/systemd/system/audio-pi.service
fi
if sudo grep -q '^Environment=FLASK_PORT=' /etc/systemd/system/audio-pi.service; then
    sudo sed -i "s|^Environment=FLASK_PORT=.*|Environment=FLASK_PORT=${CONFIGURED_FLASK_PORT}|" /etc/systemd/system/audio-pi.service
else
    echo "Environment=FLASK_PORT=${CONFIGURED_FLASK_PORT}" | sudo tee -a /etc/systemd/system/audio-pi.service >/dev/null
fi
SYSTEMD_DISABLE_SUDO_VALUE=${INSTALL_DISABLE_SUDO:-1}
SYSTEMD_DISABLE_SUDO_ESCAPED=$(printf '%s' "$SYSTEMD_DISABLE_SUDO_VALUE" | sed -e 's/[\\&|]/\\&/g')
if sudo grep -q '^Environment=AUDIO_PI_DISABLE_SUDO=' /etc/systemd/system/audio-pi.service; then
    sudo sed -i "s|^Environment=AUDIO_PI_DISABLE_SUDO=.*|Environment=AUDIO_PI_DISABLE_SUDO=${SYSTEMD_DISABLE_SUDO_ESCAPED}|" /etc/systemd/system/audio-pi.service
else
    sudo sed -i "/^Environment=XDG_RUNTIME_DIR=/a Environment=AUDIO_PI_DISABLE_SUDO=${SYSTEMD_DISABLE_SUDO_ESCAPED}" /etc/systemd/system/audio-pi.service
fi
sudo sed -i "s|^User=.*|User=$TARGET_USER|" /etc/systemd/system/audio-pi.service
sudo sed -i "s|^Group=.*|Group=$TARGET_GROUP|" /etc/systemd/system/audio-pi.service
echo "HTTP-Port ${CONFIGURED_FLASK_PORT} wurde in /etc/systemd/system/audio-pi.service hinterlegt."
echo "Systemd-Dienst wird für Benutzer $TARGET_USER und Gruppe $TARGET_GROUP konfiguriert."
if [ -f "$AUDIO_PI_ALSACTL_UNIT_TEMPLATE" ]; then
    sudo install -o root -g root -m 0644 "$AUDIO_PI_ALSACTL_UNIT_TEMPLATE" "$AUDIO_PI_ALSACTL_UNIT_TARGET"
    if sudo systemctl reset-failed audio-pi-alsactl.service >/dev/null 2>&1; then
        echo "Status von audio-pi-alsactl.service zurückgesetzt."
    fi
    echo "One-Shot-Unit audio-pi-alsactl.service aktualisiert (kein automatischer Start)."
else
    echo "Warnung: One-Shot-Unit-Vorlage $AUDIO_PI_ALSACTL_UNIT_TEMPLATE nicht gefunden – persistente Lautstärke benötigt manuelle Pflege."
fi
if [ -f "$POLKIT_RULE_TEMPLATE" ]; then
    POLKIT_RULE_DIR="$(dirname "$POLKIT_RULE_TARGET")"
    sudo install -d -o root -g root -m 0755 "$POLKIT_RULE_DIR"
    sudo chmod 0755 "$POLKIT_RULE_DIR"
    tmp_polkit=$(mktemp)
    POLKIT_USER_ESCAPED=$(printf '%s' "$TARGET_USER" | sed -e 's/[\\/&]/\\&/g')
    sed "s/__AUDIO_PI_USER__/$POLKIT_USER_ESCAPED/g" "$POLKIT_RULE_TEMPLATE" > "$tmp_polkit"
    sudo install -o root -g root -m 0644 "$tmp_polkit" "$POLKIT_RULE_TARGET"
    sudo chmod 0644 "$POLKIT_RULE_TARGET"
    rm -f "$tmp_polkit"
    echo "Polkit-Regel für $TARGET_USER nach $POLKIT_RULE_TARGET installiert."
else
    echo "Warnung: Polkit-Vorlage $POLKIT_RULE_TEMPLATE nicht gefunden – bitte Berechtigungen manuell prüfen."
fi
install_tmpfiles_rule "$TARGET_USER" "$TARGET_GROUP" "$TARGET_UID"
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

print_post_install_instructions "$CONFIGURED_FLASK_PORT" "$AP_CONFIGURED"
echo ""
