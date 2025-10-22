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

## GPIO-Taster für Wiedergabe & Bluetooth

Das System unterstützt jetzt physische Taster am Raspberry Pi. Ein eigener Monitor in `hardware/buttons.py`
überwacht per `lgpio` konfigurierbare GPIO-Eingänge und löst bei erkannten Flanken die bekannten Aktionen
aus (`play_item`, `stop_playback`, `enable_bluetooth`, `disable_bluetooth`). Damit lassen sich zum Beispiel
Wiedergabe und Bluetooth ohne Web-UI schalten. Die wichtigsten Umgebungsvariablen:

| Variable | Bedeutung |
|----------|-----------|
| `GPIO_BUTTON_PLAY_PIN` | GPIO-Nummer des Play-Tasters; benötigt zusätzlich `GPIO_BUTTON_PLAY_ITEM_TYPE` (`file`/`playlist`) und `GPIO_BUTTON_PLAY_ITEM_ID`. Optional: `GPIO_BUTTON_PLAY_DELAY_SEC`, `GPIO_BUTTON_PLAY_VOLUME_PERCENT`. |
| `GPIO_BUTTON_STOP_PIN` | GPIO-Nummer für das sofortige Stoppen der Wiedergabe. |
| `GPIO_BUTTON_BT_ON_PIN` / `GPIO_BUTTON_BT_OFF_PIN` | GPIO-Pins zum Ein- bzw. Ausschalten von Bluetooth. |
| `GPIO_BUTTON_DEFAULT_PULL`, `GPIO_BUTTON_<AKTION>_PULL` | Pull-Up/-Down je Taster (`up`, `down`, `none`). Ohne Angabe wird `up` verwendet. |
| `GPIO_BUTTON_DEFAULT_EDGE`, `GPIO_BUTTON_<AKTION>_EDGE` | Flankenerkennung (`falling`, `rising`, `both`). Standard ist `falling`. |
| `GPIO_BUTTON_DEFAULT_DEBOUNCE_MS`, `GPIO_BUTTON_<AKTION>_DEBOUNCE_MS` | Entprellzeit in Millisekunden (Standard: 150 ms). |
| `GPIO_BUTTON_POLL_INTERVAL_SEC` | Optionales Abtastintervall des Monitors (Standard: 0,01 s). |
| `GPIO_BUTTON_CHIP`, `GPIO_BUTTON_CHIP_CANDIDATES` | (Optional) überschreibt die automatisch ermittelten `gpiochip`-IDs. |

Die Taster werden beim Start automatisch initialisiert, nutzen Pull-Ups/Pull-Downs nach obiger Konfiguration
und werden beim Shutdown sauber freigegeben. Der Monitor läuft in einem eigenen Thread, entprellt softwareseitig
und startet die hinterlegten Aktionen jeweils in separaten Worker-Threads, damit die GPIO-Überwachung reaktiv
bleibt.

Im Bereich "System" der Weboberfläche befinden sich Buttons zum Ein- und
Ausschalten von Bluetooth sowie Schaltflächen zum geordneten Neustart oder
Herunterfahren des Raspberry Pi.

## Hardware / Verkabelung

| Signal / Funktion                     | GPIO (BCM) | Hinweise |
|---------------------------------------|------------|----------|
| Endstufen-Freigabe (Standardbetrieb)  | 17         | Wird vom Websystem automatisch geschaltet. High = Endstufe an, Low = aus. |
| Endstufen-Freigabe (HiFiBerry Amp2)   | 18         | Optional: Einige HiFiBerry-HATs (Amp2) nutzen GPIO18 als Enable-Pin und können hierüber aktiviert werden. |
| I²C SDA1                              | 2          | Frei verfügbar, z.&nbsp;B. für RTC-Module oder Sensoren. |
| I²C SCL1                              | 3          | Frei verfügbar und bereits für die optionale RTC-Unterstützung vorgesehen. |

