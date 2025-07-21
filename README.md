# Audio-Pi-Control

Audio-Pi-Control ist ein vollständiges Steuer- und Audiomanagement-System für den Raspberry Pi (getestet ab Pi 4/5), entwickelt für den Hifi-Eigenbau, Bus-/Wohnmobil-Ausbau oder stationäre Beschallung. Es steuert lokale Audio-Wiedergabe, Playlists, Zeitpläne, GPIO-Endstufe, Bluetooth (als Audio-Sink!), WLAN, Lautstärke und die Echtzeituhr (RTC) – alles bequem über eine Weboberfläche.

---

## Hauptfunktionen

- **Audio-Wiedergabe per Zeitplan** (Einzeldateien & Playlists)
- **Bluetooth als Audio-Sink** (Handy → Pi → Verstärker)
- **Endstufe/GPIO automatisch schalten** (bei Musik oder BT-Audio)
- **RTC-Steuerung & Systemzeit**
- **WLAN-Scan, Verbindungsaufbau, AP-Fallback**
- **Web-Interface (Flask, passwortgeschützt)**
- **Audio-Upload, Playlist-Verwaltung**
- **Protokollierung & Logs**
- **Passwort-Management**
- **Alle Daten in SQLite-DB**

---

## Schnellstart

**1. System installieren**
```bash
sudo bash install.sh
```

**2. Anwendung starten**
Setzen Sie vor dem Start die Umgebungsvariable `FLASK_SECRET_KEY`:

```bash
export FLASK_SECRET_KEY="ein_sicherer_schluessel"
python3 app.py
```

### Automatischer Start (systemd)

Die Beispieldatei `audio-pi.service` ermöglicht den automatischen Start als systemd-Dienst. 
Durch die Zeile `ExecStartPre=/bin/sleep 10` wartet der Dienst nach dem Booten zehn Sekunden, bevor `app.py` ausgeführt wird.

Zum Aktivieren kopieren Sie die Datei z.B. nach `/etc/systemd/system/` und laden die Unit neu:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now audio-pi.service
```
