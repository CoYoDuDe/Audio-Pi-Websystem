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
und richtet den systemd-Dienst direkt ein. Der Schlüssel muss mindestens 32
Zeichen umfassen und mindestens drei Zeichengruppen enthalten
(Großbuchstaben, Kleinbuchstaben, Ziffern, Sonderzeichen). Werte aus CLI oder
Umgebungsvariablen werden identisch validiert; fehlerhafte Angaben führen zum
Abbruch. Mit `--generate-secret` bzw. `INSTALL_GENERATE_SECRET=1` erzeugt der
Installer automatisch einen `secrets.token_urlsafe(48)`-Wert, sobald kein
gültiger Schlüssel bereitsteht. Die Empfehlung der Flask-Entwickler, einen
zufälligen Secret Key zu verwenden, wird damit standardkonform umgesetzt
(siehe [Flask Configuration – SECRET_KEY](https://flask.palletsprojects.com/en/3.0.x/config/#SECRET_KEY)).
Zusätzlich stellt `install.sh` sicher, dass die Datenbank `audio.db` dem
Dienstbenutzer (`$TARGET_USER:$TARGET_GROUP`) gehört und mit `chmod 660`
beschreibbare Rechte erhält, unabhängig davon, ob die Datei neu angelegt oder
bereits vorhanden war.

Das Logfile `app.log` wird standardmäßig mit `chmod 666` angelegt, damit sowohl
der Dienstbenutzer als auch andere Mitglieder der Zielgruppe den Inhalt lesen
und fortschreiben können. Über `INSTALL_LOG_FILE_MODE` bzw. `--log-file-mode`
lässt sich der Wert bei Bedarf anpassen. Befindet sich die optionale Vorlage
`scripts/logrotate/audio-pi` im Repository, kopiert der Installer beim Setup
eine angepasste Variante nach `/etc/logrotate.d/audio-pi`. Die Vorlage liest den
gesetzten Modus automatisch aus (`create <MODE> …`) und bleibt damit auch bei
individuellen Werten konsistent, ohne dass sie manuell editiert werden muss.
Dadurch wird die Rotation des Logfiles inklusive korrekter Besitzer- und
Rechtevergabe automatisch eingerichtet.

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

### I²C vorbereiten

Die Einrichtung der I²C-Schnittstelle folgt der offiziellen Raspberry-Pi-Dokumentation
(["Enable I2C" Schritt-für-Schritt-Anleitung](https://www.raspberrypi.com/documentation/computers/configuration.html#i2c)
und den Device-Tree-Kurzbefehlen für `dtparam=i2c_arm=on`). `install.sh` versucht zunächst,
wie gewohnt `raspi-config nonint do_i2c 0` auszuführen. Fehlt `raspi-config` (z. B. auf Desktop-
oder Minimal-Installationen), aktiviert das Skript den Bus automatisch über die passende
`config.txt` und trägt – sofern noch nicht vorhanden – `dtparam=i2c_arm=on` ein. Unterstützt
werden sowohl `/boot/firmware/config.txt` (Debian Bookworm und neuer) als auch
`/boot/config.txt`. Anschließend bleibt die Pflege von `/etc/modules` unverändert bestehen,
damit `i2c-dev` beim nächsten Start geladen wird. Zusätzlich installiert das Skript direkt die
Pakete `python3-smbus` und `i2c-tools` über APT, damit sowohl die Python-Bindings als auch
Diagnosewerkzeuge wie `i2cdetect` sofort verfügbar sind.

### Internet-Zeitsynchronisation mit systemd-timesyncd

Audio-Pi-Control setzt für die Synchronisierung der Systemzeit ab sofort ausschließlich auf die systemd-
Komponenten. Die Anwendung deaktiviert die NTP-Steuerung kurzzeitig mit `timedatectl set-ntp false`,
aktiviert sie unmittelbar wieder (`timedatectl set-ntp true`) und stößt anschließend einen Neustart von
`systemd-timesyncd` an (`systemctl restart systemd-timesyncd`). Ein separates Paket wie `ntpdate` wird
dadurch nicht mehr benötigt.

Für manuelle Prüfungen stehen folgende Kommandos bereit:

```bash
sudo systemctl status systemd-timesyncd
sudo timedatectl timesync-status
```

Fehlt der Dienst, kann er auf Debian/Ubuntu-Systemen mit

```bash
sudo apt-get install -y systemd-timesyncd
sudo systemctl enable --now systemd-timesyncd
```

nachinstalliert und aktiviert werden. Auf Nicht-systemd-Distributionen ist diese Funktion nicht verfügbar –
die Weboberfläche weist in diesem Fall auf fehlende Kommandos hin.

#### Migration bestehender Installationen

Bestehende Umgebungen können ohne Neuinstallation auf die neue Paketbasis umgestellt werden:

```bash
sudo apt-get update
sudo apt-get install -y python3-smbus i2c-tools
source /opt/Audio-Pi-Websystem/venv/bin/activate
pip uninstall -y smbus || true
pip install --upgrade -r requirements.txt
```

Das Projekt importiert weiterhin `smbus`; falls das Systemmodul nicht verfügbar ist, greift die
Anwendung nun automatisch auf `smbus2` zurück. Für reine Entwicklungsumgebungen ohne Raspberry-Pi-
Pakete reicht es daher aus, `pip install -r requirements.txt` auszuführen. Im Testmodus
(`TESTING=1`) bleibt der Fallback deaktiviert, kann bei Bedarf aber mit
`AUDIO_PI_ALLOW_SMBUS2_FALLBACK=1` erzwungen werden.

**Beispiele für automatisierte Aufrufe:**

```bash
# Vollautomatische Installation ohne Access Point, Werte per Umgebungsvariablen
sudo INSTALL_GENERATE_SECRET=1 \
     INSTALL_RTC_MODE=auto \
     INSTALL_RTC_ACCEPT_DETECTION=yes \
     INSTALL_AP_SETUP=no \
     HAT_MODEL=hifiberry_dacplus \
     bash install.sh --non-interactive --generate-secret

# Gleiche Installation mit expliziten CLI-Flags inkl. Access-Point-Konfiguration
sudo bash install.sh \
     --generate-secret \
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
  Das Secret muss die genannten Mindestanforderungen erfüllen; alternativ erzeugt
  `--generate-secret` bzw. `INSTALL_GENERATE_SECRET=1` automatisch einen starken
  Schlüssel (identisch zu `secrets.token_urlsafe(48)`).
- `--flask-port` / `INSTALL_FLASK_PORT` legen den HTTP-Port für Gunicorn/Flask fest (Standard: `80`).
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

> 💡 Mit `./install.sh --dry-run` (kombinierbar mit `--flask-port` oder den
> entsprechenden `INSTALL_…`-Variablen) lässt sich die Abschlussausgabe inklusive
> des ermittelten Ports prüfen, ohne Änderungen am System vorzunehmen.

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

`install.sh` kopiert und konfiguriert `audio-pi.service` automatisch. Dabei wird der während der Installation abgefragte `FLASK_SECRET_KEY` eingetragen; ohne gültigen Schlüssel startet der Dienst nicht. Der HTTP-Port landet – standardmäßig als `FLASK_PORT=80`, bei Bedarf entsprechend der Installer-Option `--flask-port` bzw. `INSTALL_FLASK_PORT` – ebenfalls direkt in der Unit. Dank `AmbientCapabilities=CAP_NET_BIND_SERVICE` kann der nicht-root-Benutzer `pi` weiterhin Port 80 binden, ohne zusätzliche weitreichende Privilegien zu erhalten. Statt direkt `python
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

> **Hinweis:** Die Vorlage `audio-pi.service` bindet Secrets über `EnvironmentFile=/etc/audio-pi/audio-pi.env` ein. `install.sh`
> legt `/etc/audio-pi` mit Modus `0750` an, schreibt `FLASK_SECRET_KEY=<wert>` in `audio-pi.env` (Modus `0640`, Besitzer `root`,
> Gruppe des Dienstkontos) und aktualisiert die Unit automatisch. Dadurch bleibt der Schlüssel außerhalb der Unit-Datei und kann
> bei Bedarf über das Environment-File rotiert werden.

### Deployment

- **Systemd-Service aktualisieren:** Nach Code- oder Konfigurationsänderungen
  genügt `sudo systemctl restart audio-pi.service`. Für reine
  Konfigurationsupdates der Gunicorn-Parameter empfiehlt sich `sudo systemctl
  reload audio-pi.service`, wodurch Gunicorn einen Hot-Reload per HUP erhält.
- **Gehärtete Defaults:** Die Unit `audio-pi.service` beschränkt sich jetzt beim
  Capability-Set konsequent auf `CapabilityBoundingSet=CAP_NET_BIND_SERVICE`
  (gleichzeitig als `AmbientCapabilities` gesetzt) und kombiniert dies mit
  `NoNewPrivileges=yes`, `RestrictSUIDSGID=yes`,
  `SystemCallFilter=@system-service`, `RestrictNamespaces=yes`,
  `ProtectSystem=strict`, `ReadWritePaths=/opt/Audio-Pi-Websystem` sowie einem
  eingeschränkten `RestrictAddressFamilies`-Set. Laufzeitdaten bleiben dadurch
  auf das Projektverzeichnis beschränkt, während die Anwendung dennoch Port 80
  ohne Root-Rechte binden kann.
- **Polkit-Standard (sudo-frei):** Der Installer aktiviert ab sofort
  `AUDIO_PI_DISABLE_SUDO=1` und legt automatisch
  `/etc/polkit-1/rules.d/49-audio-pi.rules` an. Die Regel gestattet dem
  Dienstkonto exakt die benötigten Aktionen (`systemctl start/stop/restart`
  für `hostapd`, `dnsmasq`, `systemd-timesyncd`, `audio-pi`,
  `systemctl reboot/poweroff` sowie `timedatectl set-time/set-ntp`). Dadurch
  funktionieren AP-Umschaltung, Zeitsync und Neustart ohne `sudo`-Wrapper.
  Das Regelverzeichnis `/etc/polkit-1/rules.d` setzt der Installer auf
  `root:root` mit Modus `0755`, die Regeldatei selbst auf `0644`, damit der
  `polkitd`-Dienst sie lesen kann. Bestehende Installationen korrigiert das
  Skript automatisch; die effektiven Rechte lassen sich z. B. mit
  `stat /etc/polkit-1/rules.d/49-audio-pi.rules` überprüfen.
  Wer explizit beim alten Verhalten bleiben muss (z. B. in restriktiven
  Umgebungen ohne Polkit), setzt `INSTALL_DISABLE_SUDO=0` während der
  Installation oder trägt `AUDIO_PI_DISABLE_SUDO=0` in die Unit ein – dann
  werden weiterhin klassische `sudo`-Aufrufe verwendet.
- **Migration bestehender Installationen:** Nach dem Update die Unit-Datei
  neu einlesen und den Dienst neu starten:
  `sudo systemctl daemon-reload && sudo systemctl restart audio-pi.service`.
  Prüfe anschließend, ob `/etc/polkit-1/rules.d/49-audio-pi.rules` mit deinen
  bestehenden Polkit-Konfigurationen harmoniert.
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

#### Geheimnisse & Environment-File

- `install.sh` hinterlegt den `FLASK_SECRET_KEY` ausschließlich in `/etc/audio-pi/audio-pi.env`.
  Die Datei gehört `root` und der Dienstgruppe des Services (z. B. `root:pi`) und ist mit Modus `0640` geschützt.
- Das Verzeichnis `/etc/audio-pi` wird dabei explizit als `root:<Dienstgruppe>` mit Modus `0750` erzeugt;
  bestehende Installationen korrigiert das Skript automatisch per `sudo chown root:<Gruppe> /etc/audio-pi`.
- Beim Installer gilt eine Mindestlänge von 32 Zeichen sowie der Mix aus mindestens drei
  Zeichengruppen (Großbuchstaben, Kleinbuchstaben, Ziffern, Sonderzeichen). Ungültige
  Werte aus CLI oder ENV führen zum Abbruch.
- `--generate-secret` bzw. `INSTALL_GENERATE_SECRET=1` erzeugen automatisch ein Secret
  via `secrets.token_urlsafe(48)` und übernehmen den Wert für Dienst und Shell.
- Das Installer-Skript ergänzt die Datei `.profile` im Home-Verzeichnis des Dienstbenutzers um die Zeile
  `if [ -f "/etc/audio-pi/audio-pi.env" ]; then . "/etc/audio-pi/audio-pi.env"; fi`,
  damit interaktive Shells denselben Speicherort referenzieren, ohne das Secret direkt in der Shell-Konfiguration abzulegen.
- Rechte lassen sich nach der Installation mit `stat /etc/audio-pi/audio-pi.env` prüfen;
  nur Root und Mitglieder der Dienstgruppe erhalten Leserechte.

überprüfen.

Sollte die Unit manuell neu geladen werden müssen, genügt:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now audio-pi.service
```

Nach einem Update der Unit empfiehlt sich außerdem ein schneller Konsistenz-
Check:

```bash
sudo systemd-analyze verify /etc/systemd/system/audio-pi.service
sudo systemctl daemon-reload
sudo systemctl restart audio-pi.service
```

Damit lassen sich auch Bestandsinstallationen problemlos auf die neuen
Sicherheitsvorgaben migrieren.

### Dateipfade & Rechte

`install.sh` legt das Upload-Verzeichnis `uploads/` und die Logdatei `app.log` jetzt automatisch mit den Rechten des Dienstbenutzers an. Sowohl Besitzer als auch Gruppe werden auf den während der Installation gewählten Account (`$TARGET_USER:$TARGET_GROUP`) gesetzt, damit der systemd-Dienst ohne zusätzliche Privilegien schreiben kann. Standardmäßig gelten dabei folgende Modi:

- `uploads/`: `chmod 775` (Schreib-/Leserechte für Benutzer und Gruppe, nur Lesen für andere)
- `app.log`: `chmod 666` (Schreib-/Leserechte für alle Beteiligten, damit auch zusätzliche Tools ohne sudo anhängen können)

Die Logrotate-Vorlage `scripts/logrotate/audio-pi` wird während der Installation automatisch nach `/etc/logrotate.d/audio-pi` kopiert. Sie übernimmt den ermittelten Modus (`create <MODE> …`) und sorgt so dafür, dass rotierte Logfiles weiterhin exakt mit dem in `INSTALL_LOG_FILE_MODE`/`--log-file-mode` hinterlegten Wert erzeugt werden. Ohne eigene Vorgabe bleibt der Standard `666` aktiv, auch nach einer Rotation.

Wer ein restriktiveres Profil benötigt, kann die Modi bereits beim Installationslauf setzen, z. B. `INSTALL_UPLOAD_DIR_MODE=750` und `INSTALL_LOG_FILE_MODE=640` für ausschließlichen Gruppen-/Benutzerzugriff. Die Angaben müssen als oktale chmod-Werte (drei oder vier Stellen) übergeben werden. Die Logrotate-Vorlage muss dafür nicht angepasst werden – sie übernimmt den gewählten Wert automatisch.

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

Der Zugriff lässt sich anschließend mit `stat uploads app.log` prüfen; die Ausgabe spiegelt den gewählten Modus wider (Standard `775`/`666` oder die individuell gesetzten Werte).

## Datenbank-Initialisierung

Beim ersten Start legt die Anwendung automatisch die SQLite-Datenbank `audio.db` an, erzeugt sämtliche Tabellen und erstellt den Benutzer `admin`. Ein hart codiertes Standard-Passwort existiert nicht mehr:

- **Eigenes Startpasswort hinterlegen:** Wenn beim allerersten Start die Umgebungsvariable `INITIAL_ADMIN_PASSWORD` gesetzt ist (z. B. `export INITIAL_ADMIN_PASSWORD='DeinSicheresPasswort'` oder als zusätzliche `Environment=`-Zeile in `audio-pi.service`), wird genau dieses Passwort verwendet und gehasht gespeichert.
- **Automatische Generierung:** Ist `INITIAL_ADMIN_PASSWORD` nicht gesetzt, generiert die Anwendung einmalig ein zufälliges 16-Zeichen-Passwort. Statt es im Log festzuhalten, wird der Klartext ausschließlich in der Datei `initial_admin_password.txt` mit Rechten `0600` neben der Datenbank (`audio.db`) abgelegt. Über die Variable `INITIAL_ADMIN_PASSWORD_FILE` lässt sich der Dateiname bzw. Zielpfad (relativ oder absolut) anpassen.
- **Startpasswort abrufen:** Nach dem ersten Start kann das Passwort mit `sudo cat /opt/Audio-Pi-Websystem/initial_admin_password.txt` (bzw. anhand des unter `INITIAL_ADMIN_PASSWORD_FILE` angegebenen Pfads) ausgelesen werden. Anschließend sollte die Datei gelöscht oder in ein sicheres Geheimnis-Backend verschoben werden.
- **Passwort zurücksetzen:** Geht das Administrator-Passwort verloren, lässt es sich offline über SQLite und `werkzeug.security.generate_password_hash` neu setzen. Beispiel (Pfad anpassen, idealerweise innerhalb der virtuellen Umgebung ausführen):

```bash
sudo /opt/Audio-Pi-Websystem/venv/bin/python - <<'PY'
import sqlite3
from werkzeug.security import generate_password_hash

db_path = "/opt/Audio-Pi-Websystem/audio.db"
new_password = "NeuesSicheresPasswort"

conn = sqlite3.connect(db_path)
conn.execute(
    "UPDATE users SET password=?, must_change_password=1 WHERE username='admin'",
    (generate_password_hash(new_password),),
)
conn.commit()
conn.close()
PY
```

Beim nächsten Login erzwingt das System erneut eine Passwortänderung über die Weboberfläche.

> **Wichtig:** Nach der ersten Anmeldung muss das Passwort über die Weboberfläche (Bereich **System → Passwort ändern**) unmittelbar durch ein neues, sicheres Passwort ersetzt werden. Die Datenbank markiert den Benutzer bis zur Änderung als `must_change_password`, wodurch ein Passwortwechsel erzwungen wird.

## Konfiguration

Wichtige Einstellungen können über Umgebungsvariablen angepasst werden:

- `FLASK_SECRET_KEY`: Muss gesetzt sein, sonst startet die Anwendung nicht.
- `FLASK_PORT`: HTTP-Port für Flask (Standard: `80`).
- `DB_FILE`: Pfad zur SQLite-Datenbank (Standard: `audio.db` im Projektverzeichnis).
- `AUDIO_PI_MAX_UPLOAD_MB`: Maximale Dateigröße pro Upload in Megabyte (Standard: `100`). Wird das Limit überschritten, bricht die Anwendung den Upload ab und zeigt auf der Weboberfläche einen Hinweis an.
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
