import os
import sys
from pathlib import Path
from datetime import datetime


# Benötigte Umgebungsvariablen setzen
os.environ.setdefault("FLASK_SECRET_KEY", "test")
os.environ["TESTING"] = "1"

# Projektverzeichnis in den Suchpfad aufnehmen
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app


class FakeSMBus:
    def __init__(self):
        self.memory = {}
        self.read_calls = []
        self.write_calls = []

    def preset(self, start_register, data):
        for offset, value in enumerate(data):
            self.memory[start_register + offset] = value

    def read_i2c_block_data(self, address, register, length):
        self.read_calls.append((address, register, length))
        return [self.memory.get(register + offset, 0x00) for offset in range(length)]

    def write_i2c_block_data(self, address, register, data):
        self.write_calls.append((address, register, list(data)))
        for offset, value in enumerate(data):
            self.memory[register + offset] = value


def test_rtc_register_block_access():
    fake_bus = FakeSMBus()
    fake_bus.preset(
        0x02,
        [
            0x45,  # Sekunden = 45
            0x33,  # Minuten = 33
            0x14,  # Stunden = 14
            0x05,  # Tag = 5
            0x02,  # Wochentag = 2
            0x11,  # Monat = 11
            0x23,  # Jahr = 2023 -> 2023
        ],
    )

    original_bus = app.bus
    app.bus = fake_bus

    try:
        dt = app.read_rtc()
        assert fake_bus.read_calls == [(app.RTC_ADDRESS, 0x02, 7)]
        assert dt == datetime(2023, 11, 5, 14, 33, 45)

        new_dt = datetime(2024, 2, 3, 4, 5, 6)
        app.set_rtc(new_dt)

        assert fake_bus.write_calls, "write_i2c_block_data wurde nicht aufgerufen"
        addr, register, data = fake_bus.write_calls[-1]
        assert addr == app.RTC_ADDRESS
        assert register == 0x02
        assert data == [
            app.dec_to_bcd(new_dt.second) & 0x7F,
            app.dec_to_bcd(new_dt.minute) & 0x7F,
            app.dec_to_bcd(new_dt.hour) & 0x3F,
            app.dec_to_bcd(new_dt.day) & 0x3F,
            app.dec_to_bcd(new_dt.weekday()) & 0x07,
            app.dec_to_bcd(new_dt.month) & 0x1F,
            app.dec_to_bcd(new_dt.year - 2000) & 0xFF,
        ]
    finally:
        app.bus = original_bus
