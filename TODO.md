# TODO / technische Baustellen

## 1. Architektur aufteilen

`app.py` ist funktional stark, aber zu groß geworden. Aktuell bündelt die Datei Web-Routen, Auth, Datenbankzugriff, Audio, Bluetooth, Netzwerk, RTC, GPIO, Scheduler, Hintergrundthreads, Settings, Uploads und Systemkommandos.

Warum das problematisch ist:

- Änderungen werden riskanter.
- Fehleranalyse wird schwerer.
- Tests werden mühsamer.
- Neue Features erhöhen die Wahrscheinlichkeit, unbeabsichtigt andere Bereiche zu beschädigen.

Wichtigster Verbesserungsvorschlag: in Module zerlegen, zum Beispiel:

- `services/audio.py`
- `services/bluetooth.py`
- `services/network.py`
- `services/rtc.py`
- `services/scheduler.py`
- `routes/auth.py`
- `routes/settings.py`
- `routes/playback.py`
- `db.py` / `models.py`

Das ist die größte strukturelle Baustelle.

## 2. Web-Update absichern

Die Web-UI kann aktuell ein Update per `git pull` ausführen. Das ist praktisch, aber für stabilen Betrieb riskant:

- Ein fehlerhafter Pull kann das laufende System beschädigen.
- Rollback ist unklar.
- Die App aktualisiert sich selbst während des Betriebs.
- Webzugriff ist direkt an Codeänderungen gekoppelt.

Für Test-/Bastelbetrieb ist das okay. Für Dauerbetrieb besser:

- manuelles Update-Skript,
- release-basierte Updates,
- Backup und Rollback vor dem Pull.

## 3. HTTPS und Netzabsicherung

Login und CSRF sind vorhanden, aber reines HTTP ist nur in einem vertrauenswürdigen LAN/AP akzeptabel.

Sinnvolle Ergänzungen:

- Reverse Proxy mit TLS, z. B. Caddy oder Nginx,
- klare Dokumentation: nur in vertrauenswürdigem LAN/AP benutzen,
- Session-Cookie-Flags und Proxy-Konfiguration sauber dokumentieren.

## 4. Login-Schutz härten

Vorhanden sind Hashing, Login, Pflicht-Passwortwechsel und CSRF. Sinnvoll wären zusätzlich:

- Login-Rate-Limit / Brute-Force-Schutz,
- Sperre nach zu vielen Fehlversuchen,
- optionale Rollen wie Admin / Operator / Read-only,
- optionale API-Token für Maschinenzugriffe statt Browser-Login.

## 5. Installer: festes Initialpasswort vermeiden

Die App-Logik kann ein zufälliges Initialpasswort erzeugen und sicher ablegen. Der Installer nutzt aber als Fallback noch `12345678` für `INITIAL_ADMIN_PASSWORD`, wenn nichts anderes gesetzt ist.

Besser:

- immer zufällig generieren,
- nie einen festen schwachen Fallback benutzen,
- das generierte Erstpasswort am Ende einmalig anzeigen.

Das ist einer der wichtigsten konkreten Security-Fixes.

## 6. Gunicorn-/Worker-Betrieb festnageln

Das System arbeitet stark mit globalem Zustand, Hintergrundthreads, in-process Scheduler und Hardware-Monitoren.

Daher sollte es als Single-Worker-Gerätedienst laufen. Mehrere Worker können verursachen:

- doppelte Scheduler-Jobs,
- doppelte Button-Monitore,
- doppelte Hintergrunddienste,
- Race Conditions.

Die Gunicorn-Konfiguration und Dokumentation sollten klar festhalten: ein Worker ist Absicht und Voraussetzung.

## 7. Sinnvolle nächste Ausbaustufen

Health/Status-Endpunkte:

- `/healthz`
- `/readyz`
- letzte Fehlerzustände,
- Status von Audio, DB, RTC, Bluetooth und Scheduler.

Backup/Restore:

- SQLite-Backup,
- Export/Import der Settings,
- Export der Playlists und Schedules.

Safe Mode:

- falls Netzwerkkonfiguration kaputtgeschrieben wurde,
- falls Audio-/GPIO-Konfiguration unbrauchbar ist,
- Start mit Minimalmodus.

Disk-Space-/Quota-Checks:

- vor Upload,
- Warnung bei wenig Speicherplatz.

Optionale externe Integration:

- MQTT,
- Home Assistant,
- einfache REST-API,
- WebSocket/Eventstream für Live-Status.

Mehr Medienformate:

- nur falls gewünscht; aktuell ist die Begrenzung auf WAV/MP3 bewusst einfach.

## 8. Bereits gut gelöste Punkte

Nicht vergessen: Viele wichtige Grundlagen sind bereits vorhanden:

- Secret-Key-Handling im Installer und in Flask.
- Non-root-/systemd-/Polkit-Deployment.
- Loganzeige und logrotate.
- RTC-Erkennung und mehrere RTC-Typen.
- Netzwerkvalidierung mit Rollback-Idee.
- Button-Konfliktprüfung gegen Endstufen-Pin.
- Bluetooth- und Audio-Fallbacks.

Das System ist kein planlos zusammenkopierter Haufen, sondern ein durchdachtes Gerät mit viel Praxisbezug. Die Hauptbaustelle ist jetzt vor allem Struktur, Härtung und langfristige Wartbarkeit.
