"""A3: turn phone and camera signals into incidents.

An IMU event and a camera hazard belong to the same incident if they refer to
the same car, or are < 50 m and < 5 s apart. Otherwise a new incident starts.
"""
from __future__ import annotations

from typing import Optional

from flagzero import config
from flagzero.core.state import Incident, WorldState
from flagzero.core.track import Track, upstream_distance

IMU_KINDS = {"KERB", "SPIN", "IMPACT", "SEVERE", "ROLLOVER"}
CAMERA_KINDS = {"STOPPED_VEHICLE", "DEBRIS", "MULTI_STOP",
                # predictive (vision/hazards.py): raised while the car is still moving
                "SPIN_RISK", "CLOSING", "OFF_TRACK", "SLOWING"}
# headline kind priority when several sources agree
KIND_RANK = ["ROLLOVER", "SEVERE", "IMPACT", "MULTI_STOP", "STOPPED_VEHICLE", "SPIN", "SPIN_RISK",
             "OFF_TRACK", "CLOSING", "DEBRIS", "SLOWING", "KERB"]


def _dist(a: float, b: float, lap: float) -> float:
    d = upstream_distance(a, b, lap)
    return min(d, lap - d)


def find_incident(world: WorldState, track: Track, car: Optional[int],
                  track_m: float, t_ms: int) -> Optional[Incident]:
    for inc in world.incidents:
        if car is not None and inc.car == car:
            return inc
    for inc in world.incidents:
        close = _dist(inc.track_m, track_m, track.lap_length_m) < config.ASSOC_MAX_DIST_M
        recent = abs(t_ms - inc.updated_ms) < config.ASSOC_MAX_DT_S * 1000
        if close and recent:
            return inc
    return None


def _upsert_source(inc: Incident, src: dict) -> None:
    old = inc.source(src["src"], src["kind"])
    if old is None:
        inc.sources.append(src)
        return
    if src.get("conf") is not None and old.get("conf") is not None:
        src["conf"] = max(float(old["conf"]), float(src["conf"]))
    old.update(src)


def _headline(inc: Incident) -> str:
    kinds = {s["kind"] for s in inc.sources if s["src"] != "NO_RESPONSE"}
    for k in KIND_RANK:
        if k in kinds:
            return k
    return inc.kind


def _get_or_create(world: WorldState, track: Track, car: Optional[int], track_m: float,
                   kind: str, t_ms: int) -> tuple[Incident, bool]:
    inc = find_incident(world, track, car, track_m, t_ms)
    if inc:
        if inc.car is None and car is not None:
            inc.car = car
        return inc, False
    inc = Incident(id=world.new_incident_id(), kind=kind, track_m=track_m,
                   corner=track.nearest_corner(track_m).name, car=car,
                   created_ms=t_ms, updated_ms=t_ms, detect_ms=t_ms)
    world.incidents.append(inc)
    return inc, True


def handle_imu_event(world: WorldState, sim, msg: dict, t_ms: int) -> list[tuple[int, dict]]:
    """Returns messages to send: (car, message)."""
    out: list[tuple[int, dict]] = []
    car = int(msg["car"])
    cls = str(msg.get("cls", "")).upper()
    if cls not in IMU_KINDS or car not in world.cars:
        return out
    track_m = world.cars[car].track_m
    inc, _ = _get_or_create(world, sim.track, car, track_m, cls, t_ms)
    _upsert_source(inc, {"src": "IMU", "kind": cls, "conf": float(msg.get("conf", 0.5)),
                         "peak_g": msg.get("peak_g"), "ms": t_ms})
    inc.kind = _headline(inc)
    inc.updated_ms = t_ms
    if cls in config.STOPPING_CLASSES:
        sim.stop_car(car)
        inc.impact_ms = inc.impact_ms or t_ms
        if inc.countdown is None:
            inc.countdown = "PENDING"
            inc.countdown_started_ms = t_ms
            out.append((car, {"type": "countdown", "secs": config.COUNTDOWN_S,
                              "peak_g": msg.get("peak_g")}))
    return out


def handle_imu_update(world: WorldState, msg: dict, t_ms: int) -> None:
    car = int(msg["car"])
    for inc in world.incidents:
        if inc.car == car and msg.get("still"):
            inc.still = True
            inc.updated_ms = t_ms


def handle_ok_pressed(world: WorldState, msg: dict, t_ms: int) -> list[tuple[int, dict]]:
    return _countdown_result(world, int(msg["car"]), "OK", t_ms)


def handle_countdown_result(world: WorldState, msg: dict, t_ms: int) -> list[tuple[int, dict]]:
    res = str(msg.get("result", "")).upper()
    if res not in {"OK", "TIMEOUT"}:
        return []
    return _countdown_result(world, int(msg["car"]), res, t_ms)


def _countdown_result(world: WorldState, car: int, result: str, t_ms: int) -> list[tuple[int, dict]]:
    out = []
    for inc in world.incidents:
        if inc.car != car or inc.countdown in ("OK",):
            continue
        if inc.countdown == "TIMEOUT" and result == "TIMEOUT":
            continue
        inc.countdown = result
        inc.updated_ms = t_ms
        if result == "TIMEOUT":
            _upsert_source(inc, {"src": "NO_RESPONSE", "kind": "TIMEOUT", "conf": None, "ms": t_ms})
            inc.medical = "URGENT"
        else:
            inc.sources = [s for s in inc.sources if s["src"] != "NO_RESPONSE"]
            inc.medical = "MONITOR"
        out.append((car, {"type": "medical", "status": inc.medical}))
    return out


def handle_driver_request(world: WorldState, msg: dict, t_ms: int) -> str:
    """Phone buttons after an impact (docs/phone_changes.md). Drivers can escalate and
    inform, but never clear a flag: only race control (dashboard Reset) returns to green.

    RED_FLAG: the driver recommends red -> DRIVER/RED_FLAG source (severity 4, waits for Confirm red).
    FALSE_ALARM: the driver REPORTS it was only a wobble -> DRIVER/FALSE_ALARM source, shown to
    race control on the dashboard. Flags stay exactly as they are.
    """
    car = int(msg["car"])
    req = str(msg.get("request", "")).upper()
    mine = [inc for inc in world.incidents if inc.car == car]
    if req not in ("RED_FLAG", "FALSE_ALARM") or not mine:
        return ""
    for inc in mine:
        _upsert_source(inc, {"src": "DRIVER", "kind": req, "conf": None, "ms": t_ms})
        inc.updated_ms = t_ms
    return "red" if req == "RED_FLAG" else "false_alarm_reported"


def handle_vision(world: WorldState, sim, msg: dict, t_ms: int) -> None:
    """Camera hazards. Car positions were already applied to the sim."""
    for h in msg.get("hazards", []) or []:
        kind = str(h.get("kind", "")).upper()
        if kind not in CAMERA_KINDS or "track_m" not in h:
            continue
        car = h.get("car")
        car = int(car) if car is not None else None
        inc, _ = _get_or_create(world, sim.track, car, float(h["track_m"]), kind, t_ms)
        _upsert_source(inc, {"src": "CAMERA", "kind": kind, "conf": float(h.get("conf", 0.5)), "ms": t_ms,
                             "predicted": bool(h.get("predicted", False))})
        inc.track_m = float(h["track_m"])            # the camera's location is the precise one
        inc.corner = sim.track.nearest_corner(inc.track_m).name
        inc.kind = _headline(inc)
        inc.updated_ms = t_ms
        if kind == "STOPPED_VEHICLE" and car is not None:
            sim.stop_car(car)
