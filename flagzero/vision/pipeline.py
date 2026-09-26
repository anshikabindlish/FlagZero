"""VisionPipeline: frame + time in -> one /ws/vision message out.

No camera, window or network code here - vision.py wraps this with I/O, and the
tests drive it directly with synthetic frames.
"""
from __future__ import annotations

import math
import time

import cv2
import numpy as np

from flagzero.vision import vision_config as C
from flagzero.vision.calib import Calibration
from flagzero.vision.detector import CarDetector
from flagzero.vision.hazards import HazardEngine
from flagzero.vision.scene import SceneWatcher
from flagzero.vision.tracker import Tracker

HAZARD_COLORS = {
    "STOPPED_VEHICLE": (0, 0, 255), "DEBRIS": (0, 140, 255), "MULTI_STOP": (0, 0, 180),
    "SPIN_RISK": (0, 215, 255), "OFF_TRACK": (255, 0, 255), "SLOWING": (0, 255, 255),
    "CLOSING": (255, 128, 0),
}


class VisionPipeline:
    def __init__(self, cal: Calibration, use_aruco=True, use_color=True, debris=True):
        self.cal = cal
        colors = cal.color_cars or None
        self.detector = CarDetector(use_aruco, use_color, colors)
        self.tracker = Tracker(cal)
        self.hazards = HazardEngine(cal)
        self.scene = SceneWatcher(cal)
        self.debris_enabled = debris
        self.last_msg = None
        self.last_dets = []
        self._frame_shape = None

    def reset(self):
        """Forget motion history, re-arm stop detection, retake debris reference."""
        self.tracker.reset()
        self.hazards.reset()
        self.scene.reset_reference()

    def process(self, frame, t: float) -> dict:
        h, w = frame.shape[:2]
        if self._frame_shape != (w, h):
            self._frame_shape = (w, h)
            self.cal = self.cal.scaled_to(w, h)
            self.tracker.cal = self.hazards.cal = self.scene.cal = self.cal
            self.scene.corridor = None
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        dets = self.detector.detect(frame, gray)
        self.last_dets = dets

        # occlusion uses this frame's cars (plus hidden-but-known cars)
        polys = [d.poly for d in dets]
        points = [(d.x, d.y) for d in dets]
        for tr in self.tracker.tracks.values():
            if tr.x is not None and tr.car not in {d.car for d in dets}:
                points.append((tr.x, tr.y))
                if tr.poly is not None:
                    polys.append(tr.poly)
        if self.debris_enabled:
            occluded, debris = self.scene.update(frame, t, polys, points)
        else:
            occluded, debris = self.scene.update(frame, t, polys, points)[0], []

        tracks = self.tracker.update(dets, t, paused=occluded)
        hz = self.hazards.step(tracks, t, debris)
        risk = self.hazards.risk_by_car(hz)

        cars = []
        for tr in sorted(tracks, key=lambda tr: tr.car):
            c = tr.as_msg()
            c["risk"] = round(risk.get(tr.car, 0.0), 2)
            c["visible"] = tr.visible(t)
            cars.append(c)
        self.last_msg = {
            "type": "vision", "camera": C.CAMERA_ID,
            "t_ms": int(time.time() * 1000),
            "cars": cars, "hazards": hz, "occluded": bool(occluded),
        }
        return self.last_msg

    # ------------------------------------------------------------------ drawing
    def draw(self, frame, extra_lines=()):
        img = frame.copy()
        cal = self.cal
        pts = np.array(cal._p, np.int32).reshape(-1, 1, 2)
        overlay = img.copy()
        cv2.polylines(overlay, [pts], False, (80, 200, 80), int(2 * cal.corridor_px))
        img = cv2.addWeighted(overlay, 0.15, img, 0.85, 0)
        cv2.polylines(img, [pts], False, (0, 160, 0), 2)
        for m in range(int(cal.track_start_m // 10 * 10 + 10), int(cal.track_end_m) + 1, 10):
            x, y = cal.track_m_to_pixel(m)
            cv2.circle(img, (int(x), int(y)), 3, (0, 120, 0), -1)
            cv2.putText(img, f"{m}", (int(x) + 5, int(y) - 5), cv2.FONT_HERSHEY_SIMPLEX,
                        0.4, (0, 110, 0), 1, cv2.LINE_AA)
        tx, ty = cal.track_m_to_pixel(C.PAPER_MARK_M)
        cv2.circle(img, (int(tx), int(ty)), 9, (255, 0, 0), 2)
        cv2.putText(img, f"{C.PAPER_MARK_M:.0f} m", (int(tx) + 10, int(ty) + 18), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (255, 0, 0), 2, cv2.LINE_AA)

        msg = self.last_msg or {"cars": [], "hazards": [], "occluded": False}
        car_hz = {}
        for h in msg["hazards"]:
            if "car" in h:
                car_hz.setdefault(h["car"], []).append(h["kind"])
        for tr in self.tracker.tracks.values():
            if tr.x is None or tr.track_m is None:
                continue
            col = (0, 0, 255) if tr.car in car_hz else (255, 255, 255)
            if tr.poly is not None:
                cv2.polylines(img, [tr.poly.astype(np.int32)], True, col, 2)
            x, y = int(tr.x), int(tr.y)
            if tr.heading is not None:

                a = math.radians(tr.heading)
                cv2.arrowedLine(img, (x, y), (int(x + 35 * math.cos(a)), int(y + 35 * math.sin(a))),
                                (0, 255, 0), 2, tipLength=0.35)
            px, py = tr.predicted_point(C.LOOKAHEAD_S, cal) if tr.speed_kmh > C.MOVING_KMH else (x, y)
            cv2.line(img, (x, y), (int(px), int(py)), (255, 0, 255), 1)
            label = f"#{tr.car} {tr.track_m:.1f}m {tr.speed_kmh:.0f}km/h"
            herr = "--" if tr.heading_err is None else f"{tr.heading_err:+.0f}"
            sub = f"yaw {tr.yaw_rate:+.0f} err {herr} lat {tr.lateral:.2f} st {tr.stationary_s:.1f}"
            above = (sorted(self.tracker.tracks).index(tr.car) % 2) == 0
            base_y = y - 62 if above else y + 58
            for i, s in enumerate([label, sub]):
                (tw, _), _ = cv2.getTextSize(s, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
                org = (x - tw // 2, base_y + 16 * i)
                cv2.putText(img, s, org, cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3, cv2.LINE_AA)
                cv2.putText(img, s, org, cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)

        for d in self.scene.debris:
            if d["age"] < 0.3:
                continue
            x, y, w, h = [int(v / self.scene.scale) for v in d["rect"]]
            raised = d["age"] >= C.DEBRIS_PERSIST_S
            cv2.rectangle(img, (x, y), (x + w, y + h), (0, 140, 255) if raised else (180, 180, 180), 2)
            cv2.putText(img, f"debris {d['age']:.1f}s", (x, y - 4), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, (0, 140, 255), 1, cv2.LINE_AA)

        # hazard banner
        y0 = 24
        for h in msg["hazards"][:6]:
            tag = "PRED " if h.get("predicted") else ""
            s = f"{tag}{h['kind']} #{h.get('car', '-')} {h['track_m']:.0f}m {int(h['conf'] * 100)}%"
            (tw, _), _ = cv2.getTextSize(s, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(img, (6, y0 - 20), (18 + tw, y0 + 7), HAZARD_COLORS.get(h["kind"], (0, 0, 255)), -1)
            cv2.putText(img, s, (12, y0), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2, cv2.LINE_AA)
            y0 += 28
        if msg["occluded"]:
            cv2.putText(img, "HAND - TIMERS PAUSED", (img.shape[1] - 330, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2, cv2.LINE_AA)
        yb = img.shape[0] - 10
        for s in reversed(list(extra_lines)):
            cv2.putText(img, s, (10, yb), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(img, s, (10, yb), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
            yb -= 20
        return img