Weitere GPIOs (z.&nbsp;B. 4, 5, 6, 12, 13, 16, 19, 26) bleiben unbelegt und können für Taster,
Relais oder andere Erweiterungen genutzt werden, solange sie nicht mit dem konfigurierten
Endstufen-Pin kollidieren. Die Weboberfläche prüft beim Speichern von Taster- oder
Verstärker-Pins automatisch auf Konflikte und fordert bei Überschneidungen zur Anpassung auf.

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

Seit dem aktuellen Update lassen sich alle Dialoge per CLI-Flag oder per
Umgebungsvariablen mit dem Präfix `INSTALL_…` vorbelegen. Sobald alle
Pflichtwerte gesetzt sind, läuft `install.sh` vollständig automatisch. Das
Kommando `./install.sh --help` listet alle verfügbaren Optionen auf.

Die Paketinstallation selbst läuft nun komplett unattended. `install.sh` nutzt
`apt-get` mit `DEBIAN_FRONTEND=noninteractive` sowie den dpkg-Optionen
`--force-confdef` und `--force-confold`, damit Upgrades ohne Rückfragen
durchlaufen. Über die Variablen `INSTALL_APT_FRONTEND`,
`INSTALL_APT_DPKG_OPTIONS` und `INSTALL_APT_LOG_FILE` lässt sich das Verhalten
an eigene Anforderungen anpassen (z. B. anderes Frontend, angepasste dpkg-Flags
oder ein alternativer Log-Pfad für die Installationsprotokolle).

**Beispiele für automatisierte Aufrufe:**

```bash
# Vollautomatische Installation ohne Access Point, Werte per Umgebungsvariablen
sudo INSTALL_FLASK_SECRET_KEY="$(openssl rand -hex 32)" \
     INSTALL_RTC_MODE=auto \
     INSTALL_RTC_ACCEPT_DETECTION=yes \
     INSTALL_AP_SETUP=no \
     HAT_MODEL=hifiberry_dacplus \
     bash install.sh --non-interactive

# Gleiche Installation mit expliziten CLI-Flags inkl. Access-Point-Konfiguration
sudo bash install.sh \
     --flask-secret-key "$(openssl rand -hex 32)" \
     --rtc-mode ds3231 --rtc-accept-detection yes \
     --hat-model hifiberry_amp2 \
     --ap --ap-ssid AudioPiAP --ap-passphrase "AudioPiSecure!" \
     --ap-country DE --ap-interface wlan0 \
     --ap-ipv4 192.168.50.1 --ap-prefix 24 \
     --ap-dhcp-start 192.168.50.50 --ap-dhcp-end 192.168.50.150 \
     --ap-dhcp-lease 24h --ap-wan eth0 \
     --non-interactive
```

Dabei gilt:

- `--flask-secret-key` / `INSTALL_FLASK_SECRET_KEY` setzen das notwendige Flask-Secret.
- `--rtc-mode` (`auto`, `pcf8563`, `ds3231`, `skip`) und `--rtc-accept-detection`
  steuern die RTC-Erkennung; Adressen (`--rtc-addresses`) und Overlays
  (`--rtc-overlay`) lassen sich ebenfalls vorbelegen.
- HAT-Voreinstellungen können über `--hat-*` Flags oder die bekannten Variablen
  (`HAT_MODEL`, `HAT_DTOOVERLAY`, `HAT_SINK_NAME`, …) erfolgen.
- Für den WLAN-Access-Point existieren Flags wie `--ap-ssid`,
  `--ap-passphrase`, `--ap-channel`, `--ap-country`, `--ap-interface`,
  `--ap-ipv4`, `--ap-prefix`, `--ap-dhcp-start`, `--ap-dhcp-end`,
  `--ap-dhcp-lease` und `--ap-wan`. Ohne vollständige Angaben wechselt der
  Installer automatisch in den Dialogmodus oder – bei `--non-interactive` –
  bricht mit einer passenden Fehlermeldung ab.

