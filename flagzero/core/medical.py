"""A4: driver medical status and simulated vitals.

Countdown TIMEOUT -> URGENT, OK pressed -> MONITOR (set in incidents.py).
Vitals are SIMULATED and always labelled SIM:
  heart rate 70-80 at rest; after an impact it ramps to 135-145 over 5 s
  SpO2 stays between 96 and 98
"""
from __future__ import annotations

import random

from flagzero import config
from flagzero.core.incidents import handle_countdown_result
from flagzero.core.state import WorldState

_rng = random.Random()
_base: dict[int, dict] = {}


def _baseline(car: int) -> dict:
    if car not in _base:
        _base[car] = {"hr_rest": _rng.uniform(*config.HR_REST),
                      "hr_peak": _rng.uniform(*config.HR_IMPACT),
                      "spo2": _rng.uniform(*config.SPO2)}
    return _base[car]


def vitals_for(world: WorldState, car: int, t_ms: int) -> dict:
    b = _baseline(car)
    impact_ms = max((i.impact_ms for i in world.incidents if i.car == car and i.impact_ms), default=None)
    if impact_ms is None:
        hr = b["hr_rest"]
        lo, hi = config.HR_REST
    else:
        f = min(1.0, max(0.0, (t_ms - impact_ms) / 1000 / config.HR_RAMP_S))
        hr = b["hr_rest"] + f * (b["hr_peak"] - b["hr_rest"])
        lo, hi = (config.HR_IMPACT if f >= 1.0 else (config.HR_REST[0], config.HR_IMPACT[1]))
    hr = min(hi, max(lo, hr + _rng.uniform(-1.0, 1.0)))   # small wobble, kept inside the spec range
    b["spo2"] = min(config.SPO2[1], max(config.SPO2[0], b["spo2"] + _rng.uniform(-0.05, 0.05)))
    status = next((i.medical for i in world.incidents if i.car == car and i.medical), None)
    resp = next((i.countdown for i in world.incidents if i.car == car and i.countdown), None)
    return {"hr": round(hr), "spo2": round(b["spo2"], 1), "sim": True,
            "response": {"PENDING": "WAITING", "OK": "OK", "TIMEOUT": "NO_RESPONSE"}.get(resp, "-"),
            "medical": status}


def update(world: WorldState, t_ms: int) -> list[tuple[int, dict]]:
    """Refresh SIM vitals for phone cars, and time out a countdown the phone
    never answered (fail-safe: an unanswered driver is treated as TIMEOUT)."""
    out: list[tuple[int, dict]] = []
    limit_ms = (config.COUNTDOWN_S + config.COUNTDOWN_SERVER_GRACE_S) * 1000
    for inc in world.incidents:
        if inc.countdown == "PENDING" and inc.countdown_started_ms is not None \
                and t_ms - inc.countdown_started_ms > limit_ms and inc.car is not None:
            out += handle_countdown_result(world, {"car": inc.car, "result": "TIMEOUT"}, t_ms)
    for car in config.SIM_PHONE_CARS:
        world.vitals[car] = vitals_for(world, car, t_ms)
    return out


def reset() -> None:
    _base.clear()
