#!/bin/bash
# Hilfsfunktionen zur Auswahl und Dokumentation gängiger Audio-HATs

if [[ -z "${HAT_DEFAULT_SINK_HINT:-}" ]]; then
    HAT_DEFAULT_SINK_HINT="alsa_output.platform-soc_107c000000_sound.stereo-fallback"
fi

# Reihenfolge der Profile für Menüausgabe
HAT_PROFILE_ORDER=(
    none
    hifiberry_dacplus
    hifiberry_amp2
    iqaudio_dacplus
)

declare -A HAT_PROFILE_LABELS
declare -A HAT_PROFILE_OVERLAY
declare -A HAT_PROFILE_OPTIONS
declare -A HAT_PROFILE_SINK_HINT
declare -A HAT_PROFILE_NOTES
declare -A HAT_PROFILE_DISABLE_ONBOARD

HAT_PROFILE_LABELS[none]="Kein Zusatz-HAT (Onboard 3,5mm Klinke)"
HAT_PROFILE_OVERLAY[none]=""
HAT_PROFILE_OPTIONS[none]=""
HAT_PROFILE_SINK_HINT[none]="$HAT_DEFAULT_SINK_HINT"
HAT_PROFILE_NOTES[none]="Nutze das integrierte Audio des Raspberry Pi."
HAT_PROFILE_DISABLE_ONBOARD[none]=0

HAT_PROFILE_LABELS[hifiberry_dacplus]="HiFiBerry DAC+"
HAT_PROFILE_OVERLAY[hifiberry_dacplus]="hifiberry-dacplus"
HAT_PROFILE_OPTIONS[hifiberry_dacplus]=""
HAT_PROFILE_SINK_HINT[hifiberry_dacplus]="pattern:alsa_output.platform-*_sound.stereo-fallback"
HAT_PROFILE_NOTES[hifiberry_dacplus]="Setzt den HiFiBerry DAC+ als I2S-DAC."
HAT_PROFILE_DISABLE_ONBOARD[hifiberry_dacplus]=1

HAT_PROFILE_LABELS[hifiberry_amp2]="HiFiBerry Amp2"
HAT_PROFILE_OVERLAY[hifiberry_amp2]="hifiberry-amp"
HAT_PROFILE_OPTIONS[hifiberry_amp2]="gpio=18=op,dh"
HAT_PROFILE_SINK_HINT[hifiberry_amp2]="pattern:alsa_output.platform-*_sound.stereo-fallback"
HAT_PROFILE_NOTES[hifiberry_amp2]="Aktiviert GPIO 18 als Enable-Pin für den Amp."
HAT_PROFILE_DISABLE_ONBOARD[hifiberry_amp2]=1

HAT_PROFILE_LABELS[iqaudio_dacplus]="IQaudio DAC+"
HAT_PROFILE_OVERLAY[iqaudio_dacplus]="iqaudio-dacplus"
HAT_PROFILE_OPTIONS[iqaudio_dacplus]=""
HAT_PROFILE_SINK_HINT[iqaudio_dacplus]="pattern:alsa_output.platform-*_sound.stereo-fallback"
HAT_PROFILE_NOTES[iqaudio_dacplus]="Setzt den IQaudio DAC+ als I2S-DAC."
HAT_PROFILE_DISABLE_ONBOARD[iqaudio_dacplus]=1

hat__print_menu() {
    echo ""
    echo "Audio-HAT-Auswahl"
    echo "-------------------"
    local idx=1
    local key
    for key in "${HAT_PROFILE_ORDER[@]}"; do
        printf " %d) %s\n" "$idx" "${HAT_PROFILE_LABELS[$key]}"
        ((idx++))
    done
    printf " %d) Manuelle Eingabe (eigene dtoverlay/Sink)\n" "$idx"
    ((idx++))
    printf " %d) Überspringen (keine Änderungen)\n" "$idx"
    echo ""
}