> **Neu:** Der Installer übernimmt Secrets inklusive Sonderzeichen (z. B. `/`, `&`, Leerzeichen)
> sowie führender/abschließender Leerzeichen unverändert sowohl für den interaktiven Start
> als auch für den systemd-Dienst.
> **Neu:** Installationen in Pfaden mit Leerzeichen, `&`, `|` oder Backslashes werden bei der
> systemd-Unit jetzt automatisch korrekt eingetragen.
> **Neu:** Bereits vorhandene `export FLASK_SECRET_KEY=…`-Zeilen in `~/.profile` werden gezielt
> ersetzt, statt dass zusätzliche Einträge angehängt werden.

**2. Umgebung einrichten**
```bash
bash setup_env.sh
```

> 💡 Für Tests und Entwicklung installiert `./setup_env.sh --dev` zusätzlich die Pakete aus
> `dev-requirements.txt` (z. B. `pytest`). Alternativ lässt sich eine bestehende Umgebung mit
> `pip install -r dev-requirements.txt` erweitern; die Datei referenziert automatisch
> `requirements.txt`, sodass alle Laufzeit- und Entwicklungsabhängigkeiten konsistent bleiben.

**3. Virtuelle Umgebung aktivieren**
```bash
source venv/bin/activate
```

**4. (Optional) Entwicklungsserver starten**
Der Produktivbetrieb läuft ausschließlich über Gunicorn (siehe Abschnitt
„Deployment“). Für lokale Tests lässt sich der integrierte Flask-Server weiter
verwenden, allerdings nur nach expliziter Freigabe per Umgebungsschalter.

```bash
export FLASK_SECRET_KEY="ein_sicherer_schluessel"
# optional: Port ändern (Standard ist 80)
export FLASK_PORT=8080
export AUDIO_PI_USE_DEV_SERVER=1
python app.py
```

Ohne `AUDIO_PI_USE_DEV_SERVER=1` beendet sich `python app.py` sofort mit einem
Hinweis auf den Gunicorn-Dienst. Der integrierte Server eignet sich weiterhin
zum Debugging; Scheduler, Bluetooth-Monitor und andere Hintergrundthreads
starten wie gewohnt.

> **Hinweis:** Für Ports <1024 benötigt der ausführende Benutzer die Capability
> `CAP_NET_BIND_SERVICE`. Im Produktivbetrieb übernimmt der systemd-Dienst die
> Capability-Zuweisung automatisch.

### Automatischer Start (systemd)

`install.sh` kopiert und konfiguriert `audio-pi.service` automatisch. Dabei wird der während der Installation abgefragte `FLASK_SECRET_KEY` eingetragen; ohne gültigen Schlüssel startet der Dienst nicht. Die
Service-Datei setzt außerdem `FLASK_PORT=80` und stattet den Dienst dank
`AmbientCapabilities=CAP_NET_BIND_SERVICE` mit den nötigen Rechten aus, damit
der nicht-root-Benutzer `pi` auch Port 80 binden kann. Statt direkt `python
app.py` aufzurufen, startet systemd jetzt Gunicorn aus der virtuellen Umgebung
(`ExecStart=/opt/Audio-Pi-Websystem/venv/bin/gunicorn --config ...`). Das sorgt
für mehrere Worker-Threads, optionale Hot-ReLoads (`systemctl reload` sendet
ein HUP) und sauberere Logs (`capture_output` leitet alles an `journalctl`
weiter). Durch `ExecStartPre=/bin/sleep 10` wartet der Dienst nach dem Booten
zehn Sekunden, bevor Gunicorn mit der App initialisiert wird. Zusätzlich
richtet die Service-Datei via `RuntimeDirectory=audio-pi` ein privates
Laufzeitverzeichnis ein und setzt `XDG_RUNTIME_DIR=/run/audio-pi`, damit
PulseAudio auch ohne laufende Sitzung funktioniert. Systemd erzeugt das
Verzeichnis bei jedem Start neu; das Installationsskript ergänzt ergänzend eine
`tmpfiles.d`-Regel, sodass `/run/audio-pi` und `/run/user/<UID>` bereits beim
Boot mit den passenden Rechten anliegen. `TimeoutStartSec`, `TimeoutStopSec`
und `RestartSec` sorgen für robuste Neustarts bei fehlerhaften Deployments oder
unerwarteten Ausstiegen.

