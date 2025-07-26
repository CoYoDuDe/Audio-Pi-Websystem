import os
import importlib

# Vor dem Import Umgebungsvariablen setzen
os.environ.setdefault('FLASK_SECRET_KEY', 'test')
os.environ.setdefault('TESTING', '1')

app = importlib.import_module('app')

from app import validate_time, parse_once_datetime
from datetime import datetime
import pytest


def test_validate_time_valid():
    assert validate_time('00:00:00')
    assert validate_time('23:59:59')


def test_validate_time_invalid():
    assert not validate_time('24:00:00')
    assert not validate_time('12:00')
    assert not validate_time('12:00:60')


def test_parse_once_datetime_iso_z():
    dt = parse_once_datetime('2024-05-13T12:30:45Z')
    assert dt == datetime(2024, 5, 13, 12, 30, 45, tzinfo=dt.tzinfo)


def test_parse_once_datetime_space_second():
    dt = parse_once_datetime('2024-05-13 12:30:45')
    assert dt == datetime(2024, 5, 13, 12, 30, 45)


def test_parse_once_datetime_space_minute():
    dt = parse_once_datetime('2024-05-13 12:30')
    assert dt == datetime(2024, 5, 13, 12, 30)


def test_parse_once_datetime_invalid():
    with pytest.raises(ValueError):
        parse_once_datetime('invalid')
