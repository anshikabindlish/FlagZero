"""Tests for A1: track, lap distance, simulation. Run: python3 -m pytest -q"""
import pytest

from flagzero import config
from flagzero.core.state import CAMERA, RUNNING, STOPPED
from flagzero.core.track import default_track, load_track, upstream_distance, wrap
from flagzero.sim.sim import Simulation

LAP = 4000.0
# These tests were written for the generic 4,000 m circuit, kept as track_generic.json.
# The live track.json is now the real Interlagos (see test_interlagos_track below).
GENERIC = load_track(config.PACKAGE_DIR / "track_generic.json")


# ---------------------------------------------------------------- upstream_distance
@pytest.mark.parametrize("frm,to,expected", [
    (1000, 1423, 423),        # plain case
    (3900, 50, 150),          # wraparound: past the line
    (1423, 1423, 0),          # at the hazard
    (1500, 1423, 3923),       # just passed it: a whole lap to go
    (0, 3999, 3999),
])
def test_upstream_distance(frm, to, expected):
    assert upstream_distance(frm, to, LAP) == pytest.approx(expected)


def test_wrap():
    assert wrap(4050, LAP) == pytest.approx(50)
    assert wrap(-10, LAP) == pytest.approx(3990)


# ---------------------------------------------------------------- track
def test_track_basics():
    t = GENERIC
    assert t.lap_length_m == 4000
    t4 = t.corner("T4")
    assert t4.s == 1423 and t4.sightline_m == 90
    assert [c.name for c in t.corners] == [f"T{i}" for i in range(1, 9)]
    pts = t.raw["polyline"]
    assert len(pts) == 200 and pts[0]["s"] == 0
    assert t.raw["paper_track"] == {"from_m": 1380.0, "to_m": 1480.0, "corner": "T4"}
    assert t.nearest_corner(1400).name == "T4"


# ---------------------------------------------------------------- simulation
def make_sim():
    return Simulation(seed=7, track=GENERIC)


def test_interlagos_track():
    t = default_track()
    assert "Interlagos" in t.raw["name"]
    assert [c.name for c in t.corners] == [f"T{i}" for i in range(1, 16)]
    assert [c.s for c in t.corners] == sorted(c.s for c in t.corners)
    assert "paper_track" not in t.raw                     # no corner is special
    for c in t.corners:
        assert t.nearest_corner(c.s + 5).name == c.name


def test_spawn():
    sim = make_sim()
    cars = sim.world.cars
    assert len(cars) == 20
    assert cars[17].has_phone and cars[21].has_phone and not cars[8].has_phone
    assert all(0 <= c.track_m < LAP for c in cars.values())


def test_speeds_stay_sane_and_positions_wrap():
    sim = make_sim()
    top = config.SIM_TOP_SPEED_KMH * (1 + config.SIM_SPEED_JITTER) + 0.5
    for _ in range(60):
        sim.run_headless(1.0)
        for c in sim.world.cars.values():
            assert 0 <= c.track_m < LAP
            assert 0 <= c.speed_kmh <= top


def test_cars_slow_for_t5_hairpin():
    sim = make_sim()
    slowest = {}
    for _ in range(int(120 * config.SERVER_TICK_HZ)):
        sim.tick(1 / config.SERVER_TICK_HZ, now_s=sim.t)
        for c in sim.world.cars.values():
            if abs(c.track_m - 1880) < 5:
                slowest[c.car] = min(slowest.get(c.car, 999), c.speed_kmh)
    assert slowest, "no car passed T5"
    assert max(slowest.values()) < 85 * (1 + config.SIM_SPEED_JITTER) + 3


def test_stop_car_and_release():
    sim = make_sim()
    sim.stop_car(17)
    pos = sim.world.cars[17].track_m
    sim.run_headless(5)
    assert sim.world.cars[17].state == STOPPED
    assert sim.world.cars[17].track_m == pos
    sim.release_all()
    sim.run_headless(5)
    assert sim.world.cars[17].state == RUNNING
    assert sim.world.cars[17].track_m != pos


def test_camera_override_then_timeout():
    sim = make_sim()
    sim.apply_camera(17, 1423.0, now_s=100.0)
    c = sim.world.cars[17]
    assert c.state == CAMERA and c.track_m == 1423.0
    sim.tick(0.05, now_s=100.2)                 # camera still fresh: sim leaves it
    assert c.track_m == 1423.0
    sim.apply_camera(17, 1424.0, now_s=100.5)   # hand pushing the car slowly
    assert c.speed_mps == pytest.approx(2.0)
    sim.tick(0.05, now_s=101.5)                 # camera lost it: sim takes over
    assert c.state == RUNNING and c.track_m > 1424.0


def test_camera_does_not_unstop_a_crashed_car():
    sim = make_sim()
    sim.stop_car(17)
    sim.apply_camera(17, 1423.0, now_s=1.0)
    assert sim.world.cars[17].state == STOPPED


def test_state_message_shape():
    sim = make_sim()
    msg = sim.world.state_message()
    assert msg["type"] == "state"
    assert {"car", "track_m", "speed_kmh", "warning"} <= set(msg["cars"][0])
    assert msg["incidents"] == [] and msg["red_pending"] is False


def test_headless_is_fast():
    import time
    sim = make_sim()
    t0 = time.perf_counter()
    sim.run_headless(60)
    assert time.perf_counter() - t0 < 2.0
