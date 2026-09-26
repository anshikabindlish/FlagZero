"""A2: ETA + stopping distance -> per-car warning level.

For every car upstream of a hazard (within 2,000 m):
    ETA    = dist / v
    d_need = v*t_react + max(0, v^2 - v_safe^2) / (2a)
    margin = dist - d_need
    ETA < 6 s or margin < 100 m -> the incident's maximum level
    6-20 s  -> YELLOW
    20-40 s -> CAUTION
    > 40 s  -> none
Never above the incident's maximum level. RED (5) only after Confirm red.
"""
from __future__ import annotations

from typing import Optional

import math
from dataclasses import dataclass

from flagzero import config
from flagzero.core.state import Incident, WorldState
from flagzero.core.track import upstream_distance

NORMAL, CAUTION, YELLOW, DBL_YELLOW, SLOW_ZONE, RED = range(6)


def d_need(v_mps: float,
           t_react: float = config.ROUTER_T_REACT_S,
           v_safe_mps: float = config.ROUTER_V_SAFE_KMH / 3.6,
           a: float = config.ROUTER_BRAKE_G * config.G) -> float:
    """Distance needed to react and brake from v down to the safe speed."""
    return v_mps * t_react + max(0.0, v_mps ** 2 - v_safe_mps ** 2) / (2 * a)


def eta_s(dist_m: float, v_mps: float) -> float:
    return math.inf if v_mps <= 0.1 else dist_m / v_mps


def level_for(dist_m: float, v_mps: float, max_level: int) -> int:
    """Warning level for one car approaching one hazard."""
    if max_level <= 0 or dist_m > config.ROUTER_MAX_UPSTREAM_M:
        return NORMAL
    eta = eta_s(dist_m, v_mps)
    margin = dist_m - d_need(v_mps)
    if eta < config.ROUTER_ETA_MAX_LEVEL_S or margin < config.ROUTER_MARGIN_M:
        lvl = max_level
    elif eta < config.ROUTER_ETA_YELLOW_S:
        lvl = YELLOW
    elif eta < config.ROUTER_ETA_CAUTION_S:
        lvl = CAUTION
    else:
        lvl = NORMAL
    return min(lvl, max_level)


@dataclass
class CarWarning:
    level: int = NORMAL
    incident: Optional[Incident] = None
    dist_m: Optional[float] = None
    eta_s: Optional[float] = None


def compute(world: WorldState, lap_len: float) -> dict[int, CarWarning]:
    """Warning for every car, from every active incident. Also fills
    incident.approaching for the dashboard's approaching-cars panel."""
    out = {n: CarWarning() for n in world.cars}
    for inc in world.incidents:
        max_level = config.SEVERITY_MAX_WARNING.get(inc.severity, NORMAL)
        rows = []
        for c in world.cars.values():
            if c.car == inc.car:
                continue                     # the crashed car gets the countdown, not warnings
            dist = upstream_distance(c.track_m, inc.track_m, lap_len)
            if dist > config.ROUTER_MAX_UPSTREAM_M:
                continue
            lvl = level_for(dist, c.speed_mps, max_level)
            eta = eta_s(dist, c.speed_mps)
            rows.append({"car": c.car, "dist_m": round(dist, 1),
                         "speed_kmh": round(c.speed_kmh, 1),
                         "eta_s": None if math.isinf(eta) else round(eta, 1),
                         "warning": lvl})
            cw = out[c.car]
            if lvl > cw.level or (lvl == cw.level and lvl > 0 and cw.dist_m is not None and dist < cw.dist_m):
                out[c.car] = CarWarning(lvl, inc, dist, eta)
        rows.sort(key=lambda r: (r["eta_s"] is None, r["eta_s"] or 0))
        inc.approaching = rows[:8]
    if world.red_confirmed:
        crashed = {i.car for i in world.incidents if i.car is not None}
        for n, cw in out.items():
            if n in crashed:
                continue                     # its phone keeps the countdown / medical screen
            inc = cw.incident or (world.incidents[0] if world.incidents else None)
            dist = cw.dist_m if cw.dist_m is not None else (
                upstream_distance(world.cars[n].track_m, inc.track_m, lap_len) if inc else None)
            v = world.cars[n].speed_mps
            out[n] = CarWarning(RED, inc, dist, eta_s(dist, v) if dist is not None else None)
    return out


def warning_message(msg_id: int, cw: CarWarning) -> dict:
    """The server -> phone "warning" message (docs/protocol.md)."""
    eta = cw.eta_s
    return {
        "type": "warning", "id": msg_id, "level": cw.level,
        "label": config.WARNING_LABELS[cw.level],
        "corner": cw.incident.corner if cw.incident and cw.level else None,
        "dist_m": round(cw.dist_m) if cw.dist_m is not None and cw.level else None,
        "eta_s": round(eta, 1) if eta is not None and not math.isinf(eta) and cw.level else None,
    }
