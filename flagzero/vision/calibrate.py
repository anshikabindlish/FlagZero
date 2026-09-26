"""Calibrate the paper track: centre line -> 1380-1480 m, corridor width, HSV.

    python flagzero/vision/calibrate.py --index 1          # grab from the iPhone
    python flagzero/vision/calibrate.py --image still.png  # or from a saved still
    python flagzero/vision/calibrate.py --index 1 --load   # edit the saved one

Centre line (default mode)
    left-click   add a point - click ~10 points along the drawn line IN DRIVING ORDER
    u / right-click  undo last point           c  clear all points
Other modes / keys
    [ ]   corridor half-width -/+ 2 px (the green band should cover the track)
    h     HSV mode for colour sticky notes (1 = car 17 pink, 2 = car 8 blue);
          left-click prints the HSV under the cursor
    k     check ArUco markers visible in this still
    g     grab a fresh still from the camera
    s     save to vision/vision_calib.json      q  quit
"""
from __future__ import annotations

import argparse
import copy
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import cv2
import numpy as np

from flagzero.vision import vision_config as C
from flagzero.vision.calib import Calibration
from flagzero.vision.detector import ArucoCarDetector, ColorCarDetector

WIN = "FlagZero calibrate"
TB = "HSV"


def grab(index, proc_width, source=None):
    if source:
        cap = cv2.VideoCapture(str(source))
    else:
        be = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else (
            cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY)
        cap = cv2.VideoCapture(index, be)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    frame = None
    for _ in range(15):                      # skip exposure warm-up frames
        ok, f = cap.read()
        if ok:
            frame = f
        time.sleep(0.03 if not source else 0)
    cap.release()
    if frame is None:
        sys.exit("could not read a frame - run camera_test.py first")
    if frame.shape[1] > proc_width:
        frame = cv2.resize(frame, (proc_width, int(frame.shape[0] * proc_width / frame.shape[1])),
                           interpolation=cv2.INTER_AREA)
    return frame


