"""Tests for the in-car warning lights (flagzero/tools/warning_lights.py, hardware/warning_lights/, Person C).

The level the Arduino shows comes from real engine states, and the serial side is checked
on pyserial's loopback port when pyserial is installed. Run: python3 -m pytest -q
"""
import pytest

from flagzero import config
from flagzero.core import latency
from flagzero.core.engine import Engine
from flagzero.tools.warning_lights import car_level, dash_url


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(latency, "CSV", tmp_path / "latency.csv")
    e = Engine(seed=3)
    e.world.car_sockets.update({17: object(), 21: object()})
    return e


def crash_ahead_of_21(e):
    e.on_dash({"type": "scene", "n": 2}, {17, 21})              # car 21 ~9 s behind car 17
    e.on_car(17, {"type": "imu_event", "cls": "IMPACT", "peak_g": 5.2, "conf": 0.85})
    e.tick(0.05, {17, 21})


def test_lights_are_off_on_a_clear_track(eng):
    eng.tick(0.05, {17, 21})
    assert car_level(eng.world.state_message(), 21) == 0


def test_car_behind_the_crash_gets_a_yellow(eng):
    crash_ahead_of_21(eng)
    assert car_level(eng.world.state_message(), 21) == 2        # ~9 s away from a mild impact: YELLOW


def test_double_yellow_once_the_crashed_car_lies_still(eng):
    crash_ahead_of_21(eng)
    eng.on_car(17, {"type": "imu_update", "still": True})
    for _ in range(int(4 / 0.05)):                               # car 21 closes in on the flag zone
        eng.tick(0.05, {17, 21})
    assert car_level(eng.world.state_message(), 21) == 3


def test_red_for_everyone_when_the_driver_does_not_answer(eng):
    crash_ahead_of_21(eng)
    eng.on_car(17, {"type": "countdown_result", "result": "TIMEOUT"})
    eng.tick(0.05, {17, 21})
    msg = eng.world.state_message()
    assert car_level(msg, 21) == 5 and car_level(msg, 17) == 5


def test_dash_url():
    assert dash_url("ws://localhost:8000") == "ws://localhost:8000/ws/dash"
    assert dash_url("https://abc.trycloudflare.com/") == "wss://abc.trycloudflare.com/ws/dash"


def test_serial_line_format():
    pytest.importorskip("serial")
    from flagzero.tools.warning_lights import Arduino
    a = Arduino("loop://")
    a.ser.write(b"")                                             # loopback: what we send comes back
    a.ser.read(a.ser.in_waiting)
    a.ser.write(b"S 3\n")
    assert a.ser.read(a.ser.in_waiting) == b"S 3\n"
    a.send(5)                                                    # send() also discards what it reads back
    assert a.ser.in_waiting == 0
