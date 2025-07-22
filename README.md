# Audio-Pi-Control

Audio-Pi-Control ist ein vollständiges Steuer- und Audiomanagement-System für den Raspberry Pi (getestet ab Pi 4/5), entwickelt für den Hifi-Eigenbau, Bus-/Wohnmobil-Ausbau oder stationäre Beschallung. Es steuert lokale Audio-Wiedergabe, Playlists, Zeitpläne, GPIO-Endstufe, Bluetooth (als Audio-Sink!), WLAN, Lautstärke und die Echtzeituhr (RTC) – alles bequem über eine Weboberfläche.

---

## Hauptfunktionen

- **Audio-Wiedergabe per Zeitplan** (Einzeldateien & Playlists)
- **Bluetooth als Audio-Sink** (Handy → Pi → Verstärker)
- **Endstufe/GPIO automatisch schalten** (bei Musik oder BT-Audio)
- **RTC-Steuerung & Systemzeit**
- **WLAN-Scan, Verbindungsaufbau, AP-Fallback** (SSIDs und Passwörter dürfen Anführungszeichen und Backslashes enthalten)
- **Web-Interface (Flask, passwortgeschützt)**
- **Audio-Upload, Playlist-Verwaltung**
- **Protokollierung & Logs**
- **Passwort-Management**
- **Alle Daten in SQLite-DB**
- Einmalige Zeitpläne, deren Zeitpunkt bereits vergangen ist, werden beim Start automatisch übersprungen.

---

## Schnellstart

**1. System installieren**
```bash
sudo bash install.sh
```

**2. Virtuelle Umgebung aktivieren**
```bash
source venv/bin/activate
```
**3. Anwendung starten**
Die Anwendung bricht sofort ab, wenn `FLASK_SECRET_KEY` nicht gesetzt ist. Nach
dem Setzen der Variable genügt ein einfacher Aufruf. Seit dem behobenen
Startproblem laufen Scheduler, Bluetooth-Monitor und Co. automatisch an – es
ist kein zusätzlicher Funktionsaufruf mehr nötig.

```bash
export FLASK_SECRET_KEY="ein_sicherer_schluessel"
python app.py
```

### Automatischer Start (systemd)

Die Beispieldatei `audio-pi.service` ermöglicht den automatischen Start als systemd-Dienst.
Durch die Zeile `ExecStartPre=/bin/sleep 10` wartet der Dienst nach dem Booten zehn Sekunden, bevor `app.py` ausgeführt wird.

Zum Aktivieren kopieren Sie die Datei z.B. nach `/etc/systemd/system/` und laden die Unit neu:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now audio-pi.service
```

## Datenbank-Initialisierung

Bei der ersten Ausführung legt die Anwendung automatisch die SQLite-Datenbank `audio.db` an und erzeugt die benötigten Tabellen sowie einen Standard-Benutzer (`admin` / `password`). Es ist daher nicht notwendig, eine vorgefüllte Datenbank mitzuliefern. Wenn `audio.db` nicht existiert, wird sie beim Start erstellt.

## Tests

Für die Unittests werden die Abhängigkeiten aus beiden Requirements-Dateien benötigt. Aktivieren Sie zunächst die virtuelle Umgebung und installieren Sie dann die Requirements:

```bash
source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
pytest
```

Die Tests lassen sich danach mit `pytest` starten.

## License

This project is licensed under the [MIT License](LICENSE).
