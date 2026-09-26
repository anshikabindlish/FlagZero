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


def test_false_alarm_is_only_a_report(eng):
    crash(eng)
    eng.on_car(17, {"type": "ok_pressed"}, 2000)
    sev = eng.world.incidents[0].severity
    out = eng.on_car(17, {"type": "driver_request", "request": "FALSE_ALARM"}, 3000)
    inc = eng.world.incidents[0]                                   # still out: race control decides
    assert not any(m["type"] == "reset" for _, m in out)
    assert any(s["src"] == "DRIVER" and s["kind"] == "FALSE_ALARM" for s in inc.sources)
    assert inc.severity == sev and inc.fused_conf == pytest.approx(0.85)   # a report adds no confidence
    out = eng.on_dash({"type": "reset"}, PHONES)                   # race control agrees -> green
    assert not eng.world.incidents and {c for c, m in out if m["type"] == "reset"} == PHONES


def test_false_alarm_report_under_red_changes_nothing(eng):
    crash(eng)
    eng.on_car(17, {"type": "countdown_result", "result": "TIMEOUT"}, 16000)
    assert eng.world.red_confirmed and eng.world.red_auto             # no OK -> automatic red
    out = eng.on_car(17, {"type": "driver_request", "request": "FALSE_ALARM"}, 17000)
    assert eng.world.incidents and eng.world.red_confirmed and not any(m["type"] == "reset" for _, m in out)


@pytest.mark.parametrize("red", [False, True])
def test_phone_green_flag_is_ignored(eng, red):
    crash(eng)
    if red:
        eng.on_car(17, {"type": "countdown_result", "result": "TIMEOUT"}, 16000)
    for car in (17, 21):                                          # neither the crashed nor another driver
        out = eng.on_car(car, {"type": "green_flag"}, 17000)
        assert not any(m["type"] == "reset" for _, m in out)
    assert eng.world.incidents and eng.world.red_confirmed == red
    eng.on_dash({"type": "reset"}, PHONES)                        # only race control clears
    assert not eng.world.incidents and not eng.world.red_confirmed


def test_scene_puts_car21_about_9s_behind_car17(eng):
    e = eng
    before = {n: c.track_m for n, c in e.world.cars.items()}
    e.on_dash({"type": "scene", "n": 2}, PHONES)
    c17, c21 = e.world.cars[17], e.world.cars[21]
    assert c17.track_m == before[17]                        # car 17 is not moved
    gap = upstream_distance(c21.track_m, c17.track_m, e.sim.lap)
    assert 200 < gap < 9 * 90                               # ~9 s of driving at race speeds


def test_ok_under_yellow_car17_drives_on(eng):
    e = eng
    crash(e)
    pos = e.world.cars[17].track_m
    e.on_car(17, {"type": "ok_pressed"}, 2000)
    e.on_car(17, {"type": "countdown_result", "result": "OK"}, 2000)
    assert e.world.incidents and not e.world.red_confirmed  # yellows stay out
    for k in range(40):                                     # 2 s
        e.tick(0.05, PHONES, now_s=1 + k * 0.05, t_ms=2000 + k * 50)
    c17 = e.world.cars[17]
    assert c17.speed_mps > 0 and upstream_distance(pos, c17.track_m, e.sim.lap) > 5


def test_car17_stays_stopped_for_red(eng):
    e = eng
    crash(e)
    e.on_car(17, {"type": "ok_pressed"}, 2000)
    e.on_car(17, {"type": "driver_request", "request": "RED_FLAG"}, 2500)   # OK, but wants red
    for k in range(40):
        e.tick(0.05, PHONES, now_s=1 + k * 0.05, t_ms=3000 + k * 50)
    assert e.world.cars[17].speed_mps == 0
    e2_pos = e.world.cars[17].track_m
    crash_no_ok = e.world.cars[17]
    assert crash_no_ok.track_m == e2_pos


def test_car17_stays_stopped_without_ok(eng):
    e = eng
    crash(e)
    e.on_car(17, {"type": "countdown_result", "result": "TIMEOUT"}, 16000)
    for k in range(40):
        e.tick(0.05, PHONES, now_s=1 + k * 0.05, t_ms=16000 + k * 50)
    assert e.world.cars[17].speed_mps == 0 and e.world.red_confirmed


def test_car21_slows_for_double_yellow_and_drives_past(eng):
    e = eng
    e.on_dash({"type": "scene", "n": 2}, PHONES)            # car 21 ~9 s behind car 17
    lap = e.sim.lap
    crash(e, peak_g=9.5)                                    # strong impact wherever car 17 is: severity 3
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
    assert e.world.cars[17].speed_mps == 0                   # no I'm OK yet: crashed car still where it stopped


# ---------------------------------------------------------------- per-incident counterfactual
def test_incident_counterfactual_physics():
    from flagzero.sim import montecarlo
    r = montecarlo.incident([(21, 500, 250), (3, 30, 150), (8, 1800, 200), (9, 400, 0)],
                            sightline_m=150, fz_warn_s=0.35)
    cars = {c["car"]: c for c in r["cars"]}
    assert 9 not in cars                                          # stopped car isn't approaching
    assert cars[21]["marshal"]["impact_pct"] > 30 and cars[21]["flagzero"]["impact_pct"] < 5   # FlagZero saves it
    assert cars[3]["marshal"]["impact_pct"] == cars[3]["flagzero"]["impact_pct"] == 100       # too close for anyone
    assert cars[8]["marshal"]["impact_pct"] == cars[8]["flagzero"]["impact_pct"] == 0          # far away either way
    assert r["expected_impacts"]["flagzero"] < r["expected_impacts"]["marshal"]
    assert r["flagzero_measured"] and cars[21]["flagzero"]["warn_s"] == 0.35


def test_engine_snapshots_incident_and_measured_warning(eng):
    e = eng
    e.on_dash({"type": "scene", "n": 2}, PHONES)                 # car 21 ~9 s behind car 17
    crash(e, t_ms=1000)
    out = e.tick(0.05, PHONES, now_s=1.05, t_ms=1050)
    snap = e.incident_snapshot()
    assert snap and snap["cars"] and any(c[0] == 21 for c in snap["cars"])
    assert snap["warn_ms"] is None and snap["detect_s"] == config.MC_IMU_DETECT_S
    wid = next(m["id"] for c, m in out if c == 21 and m["type"] == "warning")
    e.on_car(21, {"type": "ack", "id": wid}, t_ms=1120)
    assert e.incident_snapshot()["warn_ms"] == 120               # this incident's measured detect -> warn
    assert e.incident_snapshot(snap["id"]) is snap
