#!/bin/bash
set -e

usage() {
  cat <<'USAGE'
Verwendung: ./setup_env.sh [--dev]

Erstellt eine virtuelle Umgebung (venv) und installiert die Laufzeitabhängigkeiten
aus requirements.txt. Mit --dev werden zusätzlich die Entwicklungsabhängigkeiten
aus dev-requirements.txt installiert.
USAGE
}

INSTALL_DEV=0

for arg in "$@"; do
  case "$arg" in
    --dev)
      INSTALL_DEV=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unbekannte Option: $arg" >&2
      usage >&2
      exit 1
      ;;
  esac
done

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

if [[ "$INSTALL_DEV" -eq 1 ]]; then
  pip install -r dev-requirements.txt
fi

echo "Umgebung bereit. Aktivieren mit: source venv/bin/activate"
if [[ "$INSTALL_DEV" -eq 1 ]]; then
  echo "Entwicklungsabhängigkeiten installiert."
else
  echo "Optional: ./setup_env.sh --dev installiert zusätzliche Entwicklungsabhängigkeiten."
fi