Die Gunicorn-Optionen lassen sich zentral in `gunicorn.conf.py` anpassen. Der
Standard liest den Port weiterhin aus `FLASK_PORT` und verwendet einen
Thread-basierten Worker (`gthread`), damit mehrere parallele HTTP-Anfragen auf
kleinen Raspberry-Pi-Boards zuverlässig abgearbeitet werden. Über die
Umgebungsvariablen `AUDIO_PI_GUNICORN_*` (z. B. `AUDIO_PI_GUNICORN_WORKERS`)
lässt sich das Verhalten bei Bedarf weiter abstimmen.

> **Wichtig:** In der Vorlage `audio-pi.service` ist `Environment="FLASK_SECRET_KEY=__CHANGE_ME__"`
> als Platzhalter hinterlegt. Der Installer ersetzt diesen weiterhin automatisch
> durch `Environment="FLASK_SECRET_KEY=<dein_schlüssel>"` und maskiert dabei
> mindestens doppelte Anführungszeichen (`"`), Backslashes (`\`) und Dollarzeichen (`$`).
> Wer die Unit manuell installiert, sollte dieselbe Maskierung verwenden, z. B. via
> `sudo sed -i "s|^Environment=.*FLASK_SECRET_KEY=.*|Environment=\"FLASK_SECRET_KEY=mein\ \"Secret\"\"|" audio-pi.service`.

Nach der Installation lässt sich das gesetzte Secret – auch mit Leerzeichen – per

```bash
systemctl show --property=Environment audio-pi.service
```

### Deployment

- **Systemd-Service aktualisieren:** Nach Code- oder Konfigurationsänderungen
  genügt `sudo systemctl restart audio-pi.service`. Für reine
  Konfigurationsupdates der Gunicorn-Parameter empfiehlt sich `sudo systemctl
  reload audio-pi.service`, wodurch Gunicorn einen Hot-Reload per HUP erhält.
- **Gunicorn-Konfiguration:** `gunicorn.conf.py` nutzt die offiziellen Flask-
  Empfehlungen für produktive WSGI-Server. Weitere Optionen können gemäß der
  [Flask-Dokumentation zu WSGI-Servern](https://flask.palletsprojects.com/en/latest/deploying/wsgi-standalone/#gunicorn)
  ergänzt werden.
- **Skalierung:** Für Szenarien mit vielen gleichzeitigen Clients lassen sich
  zusätzliche Worker (`AUDIO_PI_GUNICORN_WORKERS`) oder Threads
  (`AUDIO_PI_GUNICORN_THREADS`) aktivieren. Die Standardwerte sind bewusst
  konservativ gewählt, um auch auf kleineren Raspberry-Pi-Modellen stabil zu
  bleiben.
- **Integration in bestehende Setups:** Dank systemd eignet sich der Dienst für
  Supervisor-Lösungen wie SetupHelper (kwindrem) oder Venus-OS-basierte
  Erweiterungen. Die Aktivierung erfolgt wie gewohnt per `sudo systemctl enable
  --now audio-pi.service`.

überprüfen.

Sollte die Unit manuell neu geladen werden müssen, genügt:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now audio-pi.service
```

### Dateipfade & Rechte

`install.sh` legt das Upload-Verzeichnis `uploads/` und die Logdatei `app.log` jetzt automatisch mit den Rechten des Dienstbenutzers an. Sowohl Besitzer als auch Gruppe werden auf den während der Installation gewählten Account (`$TARGET_USER:$TARGET_GROUP`) gesetzt, damit der systemd-Dienst ohne zusätzliche Privilegien schreiben kann. Standardmäßig gelten dabei folgende Modi:

- `uploads/`: `chmod 775` (Schreib-/Leserechte für Benutzer und Gruppe, nur Lesen für andere)
- `app.log`: `chmod 660` (Schreib-/Leserechte für Benutzer und Gruppe, kein Zugriff für andere)

Wer ein vollständig geschlossenes System betreibt, kann die Werte bereits beim Installationslauf anpassen, z. B. `INSTALL_UPLOAD_DIR_MODE=750` und `INSTALL_LOG_FILE_MODE=640` für ausschließlichen Gruppen-/Benutzerzugriff. Die Angaben müssen als oktale chmod-Werte (drei oder vier Stellen) übergeben werden:

```bash
sudo INSTALL_FLASK_SECRET_KEY="$(openssl rand -hex 32)" \
     INSTALL_UPLOAD_DIR_MODE=750 \
     INSTALL_LOG_FILE_MODE=640 \
     INSTALL_AP_SETUP=no \
     bash install.sh --non-interactive
```

Damit PulseAudio ohne Desktop-Sitzung verfügbar bleibt, ergänzt das Installationsskript den ausgewählten Dienstbenutzer automatisch um die Gruppen `pulse`, `pulse-access` und `audio`. Dadurch kann der Dienst sowohl das PulseAudio-Socket-Verzeichnis als auch das ALSA-Backend direkt verwenden.

Alle beteiligten Prozesse laufen über dieselben Account-Daten: `audio-pi.service` setzt `User=` und `Group=` auf den oben genannten Benutzer bzw. dessen Primärgruppe, sodass Schreibrechte für Uploads und Logfiles konsistent bleiben.

Zusätzliche Benutzer (z. B. für SFTP-Transfers) werden sauber über Gruppenrechte eingebunden. Ermittle zunächst die Primärgruppe des Dienstkontos und füge danach den gewünschten Benutzer hinzu – die Upload- und Log-Rechte greifen damit automatisch:

```bash
SERVICE_GROUP=$(id -gn <dienstbenutzer>)
sudo usermod -aG "$SERVICE_GROUP" <dein_benutzername>
```

Der Zugriff lässt sich anschließend mit `stat uploads app.log` prüfen; Schreiben ist ausschließlich für den Dienstaccount und Mitglieder der vergebenen Gruppe erlaubt.

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

### WLAN-Access-Point

Der Installer richtet auf Wunsch weiterhin `hostapd` und `dnsmasq` ein, fragt
seit dem Update zusätzlich nach dem gewünschten Subnetz-Präfix (Standard: `/24`)
und setzt die Adresse unmittelbar per `ip addr replace` auf dem gewählten
WLAN-Interface. Damit der Pi die Adresse auch nach einem Neustart behält,
schreibt das Skript einen markierten Abschnitt in `/etc/dhcpcd.conf` und legt
vorher automatisch ein Backup mit Zeitstempel an. Der Block sieht beispielsweise
so aus:

```
# Audio-Pi Access Point configuration
interface wlan0
static ip_address=192.168.50.1/24
nohook wpa_supplicant
# Audio-Pi Access Point configuration end
```

Wer ein anderes Subnetz oder eine andere Präfix-Länge benötigt, kann die Werte
bereits während der Installation anpassen oder die erzeugte Konfiguration im
Nachgang manuell editieren (z. B. `/etc/dhcpcd.conf` und
`/etc/dnsmasq.d/audio-pi.conf`). Nach Änderungen empfiehlt sich ein Neustart des
`dhcpcd`-Dienstes bzw. ein Reboot, damit alle Komponenten die neuen Einstellungen
übernehmen.

> 💡 Für automatisierte Setups lassen sich alle Access-Point-Parameter per
> `INSTALL_AP_*` Variablen oder `--ap-*` Flags vorkonfigurieren. Zusammen mit
> `--ap` und `--non-interactive` entfällt jede manuelle Eingabe; fehlt ein Pflichtwert,
> bricht der Installer mit einer Fehlermeldung ab.

> **Hinweis:** Da aktuelle Raspberry-Pi-Images `hostapd` und teilweise auch `dnsmasq`
> standardmäßig maskieren, hebt der Installer bestehende Masken automatisch per
> `systemctl unmask` auf, bevor die Dienste mit `systemctl enable --now` aktiviert
> werden.


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
