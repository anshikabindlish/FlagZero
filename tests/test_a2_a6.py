"""Tests for A2-A6. Run: python3 -m pytest -q"""
import pytest

from flagzero import config
from flagzero.core import fusion, latency, router
from flagzero.core.engine import Engine
from flagzero.core.severity import severity
from flagzero.core.state import Incident
from flagzero.sim import marshal, montecarlo

V180 = 50.0  # 180 km/h in m/s


# ---------------------------------------------------------------- A2 router
def test_d_need_worked_example():
    assert router.d_need(V180) == pytest.approx(135.2, abs=0.2)


@pytest.mark.parametrize("dist,max_level,expected", [
    (210, 3, router.DBL_YELLOW),   # ETA 4.2 s, margin ~75 m -> incident max level
    (210, 2, router.YELLOW),
    (800, 3, router.YELLOW),       # ETA 16 s
    (1500, 3, router.CAUTION),     # ETA 30 s
    (2100, 3, router.NORMAL),      # outside 2,000 m
    (800, 1, router.CAUTION),      # never above the incident's max level
])
def test_level_for(dist, max_level, expected):
    assert router.level_for(dist, V180, max_level) == expected


def test_margin_rule_catches_fast_car_even_with_eta_over_6s():
    v = 250 / 3.6                               # d_need ~250 m
    assert router.eta_s(450, v) > 6
    assert router.level_for(300, v, 3) == router.DBL_YELLOW   # margin < 100 m


def test_router_wraparound():
    e = Engine(seed=1)
    w = e.world
    w.incidents.append(Incident(id=1, kind="SPIN", track_m=50, corner="T1", severity=2))
    w.cars[21].track_m, w.cars[21].speed_mps = 3900, V180
    cw = router.compute(w, 4000)[21]
    assert cw.dist_m == pytest.approx(150) and cw.level == router.YELLOW


# ---------------------------------------------------------------- A3 fusion + severity
def test_noisy_or_worked_example():
    assert fusion.noisy_or([0.88, 0.93]) == pytest.approx(0.9916)


def _inc(*sources, still=False, countdown=None):
    i = Incident(id=1, kind="X", track_m=1423, corner="T4", car=17)
    i.sources = [dict(s) for s in sources]
    i.still, i.countdown = still, countdown
    return i


IMU_IMPACT = {"src": "IMU", "kind": "IMPACT", "conf": 0.88, "peak_g": 5.2}


@pytest.mark.parametrize("inc,expected", [
    (_inc(), 0),
    (_inc({"src": "IMU", "kind": "KERB", "conf": 0.9}), 1),
    (_inc({"src": "IMU", "kind": "SPIN", "conf": 0.8}), 2),
    (_inc({"src": "IMU", "kind": "SPIN", "conf": 0.4}), 1),            # lone low-confidence source
    (_inc(IMU_IMPACT), 2),                                                # mild impact
    (_inc({"src": "IMU", "kind": "IMPACT", "conf": 0.9, "peak_g": 9.0}), 3),  # strong impact
    (_inc({"src": "IMU", "kind": "ROLLOVER", "conf": 0.9}), 3),
    (_inc(IMU_IMPACT, still=True), 3),
    (_inc(IMU_IMPACT, still=True, countdown="TIMEOUT"), 4),             # no OK in 15 s -> auto red
    (_inc(IMU_IMPACT, still=True, countdown="OK"), 3),
])
def test_severity(inc, expected):
    assert severity(inc) == expected


# ---------------------------------------------------------------- engine scenes
@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(latency, "CSV", tmp_path / "latency.csv")
    e = Engine(seed=3)
    return e


PHONES = {17, 21}


def run_ticks(e, seconds, t0_ms, now0):
    out = []
    n = int(seconds * config.SERVER_TICK_HZ)
    for k in range(1, n + 1):
        out += e.tick(1 / config.SERVER_TICK_HZ, PHONES, now_s=now0 + k / 20, t_ms=t0_ms + k * 50)
    return out


