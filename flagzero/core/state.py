"""In-memory world state: cars, incidents, connections.

One WorldState object lives in the server. The simulation writes car positions,
later modules (router, fusion, medical) write incidents and warnings, and the
server turns it all into the "state" message for dashboards.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


def now_ms() -> int:
    """Server clock in milliseconds. All latency figures use this clock."""
    return int(time.time() * 1000)


# Car states
RUNNING = "RUNNING"
STOPPED = "STOPPED"      # stopped in place by an incident
CAMERA = "CAMERA"        # position currently coming from the overhead camera


@dataclass
class CarState:
    car: int
    track_m: float
    speed_mps: float
    state: str = RUNNING
    has_phone: bool = False
    warning: int = 0                 # 0 NORMAL ... 5 RED (set by the router, A2)
    camera_seen_s: float | None = None   # monotonic time of last camera fix
    pace: float = 1.0                # per-car speed multiplier

    @property
    def speed_kmh(self) -> float:
        return self.speed_mps * 3.6

    def to_msg(self) -> dict[str, Any]:
        return {
            "car": self.car,
            "track_m": round(self.track_m, 1),
            "speed_kmh": round(self.speed_kmh, 1),
            "state": self.state,
            "phone": self.has_phone,
            "warning": self.warning,
        }


@dataclass
class Incident:
    """One real-world incident, possibly seen by several sources.

    sources: one entry per (src, kind), e.g.
      {"src": "IMU", "kind": "IMPACT", "conf": 0.88, "peak_g": 5.2, "ms": ...}
      {"src": "CAMERA", "kind": "STOPPED_VEHICLE", "conf": 0.93, "ms": ...}
      {"src": "NO_RESPONSE", "kind": "TIMEOUT", "conf": None, "ms": ...}
    """
    id: int
    kind: str                        # headline kind: IMPACT, SPIN, DEBRIS, STOPPED_VEHICLE, ...
    track_m: float
    corner: str
    car: int | None = None
    sources: list[dict] = field(default_factory=list)
    severity: int = 0
    fused_conf: float = 0.0
    still: bool = False              # phone reported still:true after the event
    countdown: str | None = None     # None, PENDING, OK, TIMEOUT
    countdown_started_ms: int | None = None
    impact_ms: int | None = None
    medical: str | None = None       # None, MONITOR, URGENT
    detect_ms: int | None = None     # first server receive time (latency, A6)
    approaching: list[dict] = field(default_factory=list)
    created_ms: int = field(default_factory=now_ms)
    updated_ms: int = field(default_factory=now_ms)

    def source(self, src: str, kind: str | None = None) -> dict | None:
        for s in self.sources:
            if s["src"] == src and (kind is None or s["kind"] == kind):
                return s
        return None

    def to_msg(self) -> dict[str, Any]:
        from flagzero import config  # local import avoids a cycle
        return {
            "id": self.id, "kind": self.kind, "track_m": round(self.track_m, 1),
            "corner": self.corner, "car": self.car, "sources": self.sources,
            "severity": self.severity,
            "label": config.SEVERITY_LABELS.get(self.severity, "NORMAL"),
            "fused_conf": round(self.fused_conf, 4),
            "still": self.still, "countdown": self.countdown,
            "countdown_left_s": self.countdown_left_s(),
            "medical": self.medical, "approaching": self.approaching,
            "created_ms": self.created_ms, "updated_ms": self.updated_ms,
        }

    def countdown_left_s(self) -> int | None:
        from flagzero import config
        if self.countdown != "PENDING" or self.countdown_started_ms is None:
            return None
        left = config.COUNTDOWN_S - (now_ms() - self.countdown_started_ms) / 1000
        return max(0, int(round(left)))


@dataclass
class WorldState:
    cars: dict[int, CarState] = field(default_factory=dict)
    incidents: list[Incident] = field(default_factory=list)
    red_pending: bool = False
    red_confirmed: bool = False
    latency: dict[str, Any] = field(default_factory=dict)
    vitals: dict[int, dict] = field(default_factory=dict)   # phone car -> SIM vitals
    last_vision: dict[str, Any] | None = None
    # connections (WebSocket objects); left empty in headless mode
    car_sockets: dict[int, Any] = field(default_factory=dict)
    dash_sockets: set = field(default_factory=set)
    vision_sockets: set = field(default_factory=set)
    _next_incident_id: int = 1

    def new_incident_id(self) -> int:
        i = self._next_incident_id
        self._next_incident_id += 1
        return i

    def state_message(self) -> dict[str, Any]:
        return {
            "type": "state",
            "server_ms": now_ms(),
            "cars": [c.to_msg() for c in sorted(self.cars.values(), key=lambda c: c.car)],
            "incidents": [i.to_msg() for i in self.incidents],
            "latency": self.latency,
            "red_pending": self.red_pending,
            "red_confirmed": self.red_confirmed,
            "vitals": {str(k): v for k, v in self.vitals.items()},
            "phones_connected": sorted(self.car_sockets.keys()),
        }
