"""Vision tests: geometry, ArUco heading, prediction logic, debris/hand, full clip.

    pytest flagzero/tests/test_vision.py -q
    FZ_SLOW=1 pytest flagzero/tests/test_vision.py -q    # also build + run the synthetic clip
"""
import math
import os
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flagzero.vision import vision_config as C
from flagzero.vision.calib import Calibration, wrap180
from flagzero.vision.detector import ArucoCarDetector, Detection
from flagzero.vision.hazards import HazardEngine
from flagzero.vision.scene import SceneWatcher
from flagzero.vision.tracker import Tracker

FPS = 30.0


# ---------------------------------------------------------------- helpers
def straight_cal():
    # 1000 px straight line, left -> right: 10 px per track metre
    return Calibration([[100, 300], [600, 300], [1100, 300]], corridor_px=40, frame_size=[1200, 600])


def curve_cal():
    xs = np.linspace(100, 1100, 30)
    ys = 300 + 150 * np.sin((xs - 100) / 1000 * 2 * math.pi)
    return Calibration(np.stack([xs, ys], 1).tolist(), corridor_px=40, frame_size=[1200, 600])


def det(car, x, y, heading):
    s = 20
    poly = np.array([[x - s, y - s], [x + s, y - s], [x + s, y + s], [x - s, y + s]], float)
    return Detection(car, x, y, heading, None, 1.0, poly, "aruco")


class Sim:
    """Drive Tracker + HazardEngine with fake detections, no images."""

    def __init__(self, cal):
        self.cal, self.tr, self.hz = cal, Tracker(cal), HazardEngine(cal)
        self.t = 0.0
        self.log = []

    def step(self, dets, paused=False):
        tracks = self.tr.update(dets, self.t, paused)
        h = self.hz.step(tracks, self.t)
        self.log.append((self.t, h))
        self.t += 1 / FPS
        return h

    def first(self, kind, car=None):
        for t, hs in self.log:
            for h in hs:
                if h["kind"] == kind and (car is None or h.get("car") == car):
                    return t, h
        return None, None


def on_track(cal, m, lateral_px=0.0, spin=0.0):
    x, y = cal.track_m_to_pixel(m)
    a = math.radians(cal.tangent_deg(m))
    return x + lateral_px * math.sin(a), y - lateral_px * math.cos(a), cal.tangent_deg(m) + spin


# ---------------------------------------------------------------- calibration
def test_pixel_to_track_m_straight():
    cal = straight_cal()
    assert cal.pixel_to_track_m(100, 300)[0] == pytest.approx(1380)
    assert cal.pixel_to_track_m(1100, 300)[0] == pytest.approx(1480)
    m, off = cal.pixel_to_track_m(530, 320)
    assert m == pytest.approx(1423) and off == pytest.approx(20)
    assert cal.lateral(530, 340) == pytest.approx(1.0)
    assert cal.tangent_deg(1423) == pytest.approx(0, abs=1e-6)
    assert cal.reprojection_error_m() < 1e-9


def test_track_m_roundtrip_and_io(tmp_path):
    cal = curve_cal()
    for m in (1381, 1400, 1423, 1466, 1479):
        x, y = cal.track_m_to_pixel(m)
        assert cal.pixel_to_track_m(x, y)[0] == pytest.approx(m, abs=0.05)
    p = cal.save(tmp_path / "c.json")
    again = Calibration.load(p)
    assert again.pixel_to_track_m(640, 300)[0] == pytest.approx(cal.pixel_to_track_m(640, 300)[0])
    half = cal.scaled_to(600, 300)
    x, y = cal.track_m_to_pixel(1423)
    assert half.pixel_to_track_m(x / 2, y / 2)[0] == pytest.approx(1423, abs=0.1)


def test_signed_offset_matches_predicted_point():
    cal = curve_cal()
    x, y, _ = on_track(cal, 1430, lateral_px=25)
    m, off, _ = cal.project(x, y)
    assert m == pytest.approx(1430, abs=0.3) and off == pytest.approx(25, abs=0.5)


def test_wrap180():
    assert wrap180(190) == pytest.approx(-170)
    assert wrap180(-190) == pytest.approx(170)


