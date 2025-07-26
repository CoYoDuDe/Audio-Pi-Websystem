import os
import sys
from pathlib import Path
from datetime import datetime, timezone

# Benötigte Umgebungsvariablen setzen
os.environ.setdefault("FLASK_SECRET_KEY", "test")
os.environ["TESTING"] = "1"

# Sicherstellen, dass das Projektverzeichnis im Suchpfad liegt
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import parse_once_datetime
import pytest


def test_parse_iso_z():
    dt = parse_once_datetime("2024-01-01T00:00:00Z")
    assert dt == datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)


def test_parse_iso_naive():
    dt = parse_once_datetime("2024-02-02T12:30:45")
    assert dt == datetime(2024, 2, 2, 12, 30, 45)


def test_parse_space_seconds():
    dt = parse_once_datetime("2024-03-03 08:15:30")
    assert dt == datetime(2024, 3, 3, 8, 15, 30)


def test_parse_space_minutes():
    dt = parse_once_datetime("2024-04-04 09:20")
    assert dt == datetime(2024, 4, 4, 9, 20)


def test_parse_invalid():
    with pytest.raises(ValueError):
        parse_once_datetime("foo")
