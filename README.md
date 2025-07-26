# Audio-Pi-Control

Audio-Pi-Control ist ein vollständiges Steuer- und Audiomanagement-System für den Raspberry Pi (getestet ab Pi 4/5), entwickelt für den Hifi-Eigenbau, Bus-/Wohnmobil-Ausbau oder stationäre Beschallung. Es steuert lokale Audio-Wiedergabe, Playlists, Zeitpläne, GPIO-Endstufe, Bluetooth (als Audio-Sink!), WLAN, Lautstärke und die Echtzeituhr (RTC) – alles bequem über eine Weboberfläche.

---

## Hauptfunktionen

- **Audio-Wiedergabe per Zeitplan** (Einzeldateien & Playlists)
- **Bluetooth als Audio-Sink** (Handy → Pi → Verstärker)
- **Bluetooth über Web-UI ein-/ausschalten**
- **Endstufe/GPIO automatisch schalten** (bei Musik oder BT-Audio)
- **Relais-Logik invertierbar über Web-UI**
- **RTC-Steuerung & Systemzeit**
- **WLAN-Scan, Verbindungsaufbau, AP-Fallback** (SSIDs und Passwörter dürfen Anführungszeichen und Backslashes enthalten)
- **Web-Interface (Flask, passwortgeschützt)**
- **Audio-Upload, Playlist-Verwaltung**
- **Protokollierung & Logs**
- **Passwort-Management**
- **Alle Daten in SQLite-DB**
- Einmalige Zeitpläne, deren Zeitpunkt bereits vergangen ist, werden beim Start automatisch übersprungen.
- Zeitpläne laufen nun über **APScheduler**; dank `misfire_grace_time` werden nach dem Start keine verpassten Jobs mehr nachgeholt.
- `parse_once_datetime` verarbeitet einmalige Zeitangaben in verschiedenen Formaten.

Im Bereich "System" der Weboberfläche befinden sich Buttons zum Ein- und
Ausschalten von Bluetooth.

---

## Schnellstart

**1. System installieren**
```bash
sudo bash install.sh
```
Während der Installation fragt das Skript nach einem Wert für `FLASK_SECRET_KEY`
und richtet den systemd-Dienst direkt ein.

**2. Umgebung einrichten**
```bash
bash setup_env.sh
```

**3. Virtuelle Umgebung aktivieren**
```bash
source venv/bin/activate
```

**4. Anwendung starten**
Die Anwendung bricht sofort ab, wenn `FLASK_SECRET_KEY` nicht gesetzt ist. Nach
dem Setzen der Variable genügt ein einfacher Aufruf. Seit dem behobenen
Startproblem laufen Scheduler, Bluetooth-Monitor und Co. automatisch an – es
ist kein zusätzlicher Funktionsaufruf mehr nötig.

```bash
export FLASK_SECRET_KEY="ein_sicherer_schluessel"
python app.py
```

### Automatischer Start (systemd)

`install.sh` kopiert und konfiguriert `audio-pi.service` automatisch. Die
Service-Datei nutzt den Python-Interpreter aus der virtuellen Umgebung und
startet das Programm mit PulseAudio-Zugriff (entweder über `User=pi` oder mit
`PULSE_RUNTIME_PATH`). Durch `ExecStartPre=/bin/sleep 10` wartet der Dienst nach
dem Booten zehn Sekunden, bevor `app.py` ausgeführt wird. Zusätzlich setzt die
Service-Datei `XDG_RUNTIME_DIR=/run/user/1000`, damit PulseAudio auch ohne
laufende Sitzung funktioniert.

Sollte die Unit manuell neu geladen werden müssen, genügt:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now audio-pi.service
```

## Datenbank-Initialisierung

Bei der ersten Ausführung legt die Anwendung automatisch die SQLite-Datenbank `audio.db` an und erzeugt die benötigten Tabellen sowie einen Standard-Benutzer (`admin` / `password`). Es ist daher nicht notwendig, eine vorgefüllte Datenbank mitzuliefern. Wenn `audio.db` nicht existiert, wird sie beim Start erstellt.


## Update aus dem Git-Repository

Im Web-Interface gibt es einen **Update**-Button. Nach dem Login kann damit ein
`git pull` ausgeführt werden, um lokale Änderungen aus dem Repository zu holen.
Ein Hinweis informiert über Erfolg oder Fehler.

## Tests

Die Tests laufen mit `pytest`. Nachdem die Abhängigkeiten installiert sind,
(z.B. via `pip install -r requirements.txt`), lassen sich alle Tests einfach per

```bash
pytest
```
ausführen.

## License

Dieses Projekt steht unter der [MIT-Lizenz](LICENSE).
