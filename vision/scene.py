"""Scene watcher: hand/occlusion check (MOG2) and debris detection (clean
reference frame). Car markers are masked out of both, so moving a car never
counts as a hand and a parked car never counts as debris.
"""
from __future__ import annotations

import cv2
import numpy as np

from flagzero.vision import vision_config as C


class SceneWatcher:
    PROC_W = 480                      # work at a small size: fast and less noise

    def __init__(self, cal):
        self.cal = cal
        self.mog = cv2.createBackgroundSubtractorMOG2(C.MOG2_HISTORY, C.MOG2_VAR_THRESHOLD,
                                                      detectShadows=True)
        self.ref = None               # float32 grey reference, proc size
        self.ref_valid = None         # uint8 mask: 255 where the reference is trusted
        self.corridor = None
        self.scale = 1.0
        self.last_hand_t = -1e9
        self.hand_area_frac = 0.0
        self.debris = []              # candidate tracks
        self._next_id = 1
        self._t_last = None
        self.debug_mask = None

    # ------------------------------------------------------------------
    def reset_reference(self):
        self.ref = None
        self.debris.clear()

    def _prep(self, frame):
        h, w = frame.shape[:2]
        self.scale = self.PROC_W / w
        small = cv2.resize(frame, (self.PROC_W, int(round(h * self.scale))),
                           interpolation=cv2.INTER_AREA)
        g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small
        return small, cv2.GaussianBlur(g, (5, 5), 0)

    def _car_mask(self, shape, car_polys, pad):
        m = np.zeros(shape[:2], np.uint8)
        for poly in car_polys:
            p = (np.asarray(poly, np.float64) * self.scale)
            c = p.mean(axis=0)
            v = p - c
            n = np.linalg.norm(v, axis=1, keepdims=True) + 1e-6
            p = c + v * (1 + pad * self.scale / n)
            cv2.fillConvexPoly(m, p.astype(np.int32), 255)
        return m

    # ------------------------------------------------------------------
    def update(self, frame, t, car_polys=(), car_points=()):
        """Returns (occluded: bool, debris_hazards: list)."""
        dt = 0.0 if self._t_last is None else max(0.0, t - self._t_last)
        self._t_last = t
        small, g = self._prep(frame)
        carm = self._car_mask(g.shape, car_polys, C.DEBRIS_CAR_MASK_PAD_PX)
        if self.corridor is None or self.corridor.shape != g.shape:
            full = self.cal.corridor_mask(frame.shape, C.DEBRIS_CORRIDOR_MARGIN)
            self.corridor = cv2.resize(full, (g.shape[1], g.shape[0]),
                                       interpolation=cv2.INTER_NEAREST)

        # ---- hand check: large moving foreground that isn't a car
        fg = self.mog.apply(small)
        fg = np.where(fg > 200, 255, 0).astype(np.uint8)     # drop shadows (127)
        fg[carm > 0] = 0
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        fg = cv2.dilate(fg, np.ones((7, 7), np.uint8))
        cnts, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        biggest = max((cv2.contourArea(c) for c in cnts), default=0.0)
        self.hand_area_frac = biggest / g.size
        if self.hand_area_frac > C.HAND_MIN_AREA_FRAC:
            self.last_hand_t = t
        occluded = (t - self.last_hand_t) < C.HAND_CLEAR_S

        # ---- reference frame for debris
        if self.ref is None:
            if occluded:
                return occluded, self._report()
            self.ref = g.astype(np.float32)
            self.ref_valid = np.where(carm > 0, 0, 255).astype(np.uint8)
            return occluded, self._report()
        if occluded:
            return occluded, self._report()          # freeze all debris timers

        # pixels a car used to cover at capture time: learn them once uncovered
        learn = (self.ref_valid == 0) & (carm == 0)
        self.ref[learn] = g[learn]
        self.ref_valid[learn] = 255

        diff = cv2.absdiff(g, self.ref.astype(np.uint8))
        mask = np.where(diff > C.DEBRIS_DIFF_THRESHOLD, 255, 0).astype(np.uint8)
        mask[(carm > 0) | (self.ref_valid == 0) | (self.corridor == 0)] = 0
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        self.debug_mask = mask

        # slow adaptation to lighting drift, never where something is found
        quiet = (mask == 0) & (carm == 0)
        a = C.REFERENCE_BLEND
        self.ref[quiet] = (1 - a) * self.ref[quiet] + a * g[quiet]

        s2 = 1.0 / (self.scale * self.scale)
        blobs = []
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            area_full = cv2.contourArea(c) * s2
            if not (C.DEBRIS_MIN_AREA_PX <= area_full <= C.DEBRIS_MAX_AREA_PX):
                continue
            M = cv2.moments(c)
            if M["m00"] == 0:
                continue
            x, y = M["m10"] / M["m00"] / self.scale, M["m01"] / M["m00"] / self.scale
            # an undetected car sitting where a car was last seen is not debris
            if any(np.hypot(x - cx, y - cy) < C.DEBRIS_MATCH_PX * 1.5 for cx, cy in car_points):
                continue
            blobs.append((x, y, area_full, cv2.boundingRect(c)))
        self._match(blobs, t, dt)
        return occluded, self._report()

    # ------------------------------------------------------------------
    def _match(self, blobs, t, dt):
        for d in self.debris:
            d["hit"] = False
        for x, y, area, rect in blobs:
            best = min(self.debris, key=lambda d: np.hypot(d["x"] - x, d["y"] - y), default=None)
            if best is not None and np.hypot(best["x"] - x, best["y"] - y) < C.DEBRIS_MATCH_PX \
                    and not best["hit"]:
                best.update(x=0.7 * best["x"] + 0.3 * x, y=0.7 * best["y"] + 0.3 * y,
                            area=area, last=t, hit=True, rect=rect)
                best["age"] += dt
            else:
                self.debris.append({"id": self._next_id, "x": x, "y": y, "area": area,
                                    "age": 0.0, "last": t, "hit": True, "rect": rect})
                self._next_id += 1
        self.debris = [d for d in self.debris if t - d["last"] < 0.5]

    def _report(self):
        out = []
        for d in self.debris:
            if d["age"] < C.DEBRIS_PERSIST_S:
                continue
            m, off = self.cal.pixel_to_track_m(d["x"], d["y"])
            m, off = float(m), float(off)
            size_ok = 1.0 - abs(np.log(max(d["area"], 1) / 1500.0)) / 4.0
            conf = float(min(C.CONF_MAX, 0.62 + 0.08 * min(1.0, d["age"] - C.DEBRIS_PERSIST_S)
                       + 0.2 * max(0.0, size_ok)))
            out.append({"kind": "DEBRIS", "track_m": round(m, 1), "conf": round(conf, 2),
                        "predicted": False,
                        "detail": {"id": d["id"], "age_s": round(d["age"], 1),
                                   "area_px": int(d["area"]),
                                   "lateral": round(off / max(self.cal.corridor_px, 1), 2),
                                   "xy": [int(d["x"]), int(d["y"])]}})
        return out
