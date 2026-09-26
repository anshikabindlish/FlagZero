"""The FlagZero engine: everything the server does, with no sockets.

The server passes messages in and sends out whatever comes back as a list of
(car, message) pairs. Tests drive the same engine directly, so the whole
Scene 1 / Scene 2 flow can be checked without phones or a camera.
"""
from __future__ import annotations

from typing import Optional

import logging
import time

from flagzero import config
from flagzero.core import incidents, medical, router, severity
from flagzero.core.latency import LatencyTracker
from flagzero.core.state import WorldState, now_ms
from flagzero.core.track import wrap
from flagzero.sim.sim import Simulation

log = logging.getLogger("flagzero.engine")
Out = list[tuple[int, dict]]


class Engine:
    def __init__(self, world: Optional[WorldState] = None, seed: Optional[int] = config.SIM_SEED):
        self.world = world or WorldState()
        self.sim = Simulation(self.world, seed=seed)
        self.latency = LatencyTracker()
        self._msg_id = 0
        self._sent_level: dict[int, int] = {}
        self._sent_ms: dict[int, int] = {}
        self._warn_ids: dict[int, tuple[int, int]] = {}   # warning id -> (car, sent_ms)
        self._pings: dict[int, tuple[int, int]] = {}      # ping id -> (car, sent_ms)
        self._snapshots: dict[int, dict] = {}              # incident id -> approaching cars at detection

    def _snapshot_incidents(self, t_ms: int) -> None:
        """Freeze who was approaching each incident the first tick after it was detected,
        for the per-incident marshal-vs-FlagZero counterfactual (/api/montecarlo/incident)."""
        for inc in self.world.incidents:
            if inc.id in self._snapshots or not inc.approaching:
                continue
            srcs = {s["src"] for s in inc.sources}
            self._snapshots[inc.id] = {
                "id": inc.id, "kind": inc.kind, "corner": inc.corner, "track_m": inc.track_m, "t_ms": t_ms,
                "sightline_m": self.sim.track.nearest_corner(inc.track_m).sightline_m,
                # how long the first sensor took before it could report: phone capture window or camera
                "detect_s": config.MC_IMU_DETECT_S if "IMU" in srcs else sum(config.MC_CAMERA_DETECT_S) / 2,
                "cars": [(r["car"], r["dist_m"], r["speed_kmh"]) for r in inc.approaching],
                "warn_ms": None,                                  # filled in when a phone acks its first warning
            }

    def incident_snapshot(self, inc_id: Optional[int] = None) -> Optional[dict]:
        if inc_id is None:
            return self._snapshots[max(self._snapshots)] if self._snapshots else None
        return self._snapshots.get(inc_id)

    def next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    # ------------------------------------------------------------ phones
    def on_car(self, car: int, msg: dict, t_ms: Optional[int] = None) -> Out:
        t_ms = now_ms() if t_ms is None else t_ms
        kind = msg.get("type")
        msg.setdefault("car", car)
        out: Out = []
        if kind == "hello":
            self._sent_level.pop(car, None)            # resend its current warning
            log.info("hello from car %s (%s)", car, msg.get("platform"))
        elif kind == "tel":
            self.world.latency.setdefault("tel", {})[str(car)] = {
                "g": msg.get("g"), "gyro": msg.get("gyro"), "ms": t_ms}
        elif kind == "ack":
            self._on_ack(int(msg.get("id", -1)), t_ms)
        elif kind == "imu_event":
            out += incidents.handle_imu_event(self.world, self.sim, msg, t_ms)
        elif kind == "imu_update":
            incidents.handle_imu_update(self.world, msg, t_ms)
        elif kind == "ok_pressed":
            out += incidents.handle_ok_pressed(self.world, msg, t_ms)
            self._resume_if_ok(car)
        elif kind == "countdown_result":
            out += incidents.handle_countdown_result(self.world, msg, t_ms)
            if str(msg.get("result", "")).upper() == "OK":
                self._resume_if_ok(car)
        elif kind == "driver_request":                 # docs/phone_changes.md
            res = incidents.handle_driver_request(self.world, msg, t_ms)
            if res == "clear":
                log.info("car %s: false alarm -> green flag", car)
                out += self.reset(set(self.world.car_sockets) | {car})
            elif res == "red":
                self.sim.stop_car(car)                 # driver wants red: they stay where they are
        elif kind == "green_flag":                     # phone's race-control button = dashboard reset
            out += self.reset(set(self.world.car_sockets) | {car})
        if kind not in ("tel", "ack"):
            log.info("car %s -> %s", car, msg)
        severity.update(self.world)
        return out

    def _resume_if_ok(self, car: int) -> None:
        """Driver pressed I'm OK. Under yellow or green the car drives on; the incident
        and its flags stay out until race control clears them. Not if red is out or
        recommended for this car's incident."""
        severity.update(self.world)
        if self.world.red_confirmed or any(i.car == car and i.severity >= 4 for i in self.world.incidents):
            return
        self.sim.release_car(car)
        log.info("car %s: driver OK -> car rejoins under the current flags", car)

    def _on_ack(self, msg_id: int, t_ms: int) -> None:
        if msg_id in self._pings:
            car, sent = self._pings.pop(msg_id)
            rtt = t_ms - sent
            self.world.latency[f"rtt_car{car}_ms"] = rtt
            self.world.latency["tunnel_rtt_ms"] = rtt
        elif msg_id in self._warn_ids:
            car, _ = self._warn_ids.pop(msg_id)
            row = self.latency.ack(msg_id, t_ms, self.world.latency.get(f"rtt_car{car}_ms"))
            if row:
                self.world.latency["last_detect_to_warn_ms"] = row["detect_to_ack_ms"]
                snap = self.incident_snapshot()          # measured warning time for this incident's replay
                if snap is not None and snap.get("warn_ms") is None:
                    snap["warn_ms"] = row["detect_to_ack_ms"]
                self.world.latency["last_detect_to_sent_ms"] = row["detect_to_sent_ms"]

    def pings(self, connected: set[int], t_ms: Optional[int] = None) -> Out:
        t_ms = now_ms() if t_ms is None else t_ms
        out = []
        for car in connected:
            i = self.next_id()
            self._pings[i] = (car, t_ms)
            out.append((car, {"type": "ping", "id": i}))
        if len(self._pings) > 500:
            for k in sorted(self._pings)[:250]:
                self._pings.pop(k, None)
        return out

    # ------------------------------------------------------------ camera
    def on_vision(self, msg: dict, t_ms: Optional[int] = None, now_s: Optional[float] = None) -> None:
        t_ms = now_ms() if t_ms is None else t_ms
        now_s = time.monotonic() if now_s is None else now_s
        for c in msg.get("cars", []) or []:
            if "car" in c and "track_m" in c:
                self.sim.apply_camera(int(c["car"]), float(c["track_m"]), now_s=now_s)
        incidents.handle_vision(self.world, self.sim, msg, t_ms)
        self.world.last_vision = msg
        severity.update(self.world)

    # ------------------------------------------------------------ race control
    def on_dash(self, msg: dict, connected: set[int]) -> Out:
        kind = msg.get("type")
        log.info("dashboard -> %s", msg)
        if kind == "reset":
            return self.reset(connected)
        if kind == "confirm_red":
            if any(i.severity >= 4 for i in self.world.incidents) or msg.get("force"):
                self.world.red_confirmed = True
                self.world.red_pending = False
            return []
        if kind == "scene":
            out = self.reset(connected)
            self.position_scene(int(msg.get("n", 1)))
            return out
        return []

    def reset(self, connected: set[int]) -> Out:
        w = self.world
        w.incidents.clear()
        w.red_pending = w.red_confirmed = w.red_auto = False
        self.sim.release_all()
        for c in w.cars.values():
            c.warning = 0
        medical.reset()
        self.latency.reset()
        self._sent_level = {car: 0 for car in connected}
        return [(car, {"type": "reset"}) for car in connected]

    def position_scene(self, n: int) -> None:
        """Line car 21 up SCENE_CAR21_BEHIND_S seconds behind car 17, wherever car 17 is on
        the lap, so car 21 is the next car to reach anything that happens to car 17.
        Nothing is tied to a particular corner; every other car stays where it is."""
        cars = self.world.cars
        if 17 not in cars or 21 not in cars:
            return
        dt = 1.0 / config.SERVER_TICK_HZ
        s = cars[17].track_m
        for _ in range(int(config.SCENE_CAR21_BEHIND_S / dt)):   # drive backwards along the speed profile
            s = wrap(s - self.sim.v_allow(s) * dt, self.sim.lap)
        cars[21].track_m = s
        cars[21].speed_mps = self.sim.v_allow(s)
        log.info("scene %d: car 17 at %.0f m, car 21 %.0f s behind at %.0f m", n,
                 cars[17].track_m, config.SCENE_CAR21_BEHIND_S, s)

    # ------------------------------------------------------------ main tick
    def tick(self, dt: float, connected: set[int], now_s: Optional[float] = None,
             t_ms: Optional[int] = None) -> Out:
        t_ms = now_ms() if t_ms is None else t_ms
        self.sim.tick(dt, now_s=now_s)
        out = medical.update(self.world, t_ms)
        severity.update(self.world)
        warnings = router.compute(self.world, self.sim.lap)
        self._snapshot_incidents(t_ms)
        for n, cw in warnings.items():
            self.world.cars[n].warning = cw.level
        for car in connected:
            cw = warnings.get(car)
            if cw is None:
                continue
            last = self._sent_level.get(car)
            due = last != cw.level or (cw.level > 0 and t_ms - self._sent_ms.get(car, 0) >= config.ROUTER_RESEND_S * 1000)
            if not due:
                continue
            i = self.next_id()
            out.append((car, router.warning_message(i, cw)))
            self._sent_level[car] = cw.level
            self._sent_ms[car] = t_ms
            self._warn_ids[i] = (car, t_ms)
            self.latency.warning_sent(i, cw.incident, car, cw.level, t_ms)
        if len(self._warn_ids) > 2000:
            for k in sorted(self._warn_ids)[:1000]:
                self._warn_ids.pop(k, None)
        return out