def test_scene1_spin_warns_car21(eng):
    e = eng
    e.on_dash({"type": "scene", "n": 1}, PHONES)
    spin_m = e.world.cars[17].track_m
    e.on_car(17, {"type": "imu_event", "cls": "SPIN", "peak_g": 1.8, "conf": 0.72}, t_ms=1000)
    inc = e.world.incidents[0]
    corner = e.sim.track.nearest_corner(spin_m).name
    assert inc.kind == "SPIN" and inc.corner == corner and inc.severity == 2
    assert e.world.cars[17].state != "STOPPED"        # a spin doesn't stop the car
    out = run_ticks(e, 3, 1000, 1.0)
    warn21 = [m for c, m in out if c == 21 and m["type"] == "warning"]
    assert warn21 and warn21[0]["level"] >= router.CAUTION and warn21[0]["corner"] == corner
    dists = [m["dist_m"] for m in warn21 if m["dist_m"] is not None]
    assert dists[-1] < dists[0]                       # counts down live
    assert all(m["level"] <= router.YELLOW for m in warn21)   # a spin maxes out at YELLOW
    assert len(warn21) >= 10                          # resent every 250 ms
    # A6: ack the first warning -> one latency row
    e.on_car(21, {"type": "ack", "id": warn21[0]["id"]}, t_ms=1600)
    s = latency.stats()
    assert s["n"] == 1 and e.world.latency["last_detect_to_warn_ms"] == 600


def test_scene2_full_escalation(eng):
    e = eng
    e.on_dash({"type": "scene", "n": 2}, PHONES)
    p17 = round(e.world.cars[17].track_m, 1)            # car 17 crashes wherever it is
    out = e.on_car(17, {"type": "imu_event", "cls": "IMPACT", "peak_g": 5.2, "conf": 0.88}, t_ms=1100)
    assert out == [(17, {"type": "countdown", "secs": 15, "peak_g": 5.2})]
    inc = e.world.incidents[0]
    assert inc.severity == 2 and inc.track_m == pytest.approx(p17, abs=1)
    assert e.world.cars[17].state == "STOPPED"

    e.on_car(17, {"type": "imu_update", "still": True}, t_ms=2600)
    assert inc.severity == 3

    assert len(e.world.incidents) == 1                  # same car -> same incident
    assert inc.fused_conf == pytest.approx(0.88)

    out = run_ticks(e, 1, 3200, 3.2)
    lv21 = [m["level"] for c, m in out if c == 21 and m["type"] == "warning"]
    assert lv21 and max(lv21) < router.RED               # no RED while the countdown runs
    assert not any(c == 17 and m["type"] == "warning" for c, m in out)   # crashed car: no warnings

    out = e.on_car(17, {"type": "countdown_result", "result": "TIMEOUT"}, t_ms=16200)
    assert (17, {"type": "medical", "status": "URGENT"}) in out
    assert inc.severity == 4
    assert e.world.red_confirmed and e.world.red_auto and not e.world.red_pending   # automatic, no click

    out = run_ticks(e, 0.5, 16200, 16.2)
    assert [m["level"] for c, m in out if c == 21 and m["type"] == "warning"][-1] == router.RED
    assert not any(c == 17 and m.get("level") == router.RED for c, m in out)
    assert e.world.state_message()["vitals"]["17"]["sim"] is True

    out = e.on_dash({"type": "reset"}, PHONES)
    assert (21, {"type": "reset"}) in out and not e.world.incidents
    assert e.world.cars[17].state != "STOPPED"


def test_ok_pressed_means_monitor(eng):
    e = eng
    e.on_car(17, {"type": "imu_event", "cls": "IMPACT", "peak_g": 6, "conf": 0.9}, t_ms=0)
    out = e.on_car(17, {"type": "ok_pressed"}, t_ms=3000)
    assert (17, {"type": "medical", "status": "MONITOR"}) in out
    assert e.world.incidents[0].countdown == "OK"


