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
    """Filled in properly by A3. Fields match docs/protocol.md."""
    id: int
    kind: str                        # IMPACT, SPIN, DEBRIS, STOPPED_VEHICLE, ...
    track_m: float
    corner: str
    car: int | None = None
    sources: list[dict] = field(default_factory=list)
    severity: int = 0
    fused_conf: float = 0.0
    medical: str | None = None       # None, MONITOR, URGENT
    created_ms: int = field(default_factory=now_ms)
    updated_ms: int = field(default_factory=now_ms)

    def to_msg(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "track_m": round(self.track_m, 1),
            "corner": self.corner, "car": self.car, "sources": self.sources,
            "severity": self.severity, "fused_conf": round(self.fused_conf, 4),
            "medical": self.medical, "created_ms": self.created_ms,
            "updated_ms": self.updated_ms,
        }


@dataclass
class WorldState:
    cars: dict[int, CarState] = field(default_factory=dict)
    incidents: list[Incident] = field(default_factory=list)
    red_pending: bool = False
    red_confirmed: bool = False
    latency: dict[str, Any] = field(default_factory=dict)
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
            "phones_connected": sorted(self.car_sockets.keys()),
        }
