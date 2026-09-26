"""Per-car kinematics from detections: track position, speed, heading, yaw rate,
lateral offset, stillness. Time `t` is always passed in (seconds), so the same
code runs on a live camera, a recorded file at any speed, and in unit tests.
"""
from __future__ import annotations

import math
from collections import deque

from flagzero.vision import vision_config as C
from flagzero.vision.calib import Calibration, wrap180
from flagzero.vision.detector import Detection


class CarTrack:
    def __init__(self, car: int):
        self.car = car
        self.reset_motion()

    def reset_motion(self):
        self.x = self.y = None
        self.t_last = None
        self.last_seen = -1e9
        self.hist = deque()              # (t, x_raw, y_raw, track_m)
        self.track_m = None
        self.offset_px = 0.0
        self.lateral = 0.0
        self.vx = self.vy = 0.0          # px/s, smoothed
        self.speed_px_s = 0.0
        self.speed_ms = 0.0              # track metres / s (2D, unsigned)
        self.v_along = 0.0               # track metres / s, signed along the lap
        self.v_lat = 0.0                 # px / s toward the left edge (+) / right (-)
        self.decel_kmh_s = 0.0
        self._prev_kmh = 0.0
        self.heading = None              # nose direction, image deg
        self.heading_err = None          # vs track tangent, deg, signed
        self._yaw_ref = None             # last angle used for yaw (heading or box)
        self._yaw_mod = 360.0
        self.yaw_rate = 0.0              # deg/s, smoothed
        self.still = False
        self.stationary_s = 0.0
        self.travelled_m = 0.0
        self.armed = not C.REQUIRE_MOVE_BEFORE_STOP
        self.quality = 0.0
        self.source = ""
        self.poly = None
        self.seen_now = False

    # ------------------------------------------------------------------
    @property
    def speed_kmh(self):
        return self.speed_ms * 3.6 * C.SPEED_DISPLAY_SCALE

    def visible(self, t):
        return self.seen_now

    def lost(self, t):
        return t - self.last_seen > C.LOST_TIMEOUT_S

    def update(self, det: Detection, t: float, cal: Calibration, paused: bool = False):
        dt = 0.0 if self.t_last is None else max(1e-3, t - self.t_last)
        first = self.x is None
        if first:
            self.x, self.y = det.x, det.y
        else:
            a = C.POS_EMA
            self.x = a * det.x + (1 - a) * self.x
            self.y = a * det.y + (1 - a) * self.y
        self.quality, self.source, self.poly = det.quality, det.source, det.poly

        m, off, _ = cal.project(self.x, self.y)
        if self.track_m is not None:
            self.travelled_m += abs(m - self.track_m)
            if self.travelled_m >= C.ARM_MOVE_M:
                self.armed = True
        self.track_m, self.offset_px = m, off
        self.lateral = abs(off) / max(cal.corridor_px, 1e-6)

        # --- velocity from the raw history over ~0.25 s (robust to jitter)
        self.hist.append((t, det.x, det.y, m, off))
        while self.hist and t - self.hist[0][0] > max(C.STILL_WINDOW_S, 0.3):
            self.hist.popleft()
        vx = vy = va = vl = 0.0
        ref = next((h for h in self.hist if t - h[0] <= 0.25), None)
        if ref is not None and t - ref[0] > 1e-3:
            span = t - ref[0]
            vx, vy = (det.x - ref[1]) / span, (det.y - ref[2]) / span
            va = (m - ref[3]) / span
            vl = (off - ref[4]) / span
        s = C.SPEED_EMA
        if first:
            self.vx = self.vy = self.v_along = self.v_lat = 0.0
        else:
            self.vx = s * vx + (1 - s) * self.vx
            self.vy = s * vy + (1 - s) * self.vy
            self.v_along = s * va + (1 - s) * self.v_along
            self.v_lat = s * vl + (1 - s) * self.v_lat
        self.speed_px_s = math.hypot(self.vx, self.vy)
        self.speed_ms = self.speed_px_s * cal.m_per_px
        kmh = self.speed_kmh
        if dt > 0:
            inst = (self._prev_kmh - kmh) / dt
            self.decel_kmh_s = 0.5 * inst + 0.5 * self.decel_kmh_s
        self._prev_kmh = kmh

        # --- heading + yaw rate
        if det.heading_deg is not None:
            ang, mod = det.heading_deg, 360.0
            self.heading = det.heading_deg
            self.heading_err = wrap180(det.heading_deg - cal.tangent_deg(m))
        else:
            ang, mod = det.box_angle_deg, 90.0
            self.heading = None
            self.heading_err = None
        if ang is not None and self._yaw_ref is not None and mod == self._yaw_mod and dt > 0:
            d = (ang - self._yaw_ref + mod / 2) % mod - mod / 2
            self.yaw_rate = C.YAW_EMA * (d / dt) + (1 - C.YAW_EMA) * self.yaw_rate
        elif dt > 0:
            self.yaw_rate *= (1 - C.YAW_EMA)
        self._yaw_ref, self._yaw_mod = ang, mod

        # --- stillness: nothing in the last STILL_WINDOW_S moved > STILL_PX
        window = [h for h in self.hist if t - h[0] <= C.STILL_WINDOW_S]
        span_ok = window and (t - window[0][0]) >= C.STILL_WINDOW_S * 0.8
        max_move = max((math.hypot(det.x - h[1], det.y - h[2]) for h in window), default=0)
        still_now = bool(span_ok and max_move < C.STILL_PX)
        if still_now:
            if not self.still:
                self.stationary_s = C.STILL_WINDOW_S   # it has already been still that long
            elif not paused:
                self.stationary_s += dt
        else:
            self.stationary_s = 0.0
        self.still = still_now
        self.t_last = t
        self.last_seen = t
        self.seen_now = True

    def coast(self, t: float, paused: bool):
        """Car not seen this frame. While a hand hides it, freeze everything."""
        if self.t_last is None:
            return
        dt = t - self.t_last
        self.t_last = t
        self.seen_now = False
        if paused:
            self.last_seen += dt         # a hidden car is not a lost car
        else:
            self.yaw_rate *= 0.8
            self.vx *= 0.8
            self.vy *= 0.8

    def predicted_offset_px(self, horizon_s: float):
        """Track-relative extrapolation: follows the curve, so a car that is
        simply going round a bend is never predicted to leave the track."""
        return self.offset_px + self.v_lat * horizon_s

    def predicted_lateral(self, horizon_s: float, cal):
        return abs(self.predicted_offset_px(horizon_s)) / max(cal.corridor_px, 1e-6)

    def predicted_point(self, horizon_s: float, cal):
        m = self.track_m + self.v_along * horizon_s
        x, y = cal.track_m_to_pixel(m)
        a = math.radians(cal.tangent_deg(m))
        off = self.predicted_offset_px(horizon_s)
        # project() reports offset > 0 on the image-left of the driving direction
        return x + off * math.sin(a), y - off * math.cos(a)

    def as_msg(self):
        r = lambda v, n=1: None if v is None else round(float(v), n)
        return {
            "car": self.car,
            "track_m": r(self.track_m),
            "speed_px_s": r(self.speed_px_s),
            "speed_kmh": r(self.speed_kmh),
            "stationary_s": r(self.stationary_s),
            "heading_err_deg": r(self.heading_err),
            "yaw_rate_dps": r(self.yaw_rate),
            "lateral": r(self.lateral, 2),
            "off_track": bool(self.lateral > C.OFF_TRACK_LATERAL),
            "src": self.source,
        }


class Tracker:
    def __init__(self, cal: Calibration):
        self.cal = cal
        self.tracks: dict[int, CarTrack] = {}

    def update(self, dets, t, paused=False):
        seen = set()
        for d in dets:
            tr = self.tracks.setdefault(d.car, CarTrack(d.car))
            if tr.lost(t) and tr.t_last is not None:
                keep_armed = tr.armed
                tr.reset_motion()
                tr.armed = keep_armed
            tr.update(d, t, self.cal, paused)
            seen.add(d.car)
        for car, tr in self.tracks.items():
            if car not in seen:
                tr.coast(t, paused)
        return self.active(t)

    def active(self, t):
        return [tr for tr in self.tracks.values()
                if tr.track_m is not None and not tr.lost(t)]

    def reset(self):
        self.tracks.clear()