def test_server_times_out_silent_phone_and_raises_red(eng):
    e = eng
    e.on_car(17, {"type": "imu_event", "cls": "IMPACT", "peak_g": 6, "conf": 0.9}, t_ms=0)
    out = run_ticks(e, 14, 0, 0.0)
    assert e.world.incidents[0].countdown == "PENDING"      # still inside the 15 s window
    assert not e.world.red_confirmed
    out = run_ticks(e, 5, 14000, 14.0)                       # phone never answers
    assert (17, {"type": "medical", "status": "URGENT"}) in out
    assert e.world.incidents[0].countdown == "TIMEOUT"
    assert e.world.red_confirmed and e.world.red_auto


def test_ok_within_15s_means_no_red(eng):
    e = eng
    e.on_car(17, {"type": "imu_event", "cls": "IMPACT", "peak_g": 6, "conf": 0.9}, t_ms=0)
    e.on_car(17, {"type": "imu_update", "still": True}, t_ms=1500)
    e.on_car(17, {"type": "ok_pressed"}, t_ms=12000)
    run_ticks(e, 10, 12000, 12.0)
    assert e.world.incidents[0].severity == 3 and not e.world.red_confirmed


def test_vitals_ramp_after_impact(eng):
    e = eng
    run_ticks(e, 0.1, 0, 0.0)
    rest = e.world.vitals[17]["hr"]
    e.on_car(17, {"type": "imu_event", "cls": "IMPACT", "peak_g": 6, "conf": 0.9}, t_ms=1000)
    run_ticks(e, 0.1, 7000, 7.0)
    assert 70 <= rest <= 80 and 135 <= e.world.vitals[17]["hr"] <= 145
    assert 96 <= e.world.vitals[17]["spo2"] <= 98


def test_events_near_each_other_merge(eng):
    e = eng
    e.world.cars[8].track_m = 1423
    e.on_car(8, {"type": "imu_event", "cls": "SPIN", "conf": 0.8}, t_ms=0)
    e.world.cars[5].track_m = 1440
    e.on_car(5, {"type": "imu_event", "cls": "SPIN", "conf": 0.8}, t_ms=2000)
    assert len(e.world.incidents) == 1 and e.world.incidents[0].car == 8   # < 50 m, < 5 s -> same incident
    e.world.cars[3].track_m = 2500
    e.on_car(3, {"type": "imu_event", "cls": "SPIN", "conf": 0.8}, t_ms=2500)
    assert len(e.world.incidents) == 2                  # far away -> new incident


# ---------------------------------------------------------------- A4 marshal / A5 Monte Carlo
def test_marshal_sample_ranges():
    s = marshal.sample(5000)
    assert (s["react"] >= 0.8).all() and (s["react"] <= 2.5).all()
    assert (s["total"] >= 0.2 + 0.8 + 0.5).all()
    assert isinstance(marshal.sample()["total"], float)


def test_montecarlo_fast_and_reproducible():
    a = montecarlo.run(10_000, seed=42)
    b = montecarlo.run(10_000, seed=42)
    assert a["flagzero"] == b["flagzero"]
    assert a["runtime_ms"] < 1000
    assert a["flagzero"]["warned_before_hazard_pct"] > a["baseline"]["warned_before_hazard_pct"]
    assert a["flagzero"]["median_time_to_warn_s"] < a["baseline"]["median_time_to_warn_s"]
    for k in ("marshal", "flagzero", "car_reaches_hazard_s"):
        assert k in a["timeline"]


def test_montecarlo_slider_changes_result():
    lo = montecarlo.run(5000, seed=1, visibility=1.0)
    hi = montecarlo.run(5000, seed=1, visibility=0.0)
    assert lo["baseline"]["median_time_to_warn_s"] < hi["baseline"]["median_time_to_warn_s"]