# ---------------------------------------------------------------- ArUco
@pytest.mark.parametrize("heading", [0, 37, 90, 145, -120])
def test_aruco_heading(heading):
    from flagzero.tools.make_test_video import car_sprite, paste_rotated, W, H
    frame = np.full((H, W, 3), 235, np.uint8)
    paste_rotated(frame, car_sprite(17, (180, 105, 255)), 400, 300, heading)
    ds = ArucoCarDetector().detect(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    assert len(ds) == 1 and ds[0].car == 17
    assert ds[0].x == pytest.approx(400, abs=1.5) and ds[0].y == pytest.approx(300, abs=1.5)
    assert abs(wrap180(ds[0].heading_deg - heading)) < 3


# ---------------------------------------------------------------- prediction logic
def test_normal_lap_on_a_curve_raises_nothing():
    cal = curve_cal()
    s = Sim(cal)
    for k in range(150):                                    # 5 s at ~50 km/h (paper scale)
        m = 1385 + k * 0.45
        s.step([det(17, *on_track(cal, m))])
    assert all(not hs for _, hs in s.log), [h for _, hs in s.log for h in hs][:3]


def test_parked_car_is_not_a_stopped_vehicle_until_it_has_moved():
    cal = straight_cal()
    s = Sim(cal)
    for _ in range(120):                                    # parked 4 s
        s.step([det(17, *on_track(cal, 1400))])
    assert s.first("STOPPED_VEHICLE")[0] is None
    for k in range(30):                                     # drive 1 s
        s.step([det(17, *on_track(cal, 1400 + k * 0.5))])
    t_stop = s.t
    for _ in range(120):
        s.step([det(17, *on_track(cal, 1415))])
    t, h = s.first("STOPPED_VEHICLE", 17)
    assert t is not None and 1.8 <= t - t_stop <= 2.6
    assert h["track_m"] == pytest.approx(1415, abs=0.5) and not h["predicted"]


def test_hand_pauses_the_stop_timer():
    cal = straight_cal()
    s = Sim(cal)
    for k in range(30):
        s.step([det(17, *on_track(cal, 1400 + k * 0.5))])
    t_stop = s.t
    for k in range(150):                                    # 5 s stopped, hand for 2 s of it
        paused = 30 <= k < 90
        s.step([det(17, *on_track(cal, 1415))], paused=paused)
    t, _ = s.first("STOPPED_VEHICLE", 17)
    assert t - t_stop >= 3.8                                # ~2 s later than without the hand


def test_spin_is_predicted_before_the_car_stops():
    cal = straight_cal()
    s = Sim(cal)
    for k in range(45):                                     # drive
        s.step([det(17, *on_track(cal, 1390 + k * 0.6))])
    t_spin = s.t
    for k in range(30):                                     # 1 s: 540 deg while sliding 4 m
        u = k / 30
        s.step([det(17, *on_track(cal, 1417 + 4 * u, spin=540 * u))])
    for _ in range(120):                                    # stopped backwards
        s.step([det(17, *on_track(cal, 1421, spin=540))])
    t_pred, h = s.first("SPIN_RISK", 17)
    t_obs, h_obs = s.first("STOPPED_VEHICLE", 17)
    assert t_pred is not None and h["predicted"]
    assert t_pred - t_spin < 0.4                            # within a few frames
    assert t_obs - t_pred > 2.0                             # the prediction is the head start
    assert h_obs["detail"]["backwards"] is True


def test_predicted_off_track_before_it_leaves():
    cal = curve_cal()
    s = Sim(cal)
    t_cross = None
    for k in range(60):
        lat = max(0.0, (k - 20) * 2.2)                      # drifts wide at 66 px/s
        if t_cross is None and lat > cal.corridor_px:
            t_cross = s.t
        s.step([det(17, *on_track(cal, 1400 + k * 0.5, lateral_px=lat))])
    t, h = s.first("OFF_TRACK", 17)
    assert t is not None and h["predicted"] and t < t_cross


def test_closing_on_a_stopped_car_and_multi_stop():
    cal = straight_cal()
    s = Sim(cal)
    for k in range(30):                                     # 17 arrives and stops at 1440
        s.step([det(17, *on_track(cal, 1425 + k * 0.5)), det(8, *on_track(cal, 1382))])
    for k in range(60):
        s.step([det(17, *on_track(cal, 1440)), det(8, *on_track(cal, 1382))])
    for k in range(30):                                     # 8 charges in at ~110 km/h
        s.step([det(17, *on_track(cal, 1440)), det(8, *on_track(cal, 1400 + k))])
    for k in range(120):
        s.step([det(17, *on_track(cal, 1440)), det(8, *on_track(cal, 1429))])
    t, h = s.first("CLOSING", 8)
    assert t is not None and h["detail"]["ahead"] == 17 and h["detail"]["ttc_s"] < 1.0
    t_m, h_m = s.first("MULTI_STOP")
    assert t_m is not None and sorted(h_m["detail"]["cars"]) == [8, 17]


def test_multi_stop_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(C, "MULTI_STOP_ENABLED", False)
    cal = straight_cal()
    s = Sim(cal)
    for k in range(30):
        s.step([det(17, *on_track(cal, 1400 + k * 0.5)), det(8, *on_track(cal, 1382 + k * 0.5))])
    for _ in range(120):
        s.step([det(17, *on_track(cal, 1415)), det(8, *on_track(cal, 1397))])
    assert s.first("STOPPED_VEHICLE", 8)[0] and s.first("MULTI_STOP")[0] is None


# ---------------------------------------------------------------- debris + hand
def paper(cal):
    f = np.full((600, 1200, 3), 232, np.uint8)
    cv2.polylines(f, [np.array(cal._p, np.int32).reshape(-1, 1, 2)], False, (40, 40, 40), 4)
    return f


def test_debris_after_two_seconds_and_hand_pauses_it():
    cal = straight_cal()
    sw = SceneWatcher(cal)
    base = paper(cal)
    x, y = cal.track_m_to_pixel(1423)
    t, raised = 0.0, None
    hand_frames = 0
    for k in range(int(7 * FPS)):
        f = base.copy()
        if t >= 1.0:
            cv2.circle(f, (int(x), int(y) + 8), 18, (170, 170, 175), -1)
        if 1.5 <= t < 2.5:                                  # hand covers the frame corner
            cv2.rectangle(f, (650, 0), (1200, 450), (140, 170, 225), -1)
            cv2.rectangle(f, (650 + (k % 7) * 3, 0), (700, 60), (120, 150, 210), -1)
        occ, deb = sw.update(f, t)
        hand_frames += occ
        if deb and raised is None:
            raised = (t, deb[0])
        t += 1 / FPS
    assert hand_frames > 20
    assert raised is not None
    t_r, h = raised
    assert h["kind"] == "DEBRIS" and h["track_m"] == pytest.approx(1423, abs=2)
    # 2 s of persistence + ~1 s paused while the hand was in frame
    assert 2.8 <= t_r - 1.0 <= 4.0


def test_moving_car_masks_are_not_debris():
    cal = straight_cal()
    sw = SceneWatcher(cal)
    base = paper(cal)
    t = 0.0
    for k in range(int(5 * FPS)):
        f = base.copy()
        x = 200 + k * 4
        d = det(17, x, 300, 0)
        cv2.fillConvexPoly(f, d.poly.astype(np.int32), (60, 60, 60))
        occ, deb = sw.update(f, t, [d.poly], [(d.x, d.y)])
        assert not deb and not occ or k < 5
        t += 1 / FPS


# ---------------------------------------------------------------- full clip
@pytest.mark.skipif(not os.environ.get("FZ_SLOW"), reason="set FZ_SLOW=1 (~60 s)")
def test_synthetic_clip_end_to_end(tmp_path):
    from flagzero.tools.make_test_video import build
    from flagzero.vision.pipeline import VisionPipeline
    v, c = build(tmp_path)
    pipe = VisionPipeline(Calibration.load(c))
    cap = cv2.VideoCapture(str(v))
    first, k = {}, 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        for h in pipe.process(f, k / 30)["hazards"]:
            first.setdefault((h["kind"], h.get("car")), k / 30)
        k += 1
    assert 5.5 <= first[("SPIN_RISK", 17)] < 6.2
    assert first[("SPIN_RISK", 17)] + 2 < first[("STOPPED_VEHICLE", 17)]
    assert 6.8 <= first[("CLOSING", 8)] < 8.4
    assert ("MULTI_STOP", 8) in first or ("MULTI_STOP", 17) in first
    assert 13.0 <= first[("DEBRIS", None)] <= 14.5
