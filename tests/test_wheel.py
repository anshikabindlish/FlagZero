"""Tests for the physical steering wheel (flagzero/tools/wheel.py, hardware/wheel/, Person C).

The wheel's flag + countdown come from real engine states, and the server passes a
wheel button to both of the car's screens (phone + wheel display). Run: python3 -m pytest -q
"""
import pytest
from fastapi.testclient import TestClient

from flagzero import config, server
from flagzero.core import latency
from flagzero.core.engine import Engine
from flagzero.tools.wheel import dash_url, wheel_state


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(latency, "CSV", tmp_path / "latency.csv")
    e = Engine(seed=3)
    e.world.car_sockets.update({17: object(), 21: object()})
    return e


def test_wheel_follows_the_crashed_cars_countdown_and_red(eng):
    assert wheel_state(eng.world.state_message(), 17) == (0, -1)
    eng.on_car(17, {"type": "imu_event", "cls": "IMPACT", "peak_g": 5.2, "conf": 0.85})
    level, secs = wheel_state(eng.world.state_message(), 17)
    assert 13 <= secs <= 15                                    # I'M OK check running
    eng.on_car(17, {"type": "countdown_result", "result": "TIMEOUT"})
    assert wheel_state(eng.world.state_message(), 17) == (5, -1)   # no answer: automatic RED


def test_wheel_shows_the_warning_of_an_approaching_car(eng):
    eng.on_car(17, {"type": "imu_event", "cls": "IMPACT", "peak_g": 5.2, "conf": 0.85})
    eng.position_scene(2)
    eng.tick(0.05, {17, 21})
    level, secs = wheel_state(eng.world.state_message(), 21)
    assert level >= 2 and secs == -1


def test_dash_url():
    assert dash_url("ws://localhost:8000") == "ws://localhost:8000/ws/dash"
    assert dash_url("https://abc.trycloudflare.com/") == "wss://abc.trycloudflare.com/ws/dash"


def _wait_for(ws, kind, tries=200):
    for _ in range(tries):
        m = ws.receive_json()
        if m.get("type") == kind:
            return m
    raise AssertionError(f"no {kind} message")


def test_wheel_button_reaches_phone_and_wheel_display():
    with TestClient(server.app) as client:
        with client.websocket_connect("/ws/car?car=17") as phone, \
                client.websocket_connect("/ws/car?car=17&role=wheel") as display, \
                client.websocket_connect("/ws/dash") as dash:
            assert 17 in server.world.car_sockets and 17 in server.wheel_sockets
            dash.send_json({"type": "wheel_button", "car": 17, "button": "ok"})
            assert _wait_for(phone, "wheel_button")["button"] == "ok"
            assert _wait_for(display, "wheel_button")["button"] == "ok"
