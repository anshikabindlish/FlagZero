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
        elif kind == "countdown_result":
            out += incidents.handle_countdown_result(self.world, msg, t_ms)
        if kind not in ("tel", "ack"):
            log.info("car %s -> %s", car, msg)
        severity.update(self.world)
        return out

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
        """Put the phone cars where each demo scene needs them."""
        t4 = self.sim.track.corner("T4").s
        lap = self.sim.lap
        cars = self.world.cars
        if 21 in cars:
            cars[21].track_m = wrap(t4 - config.SCENE_CAR21_BEFORE_T4_M, lap)
            cars[21].speed_mps = self.sim.v_allow(cars[21].track_m)
        if 17 in cars:
            cars[17].track_m = wrap(config.SCENE_CAR17_AT_M, lap)
            cars[17].speed_mps = self.sim.v_allow(cars[17].track_m)
        log.info("scene %d: car 21 at %.0f m, car 17 at %.0f m", n,
                 cars[21].track_m if 21 in cars else -1, cars[17].track_m if 17 in cars else -1)

    # ------------------------------------------------------------ main tick
    def tick(self, dt: float, connected: set[int], now_s: Optional[float] = None,
             t_ms: Optional[int] = None) -> Out:
        t_ms = now_ms() if t_ms is None else t_ms
        self.sim.tick(dt, now_s=now_s)
        out = medical.update(self.world, t_ms)
        severity.update(self.world)
        warnings = router.compute(self.world, self.sim.lap)
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
