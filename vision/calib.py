"""Calibration model: clicked centre line -> track metres, heading, corridor.

Pure geometry + JSON load/save. No windows here (that's calibrate.py), so the
same object is used by vision.py, the tests and the synthetic video generator.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from flagzero.vision import vision_config as C


@dataclass
class Calibration:
    points: list                       # [[x, y], ...] centre line in driving order
    track_start_m: float = C.TRACK_START_M
    track_end_m: float = C.TRACK_END_M
    corridor_px: float = 40.0          # half-width of the track, pixels
    frame_size: list = field(default_factory=lambda: [1280, 720])  # [w, h]
    color_cars: dict = field(default_factory=dict)  # car -> {"lo":[..],"hi":[..]}

    def __post_init__(self):
        pts = np.asarray(self.points, dtype=np.float64)
        if pts.ndim != 2 or len(pts) < 2:
            raise ValueError("calibration needs at least 2 centre-line points")
        step = np.hypot(*(pts[1:] - pts[:-1]).T)
        pts = pts[np.concatenate([[True], step > 1e-9])]   # drop double clicks
        if len(pts) < 2:
            raise ValueError("calibration points are all identical")
        self._p = pts
        seg = pts[1:] - pts[:-1]
        self._seg = seg
        self._seg_len = np.hypot(seg[:, 0], seg[:, 1])
        self._cum = np.concatenate([[0.0], np.cumsum(self._seg_len)])
        self.length_px = float(self._cum[-1])
        self.m_per_px = (self.track_end_m - self.track_start_m) / self.length_px

    # ------------------------------------------------------------ projection
    def project(self, x: float, y: float):
        """Nearest point on the polyline.

        Returns (track_m, signed_offset_px, seg_index). Offset > 0 = left of
        the driving direction in image coordinates.
        """
        p = np.array([x, y], dtype=np.float64)
        a = self._p[:-1]
        d = p - a
        t = np.clip(np.einsum("ij,ij->i", d, self._seg) / self._seg_len ** 2, 0.0, 1.0)
        proj = a + self._seg * t[:, None]
        dist = np.hypot(*(p - proj).T)
        i = int(np.argmin(dist))
        along = self._cum[i] + t[i] * self._seg_len[i]
        s = self._seg[i]
        cross = s[0] * (p[1] - proj[i][1]) - s[1] * (p[0] - proj[i][0])
        signed = float(dist[i]) * (-1.0 if cross > 0 else 1.0)
        return self.track_start_m + along * self.m_per_px, signed, i

    def pixel_to_track_m(self, x: float, y: float):
        """(track_m, dist_from_line_px) - the function named in the build spec."""
        m, off, _ = self.project(x, y)
        return m, abs(off)

    def track_m_to_pixel(self, track_m: float):
        along = (track_m - self.track_start_m) / self.m_per_px
        along = min(max(along, 0.0), self.length_px)
        i = int(np.searchsorted(self._cum, along, side="right") - 1)
        i = min(max(i, 0), len(self._seg) - 1)
        t = (along - self._cum[i]) / self._seg_len[i]
        q = self._p[i] + self._seg[i] * t
        return float(q[0]), float(q[1])

    def tangent_deg(self, track_m: float) -> float:
        """Driving direction of the centre line at track_m, image degrees
        (0 = +x / right, 90 = +y / down)."""
        along = (track_m - self.track_start_m) / self.m_per_px
        # average over a short window so kinks between clicks don't jump
        win = max(8.0, self.length_px * 0.02)
        a0 = self.track_m_to_pixel(self.track_start_m + (along - win) * self.m_per_px)
        a1 = self.track_m_to_pixel(self.track_start_m + (along + win) * self.m_per_px)
        return math.degrees(math.atan2(a1[1] - a0[1], a1[0] - a0[0]))

    def lateral(self, x: float, y: float) -> float:
        """0 on the centre line, 1.0 at the corridor edge."""
        _, off = self.pixel_to_track_m(x, y)
        return off / max(self.corridor_px, 1e-6)

    def corridor_mask(self, shape, scale: float = 1.0):
        import cv2
        mask = np.zeros(shape[:2], np.uint8)
        pts = self._p.astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(mask, [pts], False, 255, int(max(2, 2 * self.corridor_px * scale)))
        return mask

    def reprojection_error_m(self) -> float:
        """Sanity check: clicked points should project back onto themselves."""
        errs = []
        for i, (x, y) in enumerate(self._p):
            m, _ = self.pixel_to_track_m(x, y)
            expect = self.track_start_m + self._cum[i] * self.m_per_px
            errs.append(abs(m - expect))
        return float(max(errs))

    # ------------------------------------------------------------ io
    def to_dict(self):
        return {
            "points": [[float(x), float(y)] for x, y in self._p],
            "track_start_m": self.track_start_m,
            "track_end_m": self.track_end_m,
            "corridor_px": float(self.corridor_px),
            "frame_size": list(self.frame_size),
            "color_cars": {str(k): v for k, v in self.color_cars.items()},
        }

    def save(self, path: Path = C.CALIB_PATH):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))
        return path

    @classmethod
    def load(cls, path: Path = C.CALIB_PATH) -> "Calibration":
        d = json.loads(Path(path).read_text())
        cc = {int(k): v for k, v in d.get("color_cars", {}).items()}
        return cls(d["points"], d.get("track_start_m", C.TRACK_START_M),
                   d.get("track_end_m", C.TRACK_END_M), d.get("corridor_px", 40.0),
                   d.get("frame_size", [1280, 720]), cc)

    def scaled_to(self, w: int, h: int) -> "Calibration":
        """Same calibration for a different frame resolution."""
        fw, fh = self.frame_size
        if (fw, fh) == (w, h):
            return self
        sx, sy = w / fw, h / fh
        pts = (self._p * [sx, sy]).tolist()
        return Calibration(pts, self.track_start_m, self.track_end_m,
                           self.corridor_px * (sx + sy) / 2, [w, h], self.color_cars)


def wrap180(deg: float) -> float:
    return (deg + 180.0) % 360.0 - 180.0