class Calibrator:
    def __init__(self, frame, existing: Calibration | None):
        self.frame = frame
        self.points = [list(p) for p in existing.points] if existing else []
        self.corridor = existing.corridor_px if existing else 45.0
        self.colors = copy.deepcopy(existing.color_cars) if existing and existing.color_cars \
            else {k: {"lo": list(v["lo"]), "hi": list(v["hi"]), "name": v["name"]}
                  for k, v in C.COLOR_CARS.items()}
        self.mode = "line"
        self.hsv_car = 17
        self.hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        self.mouse = (0, 0)

    # ------------------------------------------------------------ calibration obj
    def cal(self):
        if len(self.points) < 2:
            return None
        h, w = self.frame.shape[:2]
        return Calibration(self.points, corridor_px=self.corridor, frame_size=[w, h],
                           color_cars=self.colors)

    # ------------------------------------------------------------ mouse
    def on_mouse(self, ev, x, y, flags, _):
        self.mouse = (x, y)
        if ev == cv2.EVENT_LBUTTONDOWN:
            if self.mode == "line":
                self.points.append([float(x), float(y)])
            else:
                hh, ss, vv = self.hsv[y, x]
                print(f"HSV at ({x},{y}) = [{hh}, {ss}, {vv}]  "
                      f"-> try lo=[{max(0, hh - 10)}, {max(0, ss - 60)}, {max(0, vv - 60)}] "
                      f"hi=[{min(179, hh + 10)}, 255, 255]")
        elif ev == cv2.EVENT_RBUTTONDOWN and self.mode == "line" and self.points:
            self.points.pop()

    # ------------------------------------------------------------ HSV trackbars
    def open_hsv(self):
        cv2.namedWindow(TB, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(TB, 480, 560)
        cfg = self.colors[self.hsv_car]
        for i, ch in enumerate("HSV"):
            mx = 179 if ch == "H" else 255
            for part, arr in (("lo", cfg["lo"]), ("hi", cfg["hi"])):
                name = f"{ch} {part}"
                try:
                    cv2.getTrackbarPos(name, TB)
                    cv2.setTrackbarPos(name, TB, int(arr[i]))
                except cv2.error:
                    cv2.createTrackbar(name, TB, int(arr[i]), mx, lambda v: None)
                cv2.setTrackbarPos(name, TB, int(arr[i]))

    def read_hsv(self):
        cfg = self.colors[self.hsv_car]
        for i, ch in enumerate("HSV"):
            cfg["lo"][i] = cv2.getTrackbarPos(f"{ch} lo", TB)
            cfg["hi"][i] = cv2.getTrackbarPos(f"{ch} hi", TB)

    # ------------------------------------------------------------ drawing
    def render(self):
        img = self.frame.copy()
        cal = self.cal()
        if cal:
            pts = np.array(cal._p, np.int32).reshape(-1, 1, 2)
            ov = img.copy()
            cv2.polylines(ov, [pts], False, (80, 220, 80), int(2 * self.corridor))
            img = cv2.addWeighted(ov, 0.25, img, 0.75, 0)
            cv2.polylines(img, [pts], False, (0, 170, 0), 2)
            for m in range(int(C.TRACK_START_M // 10 * 10 + 10), int(C.TRACK_END_M) + 1, 10):
                x, y = cal.track_m_to_pixel(m)
                cv2.circle(img, (int(x), int(y)), 3, (0, 100, 0), -1)
                cv2.putText(img, str(m), (int(x) + 4, int(y) - 6), cv2.FONT_HERSHEY_SIMPLEX,
                            0.4, (0, 90, 0), 1, cv2.LINE_AA)
            tx, ty = cal.track_m_to_pixel(C.PAPER_MARK_M)
            cv2.circle(img, (int(tx), int(ty)), 12, (255, 0, 0), 2)
            cv2.putText(img, f"mark {C.PAPER_MARK_M:.0f} m", (int(tx) + 14, int(ty) + 4), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, (255, 0, 0), 2, cv2.LINE_AA)
            m, off = cal.pixel_to_track_m(*self.mouse)
            cursor = f"cursor: {m:.1f} m, {off:.0f}px from line"
        else:
            cursor = "click points along the centre line in driving order"
        for i, (x, y) in enumerate(self.points):
            cv2.circle(img, (int(x), int(y)), 5, (0, 0, 255), -1)
            cv2.putText(img, str(i + 1), (int(x) + 6, int(y) + 14), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, (0, 0, 255), 1, cv2.LINE_AA)
        if self.mode == "hsv":
            self.read_hsv()
            det = ColorCarDetector(self.colors)
            mask = det.mask(self.hsv, self.hsv_car)
            tint = np.zeros_like(img)
            tint[mask > 0] = (255, 0, 255)
            img = cv2.addWeighted(img, 1.0, tint, 0.6, 0)
            for d in det.detect(self.frame, self.hsv):
                cv2.polylines(img, [d.poly.astype(np.int32)], True, (0, 0, 255), 2)
                cv2.putText(img, f"#{d.car}", (int(d.x), int(d.y)), cv2.FONT_HERSHEY_SIMPLEX,
                            0.7, (0, 0, 255), 2)
        lines = [f"mode: {self.mode.upper()}" + (f" (car {self.hsv_car})" if self.mode == 'hsv' else "")
                 + f"   points: {len(self.points)}   corridor: {self.corridor:.0f}px",
                 cursor,
                 "click=add  u=undo  c=clear  [ ]=width  h=HSV  1/2=car  k=ArUco  g=grab  s=save  q=quit"]
        for i, s in enumerate(lines):
            y = 24 + 22 * i
            cv2.putText(img, s, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(img, s, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        return img


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", type=int, default=0)
    ap.add_argument("--image", type=Path)
    ap.add_argument("--source", type=Path, help="take the first frame of a video")
    ap.add_argument("--load", action="store_true", help="start from the saved calibration")
    ap.add_argument("--out", type=Path, default=C.CALIB_PATH)
    ap.add_argument("--proc-width", type=int, default=1280, help="must match vision.py")
    a = ap.parse_args()

    if a.image:
        frame = cv2.imread(str(a.image))
        if frame is None:
            sys.exit(f"can't read {a.image}")
        if frame.shape[1] > a.proc_width:
            frame = cv2.resize(frame, (a.proc_width, int(frame.shape[0] * a.proc_width / frame.shape[1])))
    else:
        frame = grab(a.index, a.proc_width, a.source)
    existing = Calibration.load(a.out) if a.load and a.out.exists() else None
    if existing:
        existing = existing.scaled_to(frame.shape[1], frame.shape[0])
    c = Calibrator(frame, existing)

    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WIN, frame.shape[1], frame.shape[0])
    cv2.setMouseCallback(WIN, c.on_mouse)
    print(__doc__)
    while True:
        cv2.imshow(WIN, c.render())
        k = cv2.waitKey(20) & 0xFF
        if k in (ord("q"), 27):
            break
        elif k == ord("u") and c.points:
            c.points.pop()
        elif k == ord("c"):
            c.points.clear()
        elif k == ord("["):
            c.corridor = max(6.0, c.corridor - 2)
        elif k == ord("]"):
            c.corridor += 2
        elif k == ord("h"):
            if c.mode == "hsv":
                c.mode = "line"
                cv2.destroyWindow(TB)
            else:
                c.mode = "hsv"
                c.open_hsv()
        elif k in (ord("1"), ord("2")) and c.mode == "hsv":
            c.read_hsv()
            c.hsv_car = 17 if k == ord("1") else 8
            c.open_hsv()
        elif k == ord("k"):
            dets = ArucoCarDetector().detect(cv2.cvtColor(c.frame, cv2.COLOR_BGR2GRAY))
            print("ArUco cars visible:", sorted(d.car for d in dets) or "NONE - check print size, "
                  "focus, glare; markers need a white border")
        elif k == ord("g") and not a.image:
            c.frame = grab(a.index, a.proc_width, a.source)
            c.hsv = cv2.cvtColor(c.frame, cv2.COLOR_BGR2HSV)
        elif k == ord("s"):
            cal = c.cal()
            if cal is None:
                print("need at least 2 points")
                continue
            path = cal.save(a.out)
            again = Calibration.load(path)
            tx, ty = again.track_m_to_pixel(C.PAPER_MARK_M)
            print(f"saved {path}\n  {len(again.points)} points, {again.length_px:.0f} px of line, "
                  f"{again.m_per_px:.3f} track-m per px\n"
                  f"  re-projection error of clicked points: {again.reprojection_error_m():.3f} m\n"
                  f"  the {C.PAPER_MARK_M:.0f} m mark is at pixel ({tx:.0f}, {ty:.0f}) - check it's where you expect on the paper")
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
