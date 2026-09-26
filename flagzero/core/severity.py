"""A3: severity levels 0-4 and the recommended response.

0  nothing
1  IMU KERB, or any single source with confidence < 0.6
2  SPIN; mild IMPACT; camera STOPPED_VEHICLE alone; DEBRIS
3  strong IMPACT or ROLLOVER; IMPACT followed by still:true; MULTI_STOP
4  level 3 AND countdown TIMEOUT AND camera confirms a stationary vehicle
   (at least 3 corroborating signals)  -> RED FLAG RECOMMENDED

Team change: with AUTO_RED_ON_TIMEOUT, a driver who does not press I'm OK
within COUNTDOWN_S (15 s) makes the incident severity 4 on its own, and RED
goes out automatically. Race control can still Confirm red by hand for any
other severity-4 incident.
"""
from __future__ import annotations

from flagzero import config
from flagzero.core.fusion import best_by_source
from flagzero.core.state import Incident, WorldState

RESPONSES = {
    0: "No action",
    1: "Caution to approaching cars",
    2: "Local yellow flag",
    3: "Double yellow / slow zone; prepare recovery",
    4: "RED FLAG RECOMMENDED - confirm to stop the session; dispatch medical",
}


def severity(inc: Incident) -> int:
    imu = [s for s in inc.sources if s["src"] == "IMU"]
    cam = [s for s in inc.sources if s["src"] == "CAMERA"]
    imu_kinds = {s["kind"] for s in imu}
    cam_kinds = {s["kind"] for s in cam}
    strong_impact = any(
        s["kind"] == "SEVERE" or (s["kind"] == "IMPACT" and (s.get("peak_g") or 0) >= config.IMPACT_STRONG_G)
        for s in imu)
    any_impact = bool(imu_kinds & {"IMPACT", "SEVERE"})

    lvl = 0
    if "KERB" in imu_kinds:
        lvl = max(lvl, 1)
    if imu_kinds & {"SPIN", "IMPACT"} or cam_kinds & {"STOPPED_VEHICLE", "DEBRIS"}:
        lvl = max(lvl, 2)
    if strong_impact or "ROLLOVER" in imu_kinds or "MULTI_STOP" in cam_kinds \
            or (any_impact and inc.still):
        lvl = max(lvl, 3)

    # a lone low-confidence source is capped at CAUTION
    best = best_by_source(inc)
    if len(best) == 1 and next(iter(best.values())) < config.LOW_CONF:
        lvl = min(lvl, 1) if lvl else 1

    corroborating = int(bool(imu)) + int("STOPPED_VEHICLE" in cam_kinds or "MULTI_STOP" in cam_kinds) \
        + int(inc.countdown == "TIMEOUT")
    if lvl >= 3 and inc.countdown == "TIMEOUT" and corroborating >= 3:
        lvl = 4
    if config.AUTO_RED_ON_TIMEOUT and inc.countdown == "TIMEOUT":
        lvl = 4
    return lvl


def update(world: WorldState) -> None:
    from flagzero.core.fusion import fused_confidence
    for inc in world.incidents:
        inc.fused_conf = fused_confidence(inc)
        inc.severity = severity(inc)
    if config.AUTO_RED_ON_TIMEOUT and any(i.countdown == "TIMEOUT" for i in world.incidents) \
            and not world.red_confirmed:
        world.red_confirmed = True
        world.red_auto = True
    world.red_pending = any(i.severity >= 4 for i in world.incidents) and not world.red_confirmed