hat__apply_manual_input() {
    read -rp "Eigener dtoverlay-Name (leer für keinen): " manual_overlay || return 1
    read -rp "Zusätzliche Overlay-Optionen (z.B. gpio=18=op,dh, optional): " manual_options || return 1
    local manual_sink
    while true; do
        read -rp "PulseAudio-Sink-Name oder Muster (z.B. pattern:alsa_output.platform-*): " manual_sink || return 1
        if [[ -n "${manual_sink}" ]]; then
            break
        fi
        echo "Bitte einen Sink-Namen oder ein Muster angeben."
    done
    local manual_disable=${HAT_DISABLE_ONBOARD_AUDIO:-}
    if [[ -z "$manual_disable" ]]; then
        read -rp "Onboard-Audio (dtparam=audio=on) deaktivieren? [j/N]: " manual_disable
    fi
    case "${manual_disable,,}" in
        j|ja|y|yes|1)
            manual_disable=1
            ;;
        *)
            manual_disable=0
            ;;
    esac
    HAT_SELECTED_KEY="manual"
    HAT_SELECTED_LABEL="Manuelle Konfiguration"
    HAT_SELECTED_OVERLAY="$manual_overlay"
    HAT_SELECTED_OPTIONS="$manual_options"
    HAT_SELECTED_SINK_HINT="$manual_sink"
    HAT_SELECTED_DISABLE_ONBOARD="$manual_disable"
    HAT_SELECTED_NOTES="Eigene Werte – bitte Dokumentation prüfen."
}

