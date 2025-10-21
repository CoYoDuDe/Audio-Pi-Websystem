# Audio-Pi-Control

Audio-Pi-Control ist ein vollständiges Steuer- und Audiomanagement-System für den Raspberry Pi (getestet ab Pi 4/5), entwickelt für den Hifi-Eigenbau, Bus-/Wohnmobil-Ausbau oder stationäre Beschallung. Es steuert lokale Audio-Wiedergabe, Playlists, Zeitpläne, GPIO-Endstufe, Bluetooth (als Audio-Sink!), WLAN, Lautstärke und die Echtzeituhr (RTC) – alles bequem über eine Weboberfläche.

---

## Hauptfunktionen

- **Audio-Wiedergabe per Zeitplan** (Einzeldateien & Playlists)
- **Bluetooth als Audio-Sink** (Handy → Pi → Verstärker)
- **Bluetooth über Web-UI ein-/ausschalten**
- **System-Neustart und Herunterfahren über Web-UI**
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
Ausschalten von Bluetooth sowie Schaltflächen zum geordneten Neustart oder
Herunterfahren des Raspberry Pi.

---

## Schnellstart

**1. System installieren**
```bash
sudo bash install.sh
```
Während der Installation fragt das Skript nach einem Wert für `FLASK_SECRET_KEY`
und richtet den systemd-Dienst direkt ein. Die Eingabe darf nicht leer sein –
das Skript wiederholt die Abfrage so lange, bis ein Wert vorliegt.
Zusätzlich stellt `install.sh` sicher, dass die Datenbank `audio.db` dem
Dienstbenutzer (`$TARGET_USER:$TARGET_GROUP`) gehört und mit `chmod 660`
beschreibbare Rechte erhält, unabhängig davon, ob die Datei neu angelegt oder
bereits vorhanden war.

> **Neu:** Der Installer übernimmt Secrets inklusive Sonderzeichen (z. B. `/`, `&`, Leerzeichen)
> unverändert sowohl für den interaktiven Start als auch für den systemd-Dienst.

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
# optional: Port ändern (Standard ist 80)
export FLASK_PORT=8080
python app.py
```

> **Hinweis:** Für Ports <1024 benötigt der ausführende Benutzer die Capability
> `CAP_NET_BIND_SERVICE`. Entweder wird die Anwendung über den systemd-Dienst
> gestartet (siehe unten), oder die Python-Interpreter-Binary erhält diese
> Capability beispielsweise per `sudo setcap 'cap_net_bind_service=+ep' $(readlink -f $(which python3))`.

### Automatischer Start (systemd)

`install.sh` kopiert und konfiguriert `audio-pi.service` automatisch. Dabei wird der während der Installation abgefragte `FLASK_SECRET_KEY` eingetragen; ohne gültigen Schlüssel startet der Dienst nicht. Die
Service-Datei setzt außerdem `FLASK_PORT=80` und stattet den Dienst dank
`AmbientCapabilities=CAP_NET_BIND_SERVICE` mit den nötigen Rechten aus, damit
der nicht-root-Benutzer `pi` auch Port 80 binden kann. Der Python-Interpreter
wird aus der virtuellen Umgebung gestartet und erhält PulseAudio-Zugriff
(entweder über `User=pi` oder mit `PULSE_RUNTIME_PATH`). Durch
`ExecStartPre=/bin/sleep 10` wartet der Dienst nach dem Booten zehn Sekunden,
bevor `app.py` ausgeführt wird. Zusätzlich setzt die Service-Datei
`XDG_RUNTIME_DIR=/run/user/1000`, damit PulseAudio auch ohne laufende Sitzung
funktioniert. 

> **Wichtig:** In der Vorlage `audio-pi.service` ist `Environment=FLASK_SECRET_KEY=__CHANGE_ME__`
> als Platzhalter hinterlegt. Wer die Unit manuell installiert, muss diesen
> Wert vor dem Kopieren durch einen sicheren Schlüssel ersetzen, z. B. via
> `sudo sed -i "s|Environment=FLASK_SECRET_KEY=.*|Environment=FLASK_SECRET_KEY=<dein_schlüssel>|" audio-pi.service`.

Sollte die Unit manuell neu geladen werden müssen, genügt:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now audio-pi.service
```

