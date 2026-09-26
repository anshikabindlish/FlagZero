"""Generate flagzero/track.json: a generic 4,000 m circuit.

Run from the repo root:  python3 -m flagzero.tools.make_track
Edit the CORNERS / MARSHAL_POSTS lists below and re-run to change the track.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

LAP_LENGTH_M = 4000.0
POINT_SPACING_M = 20.0          # one polyline point every 20 m -> 200 points

# name, lap position (m), target speed (km/h), sightline (m), note
CORNERS = [
    ("T1", 380.0, 95.0, 180.0, "heavy braking after main straight"),
    ("T2", 760.0, 150.0, 220.0, "fast right"),
    ("T3", 1080.0, 185.0, 260.0, "flat-out kink"),
    ("T4", 1423.0, 165.0, 90.0, "blind crest - the demo corner"),
    ("T5", 1880.0, 85.0, 150.0, "hairpin"),
    ("T6", 2450.0, 125.0, 200.0, "medium left"),
    ("T7", 2980.0, 105.0, 170.0, "chicane entry"),
    ("T8", 3560.0, 75.0, 140.0, "last corner onto the main straight"),
]

# id, position (m), covers from (m), covers to (m)
# Post M3 sits before T4 and cannot see over the crest: its coverage stops at 1400 m.
MARSHAL_POSTS = [
    ("M1", 150.0, 0.0, 450.0),
    ("M2", 700.0, 450.0, 950.0),
    ("M3", 1250.0, 950.0, 1400.0),
    ("M4", 1700.0, 1480.0, 2100.0),
    ("M5", 2300.0, 2100.0, 2700.0),
    ("M6", 2850.0, 2700.0, 3250.0),
    ("M7", 3400.0, 3250.0, 3800.0),
    ("M8", 3900.0, 3800.0, 4000.0),
]

PAPER_TRACK = {"from_m": 1380.0, "to_m": 1480.0, "corner": "T4"}


def _raw_shape(t: float) -> tuple[float, float]:
    """A closed, circuit-like curve for t in [0, 2*pi)."""
    x = 1.00 * math.cos(t) + 0.18 * math.cos(2 * t) - 0.08 * math.sin(3 * t)
    y = 0.62 * math.sin(t) + 0.12 * math.sin(2 * t) + 0.07 * math.cos(3 * t)
    return x, y


def build_polyline() -> list[dict]:
    # Dense sampling of the raw shape.
    n = 20000
    raw = [_raw_shape(2 * math.pi * i / n) for i in range(n + 1)]
    cum = [0.0]
    for (x0, y0), (x1, y1) in zip(raw, raw[1:]):
        cum.append(cum[-1] + math.hypot(x1 - x0, y1 - y0))
    scale = LAP_LENGTH_M / cum[-1]

    # Resample every POINT_SPACING_M metres of real distance.
    points = []
    j = 0
    steps = int(LAP_LENGTH_M / POINT_SPACING_M)
    for k in range(steps):
        s = k * POINT_SPACING_M
        target = s / scale
        while cum[j + 1] < target:
            j += 1
        seg = cum[j + 1] - cum[j]
        f = 0.0 if seg == 0 else (target - cum[j]) / seg
        x = (raw[j][0] + f * (raw[j + 1][0] - raw[j][0])) * scale
        y = (raw[j][1] + f * (raw[j + 1][1] - raw[j][1])) * scale
        points.append({"x": round(x, 2), "y": round(y, 2), "s": round(s, 1)})
    return points


def build_track() -> dict:
    return {
        "name": "FlagZero generic circuit",
        "lap_length_m": LAP_LENGTH_M,
        "units": {"x": "m", "y": "m", "s": "m along the lap", "target_kmh": "km/h"},
        "polyline": build_polyline(),
        "corners": [
            {"name": n, "s": s, "target_kmh": v, "sightline_m": sl, "note": note}
            for n, s, v, sl, note in CORNERS
        ],
        "paper_track": PAPER_TRACK,
        "marshal_posts": [
            {"id": i, "s": s, "covers_from_m": a, "covers_to_m": b}
            for i, s, a, b in MARSHAL_POSTS
        ],
    }


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "track.json"
    out.write_text(json.dumps(build_track(), indent=1))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
