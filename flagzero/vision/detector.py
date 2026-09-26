"""Car detection: ArUco markers (preferred) with a colour sticky-note fallback.

ArUco gives id + full 360 deg heading + a clean centre in one call.
Print markers with vision/markers.py; the arrow on the print = the car's nose.
Colour fallback: HSV blob per car; draw a dark dot near the note's front edge
to get a heading, otherwise only the yaw *rate* (from the box angle) is known.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from flagzero.vision import vision_config as C


@dataclass
class Detection:
    car: int
    x: float
    y: float
    heading_deg: float | None      # image degrees, nose direction; None = unknown
    box_angle_deg: float | None    # minAreaRect angle (mod 90), for yaw rate only
    quality: float                 # 0..1
    poly: np.ndarray               # 4x2 outline for drawing / masking
    source: str                    # "aruco" | "color"


def _aruco_dict():
    return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, C.ARUCO_DICT))


class ArucoCarDetector:
    def __init__(self, marker_to_car=None):
        self.marker_to_car = dict(marker_to_car or C.MARKER_TO_CAR)
        d = _aruco_dict()
        if hasattr(cv2.aruco, "ArucoDetector"):          # OpenCV >= 4.7
            p = cv2.aruco.DetectorParameters()
            p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
            self._det = cv2.aruco.ArucoDetector(d, p)
            self._legacy = None
        else:                                              # OpenCV 4.5/4.6
            self._det = None
            self._legacy = (d, cv2.aruco.DetectorParameters_create())

    def detect(self, gray) -> list[Detection]:
        if self._det is not None:
            corners, ids, _ = self._det.detectMarkers(gray)
        else:
            corners, ids, _ = cv2.aruco.detectMarkers(gray, self._legacy[0],
                                                      parameters=self._legacy[1])
        out = []
        if ids is None:
            return out
        for c, i in zip(corners, ids.flatten()):
            car = self.marker_to_car.get(int(i))
            if car is None:
                continue
            q = c.reshape(4, 2).astype(np.float64)       # TL, TR, BR, BL
            ctr = q.mean(axis=0)
            top_mid = (q[0] + q[1]) / 2
            heading = math.degrees(math.atan2(top_mid[1] - ctr[1], top_mid[0] - ctr[0]))
            side = np.mean(np.hypot(*(np.roll(q, -1, 0) - q).T))
            quality = float(min(1.0, side / 40.0))         # bigger marker = better
            out.append(Detection(car, float(ctr[0]), float(ctr[1]), heading, None,
                                 quality, q, "aruco"))
        return out


def _hsv_mask(hsv, lo, hi):
    lo = np.array(lo, np.uint8)
    hi = np.array(hi, np.uint8)
    if lo[0] <= hi[0]:
        return cv2.inRange(hsv, lo, hi)
    # hue wraps (e.g. pink/red: 160..10)
    a = cv2.inRange(hsv, lo, np.array([179, hi[1], hi[2]], np.uint8))
    b = cv2.inRange(hsv, np.array([0, lo[1], lo[2]], np.uint8), hi)
    return a | b


class ColorCarDetector:
    def __init__(self, color_cars=None):
        self.color_cars = {int(k): v for k, v in (color_cars or C.COLOR_CARS).items()}
        self._k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

    def mask(self, hsv, car):
        cfg = self.color_cars[car]
        m = _hsv_mask(hsv, cfg["lo"], cfg["hi"])
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, self._k)
        return cv2.morphologyEx(m, cv2.MORPH_CLOSE, self._k, iterations=2)

    def detect(self, bgr, hsv=None, skip_cars=()) -> list[Detection]:
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV) if hsv is None else hsv
        out = []
        for car in self.color_cars:
            if car in skip_cars:
                continue
            m = self.mask(hsv, car)
            cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not cnts:
                continue
            cnt = max(cnts, key=cv2.contourArea)
            area = cv2.contourArea(cnt)
            if area < C.COLOR_MIN_AREA_PX:
                continue
            (cx, cy), (w, h), ang = cv2.minAreaRect(cnt)
            box = cv2.boxPoints(((cx, cy), (w, h), ang))
            fill = area / max(w * h, 1.0)                  # squareness of the blob
            quality = float(min(1.0, 0.5 * fill + 0.5 * min(1.0, area / 1500.0)))
            heading = self._nose_heading(hsv, cnt, cx, cy)
            out.append(Detection(car, float(cx), float(cy), heading, float(ang % 90.0),
                                 quality, box.astype(np.float64), "color"))
        return out

    @staticmethod
    def _nose_heading(hsv, cnt, cx, cy):
        x, y, w, h = cv2.boundingRect(cnt)
        roi_mask = np.zeros((h, w), np.uint8)
        cv2.drawContours(roi_mask, [cnt - [x, y]], -1, 255, -1, offset=(0, 0))
        roi_mask = cv2.erode(roi_mask, None, iterations=2)
        v = hsv[y:y + h, x:x + w, 2]
        dark = (v < C.NOSE_DOT_MAX_V) & (roi_mask > 0)
        if dark.sum() < 12:
            return None
        ys, xs = np.nonzero(dark)
        dx, dy = xs.mean() + x - cx, ys.mean() + y - cy
        if math.hypot(dx, dy) < 3:
            return None
        return math.degrees(math.atan2(dy, dx))


class CarDetector:
    """ArUco first; colour only for cars ArUco didn't find."""

    def __init__(self, use_aruco=True, use_color=True, color_cars=None, marker_to_car=None):
        self.aruco = ArucoCarDetector(marker_to_car) if use_aruco else None
        self.color = ColorCarDetector(color_cars) if use_color else None

    def detect(self, bgr, gray=None) -> list[Detection]:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if gray is None else gray
        dets = self.aruco.detect(gray) if self.aruco else []
        if self.color:
            seen = {d.car for d in dets}
            dets += self.color.detect(bgr, skip_cars=seen)
        return dets
