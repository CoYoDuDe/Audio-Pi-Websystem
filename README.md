# Audio-Pi-Control

Weboberflaeche fuer einen Raspberry Pi zur Steuerung von Audio-Wiedergabe, Playlists, Zeitplaenen, Bluetooth, WLAN und Systemfunktionen.

## Funktionen

- Audio-Dateien hochladen und abspielen
- Playlists verwalten
- Zeitplaene anlegen
- Bluetooth ein- und ausschalten
- WLAN scannen und konfigurieren
- Systemzeit und RTC verwalten
- Neustart und Herunterfahren ueber die Weboberflaeche

## Installation

```bash
sudo bash install.sh
```

Das Skript installiert die benoetigten Pakete, legt die virtuelle Umgebung an, richtet `audio-pi.service` ein und aktiviert den automatischen Start.

## Entwicklung

```bash
./setup_env.sh --dev
./venv/bin/pytest
```

## Wichtige Dateien

- `uploads/`: `chmod 775`
- `app.log`: `chmod 666`
- Logrotate-Vorlage: `scripts/logrotate/audio-pi`
  mit `create <MODE ...>`; der Installer richtet den Modus passend zu
  `INSTALL_LOG_FILE_MODE` ein.

## Start ohne systemd

```bash
source venv/bin/activate
export FLASK_SECRET_KEY="dein-schluessel"
python app.py
```

## Hinweis

Benutzerdaten wie Datenbank, Uploads und Logs gehoeren nicht ins Repository.

## Unterstützung

Dieses Projekt wird unabhängig und privat entwickelt und kostenlos bereitgestellt. Freiwillige Unterstützung hilft bei Infrastruktur, Servern, Domains, Tests, Wartung und Weiterentwicklung.

- [PayPal](https://paypal.me/CoYoDuDe)
- [Buy Me a Coffee](https://www.buymeacoffee.com/CoYoDuDe)
- [Weitere Projekte und Informationen](https://dnsmith.net/)

Unterstützung ist freiwillig. Es gibt keinen Abo-Zwang und daraus entsteht kein Anspruch auf bestimmte Funktionen oder persönlichen Support.
