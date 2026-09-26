"""Tests for the phone additions in flagzero/docs/phone_changes.md (Person C).

driver_request RED_FLAG / FALSE_ALARM, green_flag, and cars slowing through a
DBL YELLOW flag zone and driving past the crash. Run: python3 -m pytest -q
"""
import pytest

from flagzero import config
from flagzero.core import latency, router
from flagzero.core.engine import Engine
from flagzero.core.track import upstream_distance

PHONES = {17, 21}


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(latency, "CSV", tmp_path / "latency.csv")
    e = Engine(seed=3)
    e.world.car_sockets.update({17: object(), 21: object()})  # the engine resets connected phones
    return e


def crash(e, t_ms=1000, peak_g=5.2):
    return e.on_car(17, {"type": "imu_event", "cls": "IMPACT", "peak_g": peak_g, "conf": 0.85}, t_ms)


def test_crash_stops_car17_where_it_is(eng):
    pos = eng.world.cars[17].track_m
    out = crash(eng)
    assert eng.world.cars[17].track_m == pos and eng.world.cars[17].speed_mps == 0
    assert any(c == 17 and m["type"] == "countdown" and m["secs"] == 15 for c, m in out)


def test_driver_recommends_red_needs_confirm(eng):
    crash(eng)
    eng.on_car(17, {"type": "driver_request", "request": "RED_FLAG"}, 2000)
    inc = eng.world.incidents[0]
    assert inc.severity == 4 and any(s["src"] == "DRIVER" for s in inc.sources)
    assert eng.world.red_pending and not eng.world.red_confirmed      # waits for race control
    eng.on_dash({"type": "confirm_red"}, PHONES)
    assert eng.world.red_confirmed and not eng.world.red_auto


def test_false_alarm_clears_yellows(eng):
    crash(eng)
    eng.on_car(17, {"type": "ok_pressed"}, 2000)
    out = eng.on_car(17, {"type": "driver_request", "request": "FALSE_ALARM"}, 3000)
    assert not eng.world.incidents
    assert {c for c, m in out if m["type"] == "reset"} == PHONES


def test_false_alarm_ignored_once_red(eng):
    crash(eng)
    eng.on_car(17, {"type": "countdown_result", "result": "TIMEOUT"}, 16000)
    assert eng.world.red_confirmed and eng.world.red_auto             # no OK -> automatic red
    out = eng.on_car(17, {"type": "driver_request", "request": "FALSE_ALARM"}, 17000)
    assert eng.world.incidents and not any(m["type"] == "reset" for _, m in out)


def test_green_flag_resets_everything(eng):
    crash(eng)
    eng.on_car(17, {"type": "countdown_result", "result": "TIMEOUT"}, 16000)
    out = eng.on_car(21, {"type": "green_flag"}, 17000)
    w = eng.world
    assert not w.incidents and not w.red_confirmed and not w.red_pending
    assert {c for c, m in out if m["type"] == "reset"} == PHONES


def test_car21_slows_for_double_yellow_and_drives_past(eng):
    e = eng
    e.on_dash({"type": "scene", "n": 2}, PHONES)
    lap = e.sim.lap
    t4 = e.sim.track.corner("T4").s
    # crash car 17 at T4 with a strong impact (severity 3 -> DBL YELLOW)
    e.world.cars[17].track_m = t4
    crash(e, peak_g=9.5)
    e.on_car(17, {"type": "ok_pressed"}, 1500)              # driver OK, so no automatic red
    inc = e.world.incidents[0]
    assert inc.severity == 3
    in_zone_speeds, passed, now, t = [], False, 1.0, 1000
    for _ in range(int(60 * config.SERVER_TICK_HZ)):
        now += 1 / config.SERVER_TICK_HZ
        t += 50
        e.tick(1 / config.SERVER_TICK_HZ, PHONES, now_s=now, t_ms=t)
        c21 = e.world.cars[21]
        dist = upstream_distance(c21.track_m, inc.track_m, lap)
        if dist <= 300:
            in_zone_speeds.append(c21.speed_kmh)
            assert c21.warning == router.DBL_YELLOW          # no flip-flopping back to YELLOW
        if in_zone_speeds and dist > lap - 200:              # just past the crashed car
            passed = True
            break
    assert in_zone_speeds and max(in_zone_speeds) <= 120 + 1
    assert passed and e.world.cars[21].speed_mps > 0         # kept driving
    assert e.world.cars[17].speed_mps == 0                   # crashed car still where it stopped
