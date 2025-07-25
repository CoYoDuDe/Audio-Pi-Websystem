#!/bin/bash
set -e

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

echo "Umgebung bereit. Aktivieren mit: source venv/bin/activate"

