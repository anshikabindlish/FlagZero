"""Track loading and lap-distance helpers."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from flagzero import config


def wrap(s: float, lap_len: float) -> float:
    """Bring any lap position into [0, lap_len)."""
    return s % lap_len


def upstream_distance(from_m: float, to_m: float, lap_len: float) -> float:
    """Distance a car at from_m must drive forward to reach to_m.

    Always in [0, lap_len). Handles wraparound: a car at 3,900 m on a 4,000 m
    lap is 150 m from a hazard at 50 m.
    """
    return (to_m - from_m) % lap_len


@dataclass(frozen=True)
class Corner:
    name: str
    s: float
    target_kmh: float
    sightline_m: float


@dataclass(frozen=True)
class Track:
    lap_length_m: float
    corners: tuple[Corner, ...]
    raw: dict

    def corner(self, name: str) -> Corner:
        for c in self.corners:
            if c.name == name:
                return c
        raise KeyError(name)

    def nearest_corner(self, s: float) -> Corner:
        """The corner closest to s in either direction (for labels like 'T4')."""
        def d(c: Corner) -> float:
            fwd = upstream_distance(s, c.s, self.lap_length_m)
            return min(fwd, self.lap_length_m - fwd)
        return min(self.corners, key=d)


def load_track(path: Path | None = None) -> Track:
    data = json.loads((path or config.TRACK_FILE).read_text())
    corners = tuple(
        Corner(c["name"], float(c["s"]), float(c["target_kmh"]), float(c["sightline_m"]))
        for c in data["corners"]
    )
    return Track(float(data["lap_length_m"]), corners, data)


@lru_cache(maxsize=1)
def default_track() -> Track:
    return load_track()