hat__select_from_menu() {
    hat__print_menu
    local selection
    local max_choice=$(( ${#HAT_PROFILE_ORDER[@]} + 2 ))
    while true; do
        read -rp "Auswahl (1-${max_choice}): " selection || return 1
        if [[ "$selection" =~ ^[0-9]+$ ]] && (( selection >= 1 && selection <= max_choice )); then
            break
        fi
        echo "Ungültige Auswahl – bitte erneut versuchen."
    done
    local manual_index=$(( ${#HAT_PROFILE_ORDER[@]} + 1 ))
    local skip_index=$(( manual_index + 1 ))
    if (( selection == manual_index )); then
        hat__apply_manual_input
        return 0
    fi
    if (( selection == skip_index )); then
        HAT_SELECTED_KEY="skip"
        HAT_SELECTED_LABEL="Keine Änderung"
        HAT_SELECTED_OVERLAY=""
        HAT_SELECTED_OPTIONS=""
        HAT_SELECTED_SINK_HINT="$HAT_DEFAULT_SINK_HINT"
        HAT_SELECTED_DISABLE_ONBOARD=0
        HAT_SELECTED_NOTES="Konfiguration bleibt unverändert."
        return 0
    fi
    local index=$(( selection - 1 ))
    local key="${HAT_PROFILE_ORDER[$index]}"
    hat__assign_profile "$key"
}

hat__assign_profile() {
    local key="$1"
    HAT_SELECTED_KEY="$key"
    HAT_SELECTED_LABEL="${HAT_PROFILE_LABELS[$key]}"
    HAT_SELECTED_OVERLAY="${HAT_PROFILE_OVERLAY[$key]}"
    HAT_SELECTED_OPTIONS="${HAT_PROFILE_OPTIONS[$key]}"
    HAT_SELECTED_SINK_HINT="${HAT_PROFILE_SINK_HINT[$key]}"
    HAT_SELECTED_DISABLE_ONBOARD="${HAT_PROFILE_DISABLE_ONBOARD[$key]}"
    HAT_SELECTED_NOTES="${HAT_PROFILE_NOTES[$key]}"
}

hat__handle_noninteractive() {
    local key="${HAT_MODEL:-}"
    key="${key,,}"
    if [[ -z "$key" ]]; then
        return 1
    fi
    if [[ "$key" == "manual" ]]; then
        HAT_SELECTED_KEY="manual"
        HAT_SELECTED_LABEL="Manuelle Konfiguration"
        HAT_SELECTED_OVERLAY="${HAT_DTOOVERLAY:-}"
        HAT_SELECTED_OPTIONS="${HAT_OPTIONS:-}"
        if [[ -n "${HAT_SINK_NAME:-}" ]]; then
            HAT_SELECTED_SINK_HINT="${HAT_SINK_NAME}"
        else
            HAT_SELECTED_SINK_HINT="$HAT_DEFAULT_SINK_HINT"
        fi
        local disable_value="${HAT_DISABLE_ONBOARD_AUDIO:-}"
        case "${disable_value,,}" in
            1|true|yes|y|j|ja)
                HAT_SELECTED_DISABLE_ONBOARD=1
                ;;
            0|false|no|n|nein)
                HAT_SELECTED_DISABLE_ONBOARD=0
                ;;
            *)
                HAT_SELECTED_DISABLE_ONBOARD=0
                ;;
        esac
        HAT_SELECTED_NOTES="Werte über Umgebungsvariablen gesetzt."
        return 0
    fi
    local match_found=0
    local key_candidate
    for key_candidate in "${HAT_PROFILE_ORDER[@]}"; do
        if [[ "$key" == "$key_candidate" ]]; then
            hat__assign_profile "$key_candidate"
            match_found=1
            break
        fi
    done
    if [[ $match_found -eq 0 ]]; then
        echo "Warnung: Unbekannter HAT_MODEL='$HAT_MODEL'. Es wird keine Änderung vorgenommen." >&2
        HAT_SELECTED_KEY="skip"
        HAT_SELECTED_LABEL="Keine Änderung"
        HAT_SELECTED_OVERLAY=""
        HAT_SELECTED_OPTIONS=""
        HAT_SELECTED_SINK_HINT="$HAT_DEFAULT_SINK_HINT"
        HAT_SELECTED_DISABLE_ONBOARD=0
        HAT_SELECTED_NOTES="Unbekannter HAT_MODEL-Wert."
    fi
    return 0
}

hat_select_profile() {
    HAT_SELECTED_KEY=""
    HAT_SELECTED_LABEL=""
    HAT_SELECTED_OVERLAY=""
    HAT_SELECTED_OPTIONS=""
    HAT_SELECTED_SINK_HINT="$HAT_DEFAULT_SINK_HINT"
    HAT_SELECTED_DISABLE_ONBOARD=0
    HAT_SELECTED_NOTES=""

    if hat__handle_noninteractive; then
        :
    else
        local noninteractive="${HAT_NONINTERACTIVE:-}"
        if [[ "$noninteractive" =~ ^(1|true|yes|on)$ ]]; then
            HAT_SELECTED_KEY="skip"
            HAT_SELECTED_LABEL="Keine Änderung"
            HAT_SELECTED_NOTES="Nicht-interaktiver Modus ohne Vorgaben."
            return 0
        fi
        hat__select_from_menu
    fi

    echo "Ausgewählter HAT: ${HAT_SELECTED_LABEL}" >&2
    if [[ -n "$HAT_SELECTED_OVERLAY" ]]; then
        local overlay_text="${HAT_SELECTED_OVERLAY}"
        if [[ -n "$HAT_SELECTED_OPTIONS" ]]; then
            overlay_text+=" (${HAT_SELECTED_OPTIONS})"
        fi
        echo "dtoverlay-Vorschlag: ${overlay_text}" >&2
    else
        echo "dtoverlay-Vorschlag: (keiner)" >&2
    fi
    echo "PulseAudio-Sink/Muster: ${HAT_SELECTED_SINK_HINT}" >&2
    if [[ -n "$HAT_SELECTED_NOTES" ]]; then
        echo "Hinweis: ${HAT_SELECTED_NOTES}" >&2
    fi
    return 0
}

