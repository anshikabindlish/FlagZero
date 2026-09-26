"""Synthetic paper-track video with ground-truth calibration.

Lets you test the whole vision pipeline with no camera, no printer and no paper:
    python flagzero/tools/make_test_video.py
    python flagzero/vision/vision.py --source flagzero/recordings/synthetic.mp4 \
        --calib flagzero/recordings/synthetic_calib.json

Script (t in seconds):
  0-1    both cars parked (reference frame + background warm-up)
  1-5.5  car 17 drives toward T4
  5.5-6.5 car 17 spins 540 deg sliding into T4 and stops facing backwards
  6.8-8.4 car 8 dashes in at ~100 km/h (CLOSING), brakes, stops 10 m short
          (then both stopped within 5 s -> MULTI_STOP)
  10-11.5 a hand sweeps in and drops crumpled paper at ~1455 m
  11.5-16 hand gone, debris sits there (DEBRIS after 2 s)
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import cv2
import numpy as np

from flagzero.vision import vision_config as C
from flagzero.vision.calib import Calibration

W, H, FPS = 1280, 720, 30


def centre_line(n=400):
    xs = np.linspace(110, 1170, n)
    ys = 380 + 150 * np.sin((xs - 110) / 1060 * 1.6 * math.pi) * np.exp(-((xs - 640) / 900) ** 2)
    return np.stack([xs, ys], 1)


def car_sprite(marker_id, body_bgr, size=74):
    d = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, C.ARUCO_DICT))
    gen = getattr(cv2.aruco, "generateImageMarker", None) or cv2.aruco.drawMarker
    m = gen(d, marker_id, 48)
    spr = np.full((size, size, 3), body_bgr, np.uint8)
    cv2.rectangle(spr, (8, 8), (size - 9, size - 9), (255, 255, 255), -1)   # quiet zone
    o = (size - 48) // 2
    spr[o:o + 48, o:o + 48] = cv2.cvtColor(m, cv2.COLOR_GRAY2BGR)
    return spr


def paste_rotated(frame, sprite, cx, cy, heading_deg):
    """Place sprite so its top edge (marker 'up') points along heading_deg."""
    h, w = sprite.shape[:2]
    th = math.radians(heading_deg + 90.0)
    R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    c = np.array([w / 2, h / 2])
    t = np.array([cx, cy]) - R @ c
    M = np.hstack([R, t[:, None]])
    warped = cv2.warpAffine(sprite, M, (W, H), flags=cv2.INTER_LINEAR)
    mask = cv2.warpAffine(np.full((h, w), 255, np.uint8), M, (W, H))
    # soft shadow
    sh = cv2.GaussianBlur(cv2.warpAffine(mask, np.float32([[1, 0, 3], [0, 1, 4]]), (W, H)), (9, 9), 0)
    frame[:] = (frame * (1 - 0.18 * sh[..., None] / 255)).astype(np.uint8)
    frame[mask > 128] = warped[mask > 128]


def build(out_dir: Path, seconds=16.0, seed=3):
    rng = np.random.default_rng(seed)
    line = centre_line()
    clicks = line[np.linspace(0, len(line) - 1, 12).astype(int)]
    cal = Calibration(clicks.tolist(), corridor_px=48, frame_size=[W, H])

    # paper background
    yy, xx = np.mgrid[0:H, 0:W]
    base = (228 + 10 * (xx / W) - 6 * (yy / H)).astype(np.float32)
    paper = np.dstack([base - 4, base, base + 2]).clip(0, 255).astype(np.uint8)
    pts = line.astype(np.int32).reshape(-1, 1, 2)
    cv2.polylines(paper, [pts], False, (40, 40, 40), 5, cv2.LINE_AA)
    # edge lines
    nrm = np.gradient(line, axis=0)
    nrm = np.stack([-nrm[:, 1], nrm[:, 0]], 1) / np.linalg.norm(nrm, axis=1)[:, None]
    for s in (-1, 1):
        e = (line + s * 58 * nrm).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(paper, [e], False, (90, 90, 90), 2, cv2.LINE_AA)
    tx, ty = cal.track_m_to_pixel(C.T4_M)
    cv2.putText(paper, "T4", (int(tx) - 10, int(ty) + 95), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                (60, 60, 60), 2, cv2.LINE_AA)

    s17 = car_sprite(17, (180, 105, 255))   # pink body
    s8 = car_sprite(8, (230, 150, 40))      # blue body

    # debris: crumpled white paper
    deb = np.zeros((70, 80, 3), np.uint8)
    dmask = np.zeros((70, 80), np.uint8)
    poly = np.array([[8, 30], [25, 6], [52, 10], [74, 28], [66, 58], [35, 66], [12, 52]], np.int32)
    cv2.fillPoly(dmask, [poly], 255)
    deb[:] = 250
    for _ in range(9):
        a, b = rng.integers(5, 75, 2), rng.integers(5, 65, 2)
        cv2.line(deb, (int(a[0]), int(b[0])), (int(a[1]), int(b[1])), (175, 175, 180), 2)
    deb = cv2.GaussianBlur(deb, (3, 3), 0)
    dx, dy = cal.track_m_to_pixel(1455.0)

    def lerp(a, b, u):
        u = min(max(u, 0.0), 1.0)
        return a + (b - a) * u

    def ease(u):
        u = min(max(u, 0.0), 1.0)
        return u * u * (3 - 2 * u)

    def car17(t):
        if t < 1.0:
            return 1390.0, 0.0, 0.0
        if t < 5.5:
            return lerp(1390, 1419, (t - 1) / 4.5), 0.0, 0.0
        if t < 6.5:
            u = t - 5.5
            m = 1419 + 4 * math.sin(u * math.pi / 2)
            return m, 540 * ease(u) * 1.0, 14 * ease(u)
        return 1423.0, 540.0, 14.0

    def car8(t):
        if t < 6.8:
            return 1381.0, 0.0
        if t < 7.8:                                  # ~108 km/h dash toward T4
            return lerp(1381, 1409, t - 6.8), 0.0
        if t < 8.4:                                  # hard braking
            return 1409 + 4 * math.sin((t - 7.8) / 0.6 * math.pi / 2), 0.0
        return 1413.0, 0.0

    out_dir.mkdir(parents=True, exist_ok=True)
    vpath = out_dir / "synthetic.mp4"
    vw = cv2.VideoWriter(str(vpath), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    n = int(seconds * FPS)
    for k in range(n):
        t = k / FPS
        f = paper.copy()
        if t >= 11.0:                                            # debris dropped
            x0, y0 = int(dx - 40), int(dy - 35 + 20)
            roi = f[y0:y0 + 70, x0:x0 + 80]
            roi[dmask > 0] = deb[dmask > 0]
        for fn, spr in ((car8, s8), (car17, s17)):
            r = fn(t)
            m, spin = r[0], r[1]
            lat = r[2] if len(r) > 2 else 0.0
            x, y = cal.track_m_to_pixel(m)
            head = cal.tangent_deg(m)
            nx, ny = -math.sin(math.radians(head)), math.cos(math.radians(head))
            paste_rotated(f, spr, x + lat * nx, y + lat * ny, head + spin)
        if 10.0 <= t < 11.5:                                     # hand + arm
            u = (t - 10.0) / 1.5
            hx = int(lerp(W + 150, dx - 120, min(1, u * 1.6)) if u < 0.65 else lerp(dx, W + 250, (u - 0.65) / 0.35))
            hy = int(dy + 10)
            skin = (140, 170, 225)
            cv2.rectangle(f, (hx + 60, hy - 45), (W + 400, hy + 45), skin, -1)
            cv2.ellipse(f, (hx, hy), (95, 70), 0, 0, 360, skin, -1)
            for j in range(4):
                cv2.ellipse(f, (hx - 95, hy - 45 + 30 * j), (35, 12), 0, 0, 360, skin, -1)
        noise = rng.normal(0, 2.0, f.shape).astype(np.int16)
        f = np.clip(f.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        vw.write(f)
    vw.release()
    cpath = cal.save(out_dir / "synthetic_calib.json")
    truth = {"fps": FPS, "events": {
        "spin_start_s": 5.5, "car17_stops_s": 6.5, "car8_dash_s": 6.8, "car8_stops_s": 8.4,
        "hand_s": [10.0, 11.5], "debris_drop_s": 11.0, "debris_track_m": 1455.0}}
    (out_dir / "synthetic_truth.json").write_text(json.dumps(truth, indent=2))
    return vpath, cpath


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(C.RECORDINGS_DIR))
    ap.add_argument("--seconds", type=float, default=16.0)
    a = ap.parse_args()
    v, c = build(Path(a.out), a.seconds)
    print(f"video: {v}\ncalib: {c}")
