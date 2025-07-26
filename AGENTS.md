# Codex Agents Cheat-Sheet: Audio-Pi Websystem

## Systemüberblick

Dieses Repository verwandelt den Raspberry Pi in ein lokales Steuer- und Audiosystem:
- **Audio-/MP3-Upload und Wiedergabe** (inkl. Playlists & Zeitpläne/Scheduler)
- **Endstufe** (GPIO, lgpio)
- **Bluetooth-Audio (als Sink/Loopback)**
- **PulseAudio/ALSA/HiFiBerry-Support**
- **WLAN-Management (Client + AP-Modus)**
- **RTC (I²C, z.B. PCF8563)**
- **Web-UI & Benutzerverwaltung** (Flask, SQLite3)

---

## Hauptagenten/Module

### 1. Flask-Webserver (`app.py`)
- **HTTP-Web-GUI**: Datei-Upload, Playlists, Zeitpläne, Lautstärke, RTC, Logs
- **REST-ähnliche Endpunkte** (GET/POST)
- **Authentifizierung**: Flask-Login

### 2. Audio/Playback-Agent
- **pygame**: Musik/Audio-Abspielen
- **pydub**: Normalisierung, Umwandlung, temporäre WAVs
- **Verstärker-GPIO**: Steuerung per lgpio
- **PulseAudio/ALSA**: Lautstärke, Sink/Source-Auswahl

### 3. Bluetooth-Agent
- **A2DP-Audio-Sink** (Pi wird zur BT-Box)
- **PulseAudio Loopback** für Bluetooth → DAC
- **BT-Monitor-Thread**: Erkennt BT-Verbindung, aktiviert Loopback & Endstufe

### 4. Scheduler-Agent (schedule)
- **Wartet auf geplante Wiedergaben**
- **Verhindert Mehrfachstart direkt nach Boot**
- **Lädt alle Zeitpläne aus der Datenbank**

### 5. RTC-Agent
- **RTC auslesen/setzen via I²C (smbus, PCF8563)**
- **Sync mit Systemzeit & Web-UI**  
- **Manuelle Setzen über Web-UI möglich**

### 6. WLAN/AP-Agent
- **Client/Hotspot-Umschaltung** (hostapd/dnsmasq)
- **Scan, Connect, Statusanzeige im Web-UI**

### 7. Datenbank-Agent
- **sqlite3** für Benutzer, Dateien, Playlists, Zeitpläne

---

## API/Control-Überblick (UI & Endpunkte)

| Route                            | Methode | Funktion / UI                   | Login nötig? |
|-----------------------------------|---------|---------------------------------|--------------|
| /                                | GET     | Haupt-UI                        | ✔            |
| /login, /logout                  | GET/POST| Login, Logout                   | ✖            |
| /upload                          | POST    | Audiodatei hochladen            | ✔            |
| /play_now/<typ>/<id>             | GET     | Datei/Playlist sofort abspielen | ✔            |
| /schedule, /delete_schedule      | POST    | Zeitplan setzen/löschen         | ✔            |
| /create_playlist, /add_to_playlist | POST  | Playlists verwalten             | ✔            |
| /delete_playlist/<id>            | POST    | Playlist löschen                | ✔            |
| /toggle_pause, /stop_playback    | POST    | Wiedergabe steuern              | ✔            |
| /activate_amp, /deactivate_amp   | POST    | Endstufe schalten               | ✔            |
| /volume                          | POST    | Lautstärke setzen               | ✔            |
| /logs                            | GET     | Logfile anzeigen                | ✔            |
| /wlan_scan, /wlan_connect        | GET/POST| WLAN-Management                 | ✔            |
| /change_password                 | GET/POST| Passwort ändern                 | ✔            |
| /set_time, /sync_time_from_internet | GET/POST | RTC/Zeitsync                   | ✔            |
| ...                              | ...     | Siehe app.py/README             |              |

---

## Best Practices für Agenten (Codex)

- **Hardwarezugriffe (GPIO/I²C/Pulse/ALSA)** immer auf Fehler prüfen!
- **Scheduler nicht doppelt triggern** nach Boot (siehe load_schedules).
- **Logs & Rechte**: app.log gehört auf 666 (oder Logrotate einrichten).
- **Web-UI** NIE direkt ins Internet ohne Reverse-Proxy/SSL!
- **Passwörter** regelmäßig ändern (Login-UI vorhanden).

---

## Modular & Erweiterbar für Codex

- Agenten können in eigene Python-Module ausgelagert werden (z.B. `core/audio.py`, `core/amp.py` ...)
- REST-API kann erweitert werden (z.B. für mobile Steuerung, HomeAssistant etc.)

---

## Installationsskript & Systemhinweise

Siehe **install.sh** für Gruppenrechte, ALSA, I²C, Pulse, RTC, Bluetooth & Webserver-Vorbereitung.  
**Neustart** nach RTC- oder Overlay-Setup empfohlen!

---

**Letzte Änderung:** 20.07.2025  
**Kontakt:** Open Source, Issues/PRs willkommen!