## Datenbank-Initialisierung

Beim ersten Start legt die Anwendung automatisch die SQLite-Datenbank `audio.db` an, erzeugt sämtliche Tabellen und erstellt den Benutzer `admin`. Ein hart codiertes Standard-Passwort existiert nicht mehr:

- **Eigenes Startpasswort hinterlegen:** Wenn beim allerersten Start die Umgebungsvariable `INITIAL_ADMIN_PASSWORD` gesetzt ist (z. B. `export INITIAL_ADMIN_PASSWORD='DeinSicheresPasswort'` oder als zusätzliche `Environment=`-Zeile in `audio-pi.service`), wird genau dieses Passwort verwendet und gehasht gespeichert.
- **Automatische Generierung:** Ist `INITIAL_ADMIN_PASSWORD` nicht gesetzt, generiert die Anwendung einmalig ein zufälliges 16-Zeichen-Passwort. Der Klartext wird direkt beim Start als Warnung in die Logdatei geschrieben.

Die Logdatei befindet sich im Arbeitsverzeichnis der Anwendung (standardmäßig das Projektverzeichnis, z. B. `/opt/Audio-Pi-Websystem/app.log`). Das zufällig erzeugte Passwort lässt sich danach mit `tail -n 50 app.log` bzw. bei systemd-Installationen mit `sudo tail -n 50 /opt/Audio-Pi-Websystem/app.log` nachvollziehen. Sobald der Eintrag gelesen wurde, sollte die Logdatei vor unbefugtem Zugriff geschützt bzw. bereinigt werden.

> **Wichtig:** Nach der ersten Anmeldung muss das Passwort über die Weboberfläche (Bereich **System → Passwort ändern**) unmittelbar durch ein neues, sicheres Passwort ersetzt werden. Die Datenbank markiert den Benutzer bis zur Änderung als `must_change_password`, wodurch ein Passwortwechsel erzwungen wird.

## Konfiguration

Wichtige Einstellungen können über Umgebungsvariablen angepasst werden:

- `FLASK_SECRET_KEY`: Muss gesetzt sein, sonst startet die Anwendung nicht.
- `FLASK_PORT`: HTTP-Port für Flask (Standard: `80`).
- `DB_FILE`: Pfad zur SQLite-Datenbank (Standard: `audio.db` im Projektverzeichnis).
- `MAX_SCHEDULE_DELAY_SECONDS`: Maximale Verzögerung für Scheduler-Nachläufer.

Weitere Variablen sind im Quelltext dokumentiert. Wird ein Port kleiner 1024
eingesetzt, sind – je nach Startmethode – entsprechende Capabilities oder Root-Rechte notwendig (siehe Hinweise oben).


## Update aus dem Git-Repository

Im Web-Interface gibt es einen **Update**-Button. Nach dem Login kann damit ein
`git pull` ausgeführt werden, um lokale Änderungen aus dem Repository zu holen.
Ein Hinweis informiert über Erfolg oder Fehler.

### Systemsteuerung (Neustart & Shutdown)

Zusätzlich zum Update stehen im selben Bereich zwei Buttons bereit, um einen
Neustart (`sudo reboot`) oder ein Herunterfahren (`sudo poweroff`) über das
Web-Interface auszulösen. Beide Aktionen sind ausschließlich nach erfolgreichem
Login verfügbar und fragen vor dem Absenden per JavaScript nach einer
Bestätigung.

> **Hinweis:** Damit die Kommandos ohne Passwortabfrage funktionieren, muss der
> Benutzer, unter dem Flask läuft (z. B. `pi` oder ein Service-Account), in der
> `sudoers`-Konfiguration entsprechende Regeln besitzen, etwa:
>
> ```bash
> pi ALL=NOPASSWD:/sbin/reboot,/sbin/poweroff
> ```
>
> Die exakten Pfade zu `reboot` bzw. `poweroff` können je nach Distribution
> variieren (`/usr/sbin` vs. `/sbin`).

## Tests

Die Tests laufen mit `pytest`. Nachdem die Abhängigkeiten installiert sind,
(z.B. via `pip install -r requirements.txt`), lassen sich alle Tests einfach per

```bash
pytest
```
ausführen.

## License

Dieses Projekt steht unter der [MIT-Lizenz](LICENSE).
