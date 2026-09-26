"""
flagzero/vision/vision.py

Overhead track-camera pipeline. Per frame, for each configured car colour:
HSV mask -> morphology -> largest blob -> centroid -> project onto the
calibrated polyline -> track_m, with a smoothed px/s speed estimate. Also
runs hazard detection (stopped vehicle, debris, multi-stop) and a hand
check that pauses every timer while the camera view is blocked. Sends
telemetry to the race-control server over a websocket at 10 Hz, and never
crashes the frame loop even if the server is down or restarts mid-run.

Usage
-----
    # Live camera (index from camera_test.py), with preview
    python -m flagzero.vision.vision --index 1

    # Headless for the demo (no preview windows)
    python -m flagzero.vision.vision --index 1 --no-preview

    # Replay a recording through the same pipeline
    python -m flagzero.vision.vision --source recordings/incident_01.mp4

Requires vision/vision_calib.json (see calibrate.py) to already exist.

Preview keys
------------
    q   quit
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import platform
import sys
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np
import websockets

from flagzero.vision.calibrate import load_calibration, pixel_to_track_m, track_m_to_pixel

DEFAULT_WS_URL = "ws://localhost:8000/ws/vision"

# ---- General pipeline ----
SEND_HZ = 10.0
RECONNECT_DELAY_S = 2.0
MIN_BLOB_AREA_PX = 150          # smallest contour area we trust as a real car blob
MORPH_KERNEL_SIZE = 7
SPEED_EMA_ALPHA = 0.3           # smoothing for the telemetry speed_px_s field (wall-clock based)

# ---- 1. Hand check (background subtraction / occlusion) ----
BG_HISTORY_FRAMES = 500
BG_VAR_THRESHOLD = 25
BG_LEARNING_RATE = 0.003        # slow: a static debris item (or a held-still hand) shouldn't
                                 # get absorbed into the background for at least several seconds
HAND_FOREGROUND_AREA_PX = 15000  # a foreground blob at least this big = something (a hand)
                                  # is covering most of the shot -> occluded, timers pause

# ---- 2. Stopped-vehicle detection ----
STATIONARY_WINDOW_S = 1.0          # look-back window for the "barely moved" check
STATIONARY_MOVE_THRESHOLD_PX = 3.0  # centroid must move less than this over the window
STOPPED_VEHICLE_MIN_S = 2.0         # paused-clock stationary duration before raising the hazard
STOPPED_CONF_BASE = 0.5
STOPPED_CONF_TIME_WEIGHT = 0.35
STOPPED_CONF_TIME_SCALE_S = 5.0     # confidence saturates ~5s after the threshold is crossed
STOPPED_CONF_QUALITY_WEIGHT = 0.15  # weight on blob "quality" (contour fill ratio)

# ---- 3. Debris detection ----
DEBRIS_MIN_AREA_PX = 200
DEBRIS_MAX_AREA_PX = 3000           # keep well under HAND_FOREGROUND_AREA_PX
DEBRIS_MIN_DURATION_S = 2.0
DEBRIS_MATCH_DIST_PX = 30.0          # frame-to-frame candidate matching radius
DEBRIS_LOST_GRACE_S = 1.0            # forget a candidate if unseen this long
DEBRIS_CONF_BASE = 0.5
DEBRIS_CONF_TIME_WEIGHT = 0.35
DEBRIS_CONF_TIME_SCALE_S = 5.0
DEBRIS_CONF_QUALITY_WEIGHT = 0.15    # weight on closeness to the expected debris size

# ---- 4. Multi-stop (first thing to cut under time pressure) ----
MULTI_STOP_ENABLED = True
MULTI_STOP_DISTANCE_M = 30.0
MULTI_STOP_TIME_WINDOW_S = 5.0


def _backend():
    return cv2.CAP_AVFOUNDATION if platform.system() == "Darwin" else cv2.CAP_ANY


# ----------------------------------------------------------------------
# Websocket connection: auto-reconnecting, never raises out of the frame loop
# ----------------------------------------------------------------------

class ConnectionState:
    def __init__(self):
        self.ws = None
        self.connected = False
        self.last_error: str | None = None


async def maintain_connection(state: ConnectionState, url: str) -> None:
    """Keeps trying to (re)connect forever. Runs as its own asyncio task."""
    while True:
        try:
            async with websockets.connect(url) as ws:
                state.ws = ws
                state.connected = True
                state.last_error = None
                print(f"[vision] connected to {url}")
                try:
                    await ws.wait_closed()
                except Exception:
                    pass
        except Exception as e:
            state.last_error = str(e)
        state.ws = None
        state.connected = False
        print(f"[vision] disconnected ({state.last_error}); "
              f"retrying in {RECONNECT_DELAY_S:.0f}s...")
        await asyncio.sleep(RECONNECT_DELAY_S)


async def maybe_send(state: ConnectionState, message: dict) -> bool:
    """Best-effort send. Returns False (never raises) if not connected or send fails."""
    if state.ws is None or not state.connected:
        return False
    try:
        await state.ws.send(json.dumps(message))
        return True
    except Exception as e:
        state.connected = False
        state.last_error = str(e)
        return False


# ----------------------------------------------------------------------
# Sim clock: wall-clock time that pauses entirely while occluded
# ----------------------------------------------------------------------

class SimClock:
    """A clock that only advances while the frame is not occluded.

    Every hazard/stationary timer in this module measures elapsed time
    against this clock instead of wall time, so a hand in front of the
    lens pauses everything rather than either resetting it or letting it
    keep ticking through the occlusion.
    """

    def __init__(self):
        self.t = 0.0

    def tick(self, dt: float, occluded: bool) -> float:
        if not occluded and dt > 0:
            self.t += dt
        return self.t


# ----------------------------------------------------------------------
# Per-car state
# ----------------------------------------------------------------------

def _new_car_track() -> dict:
    return {
        "prev_xy": None,
        "prev_t": None,
        "speed_smoothed": 0.0,
        "history": deque(),        # (sim_t, x, y) samples for the stationary-window check
        "stationary_since": None,  # sim_t, or None
        "last_track_m": None,
        "last_extent": 0.0,        # contour_area / bbox_area, a rough blob-quality signal
    }


# ----------------------------------------------------------------------
# Hazard evaluation (2, 3, 4)
# ----------------------------------------------------------------------

def _stopped_vehicle_hazards(car_tracks: dict, sim_t: float) -> list:
    out = []
    for car_id, track in car_tracks.items():
        since = track.get("stationary_since")
        if since is None or track.get("last_track_m") is None:
            continue
        duration = sim_t - since
        if duration < STOPPED_VEHICLE_MIN_S:
            continue

        conf_time = min(1.0, (duration - STOPPED_VEHICLE_MIN_S) / STOPPED_CONF_TIME_SCALE_S)
        quality = track.get("last_extent", 0.0)
        conf = (STOPPED_CONF_BASE
                + STOPPED_CONF_TIME_WEIGHT * conf_time
                + STOPPED_CONF_QUALITY_WEIGHT * quality)

        out.append({
            "kind": "STOPPED_VEHICLE",
            "car": car_id,
            "track_m": round(track["last_track_m"], 1),
            "conf": round(max(0.0, min(1.0, conf)), 2),
        })
    return out


def _is_car_color(h: int, s: int, v: int, calib: dict) -> bool:
    for hsv_cfg in calib["car_colors_hsv"].values():
        if (hsv_cfg["h_min"] <= h <= hsv_cfg["h_max"]
                and hsv_cfg["s_min"] <= s <= hsv_cfg["s_max"]
                and hsv_cfg["v_min"] <= v <= hsv_cfg["v_max"]):
            return True
    return False


def _debris_hazards(fg_mask: np.ndarray, hsv_frame: np.ndarray, calib: dict,
                     debris_tracks: dict, sim_t: float) -> list:
    contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    for c in contours:
        area = cv2.contourArea(c)
        if area < DEBRIS_MIN_AREA_PX or area > DEBRIS_MAX_AREA_PX:
            continue

        m = cv2.moments(c)
        if m["m00"] == 0:
            continue
        cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]

        h, s, v = hsv_frame[int(cy), int(cx)]
        if _is_car_color(int(h), int(s), int(v), calib):
            continue  # it's a car, not debris

        track_m, dist_px = pixel_to_track_m(cx, cy, calib=calib)
        if dist_px > calib["corridor_half_width_px"]:
            continue  # off the track corridor entirely

        match_id = None
        best_dist = DEBRIS_MATCH_DIST_PX
        for cid, cand in debris_tracks.items():
            d = math.hypot(cx - cand["xy"][0], cy - cand["xy"][1])
            if d < best_dist:
                best_dist = d
                match_id = cid

        if match_id is None:
            match_id = (max(debris_tracks.keys()) + 1) if debris_tracks else 1
            debris_tracks[match_id] = {
                "xy": (cx, cy), "since": sim_t, "last_seen": sim_t,
                "track_m": track_m, "area": area,
            }
        else:
            cand = debris_tracks[match_id]
            cand["xy"] = (cx, cy)
            cand["last_seen"] = sim_t
            cand["track_m"] = track_m
            cand["area"] = area

    for cid in list(debris_tracks.keys()):
        if sim_t - debris_tracks[cid]["last_seen"] > DEBRIS_LOST_GRACE_S:
            del debris_tracks[cid]

    out = []
    mid_area = (DEBRIS_MIN_AREA_PX + DEBRIS_MAX_AREA_PX) / 2.0
    half_range = (DEBRIS_MAX_AREA_PX - DEBRIS_MIN_AREA_PX) / 2.0
    for cand in debris_tracks.values():
        duration = sim_t - cand["since"]
        if duration < DEBRIS_MIN_DURATION_S:
            continue
        quality = max(0.0, 1.0 - abs(cand["area"] - mid_area) / half_range) if half_range else 1.0
        conf_time = min(1.0, (duration - DEBRIS_MIN_DURATION_S) / DEBRIS_CONF_TIME_SCALE_S)
        conf = (DEBRIS_CONF_BASE
                + DEBRIS_CONF_TIME_WEIGHT * conf_time
                + DEBRIS_CONF_QUALITY_WEIGHT * quality)
        out.append({
            "kind": "DEBRIS",
            "track_m": round(cand["track_m"], 1),
            "conf": round(max(0.0, min(1.0, conf)), 2),
        })
    return out


def _multi_stop_hazard(car_tracks: dict, sim_t: float) -> dict | None:
    if not MULTI_STOP_ENABLED:
        return None

    stopped = []
    for car_id, track in car_tracks.items():
        since = track.get("stationary_since")
        if since is None or track.get("last_track_m") is None:
            continue
        duration = sim_t - since
        if duration >= STOPPED_VEHICLE_MIN_S:
            stopped.append((car_id, track["last_track_m"], since, duration))

    for i in range(len(stopped)):
        for j in range(i + 1, len(stopped)):
            car_a, m_a, since_a, dur_a = stopped[i]
            car_b, m_b, since_b, dur_b = stopped[j]
            if (abs(m_a - m_b) <= MULTI_STOP_DISTANCE_M
                    and abs(since_a - since_b) <= MULTI_STOP_TIME_WINDOW_S):
                conf = min(1.0, 0.6 + 0.08 * min(dur_a, dur_b))
                return {
                    "kind": "MULTI_STOP",
                    "cars": [car_a, car_b],
                    "track_m": round((m_a + m_b) / 2.0, 1),
                    "conf": round(conf, 2),
                }
    return None


# ----------------------------------------------------------------------
# Per-frame processing
# ----------------------------------------------------------------------

def process_frame(frame: np.ndarray, calib: dict, car_tracks: dict, debris_tracks: dict,
                   bg_subtractor, clock: SimClock, wall_now: float, wall_dt: float):
    """
    Returns (car_results, hazards, boxes_for_preview, occluded, sim_t).

    car_results/hazards/boxes_for_preview are None when occluded is True --
    the caller should keep using its last known values (every timer here is
    paused, so nothing changed).
    """
    # --- 1. hand check: runs every frame regardless of anything else ---
    fg_mask = bg_subtractor.apply(frame, learningRate=BG_LEARNING_RATE)
    fg_mask = cv2.morphologyEx(
        fg_mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    )
    fg_contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    largest_fg_area = max((cv2.contourArea(c) for c in fg_contours), default=0.0)
    occluded = largest_fg_area > HAND_FOREGROUND_AREA_PX

    sim_t = clock.tick(wall_dt, occluded)

    if occluded:
        return None, None, None, True, sim_t

    # --- per-car colour detection ---
    hsv_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (MORPH_KERNEL_SIZE, MORPH_KERNEL_SIZE))

    car_results = []
    boxes_for_preview = []

    for car_id_str, hsv_cfg in calib["car_colors_hsv"].items():
        car_id = int(car_id_str)
        track = car_tracks.setdefault(car_id, _new_car_track())

        lower = np.array([hsv_cfg["h_min"], hsv_cfg["s_min"], hsv_cfg["v_min"]])
        upper = np.array([hsv_cfg["h_max"], hsv_cfg["s_max"], hsv_cfg["v_max"]])
        mask = cv2.inRange(hsv_frame, lower, upper)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            track["prev_xy"] = None
            continue

        largest = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(largest)
        if area < MIN_BLOB_AREA_PX:
            track["prev_xy"] = None
            continue

        x, y, w, h = cv2.boundingRect(largest)
        m = cv2.moments(largest)
        if m["m00"] == 0:
            track["prev_xy"] = None
            continue
        cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]
        extent = area / float(w * h) if w * h else 0.0

        track_m, _dist_px = pixel_to_track_m(cx, cy, calib=calib)
        track["last_track_m"] = track_m
        track["last_extent"] = max(0.0, min(1.0, extent))

        # instantaneous smoothed speed -- telemetry field only, wall-clock based
        if track["prev_xy"] is not None and track["prev_t"] is not None:
            dt = wall_now - track["prev_t"]
            if dt > 0:
                dx, dy = cx - track["prev_xy"][0], cy - track["prev_xy"][1]
                inst_speed = math.hypot(dx, dy) / dt
                track["speed_smoothed"] = (
                    SPEED_EMA_ALPHA * inst_speed + (1 - SPEED_EMA_ALPHA) * track["speed_smoothed"]
                )
        track["prev_xy"] = (cx, cy)
        track["prev_t"] = wall_now

        # 2. windowed "barely moved" check drives the (pausable) stationary timer
        track["history"].append((sim_t, cx, cy))
        while track["history"] and sim_t - track["history"][0][0] > STATIONARY_WINDOW_S + 1.0:
            track["history"].popleft()

        ref = None
        for sample in track["history"]:
            if sim_t - sample[0] >= STATIONARY_WINDOW_S:
                ref = sample
            else:
                break

        if ref is not None:
            disp = math.hypot(cx - ref[1], cy - ref[2])
            if disp < STATIONARY_MOVE_THRESHOLD_PX:
                if track["stationary_since"] is None:
                    track["stationary_since"] = ref[0]
            else:
                track["stationary_since"] = None

        stationary_s = (sim_t - track["stationary_since"]) if track["stationary_since"] is not None else 0.0

        car_results.append({
            "car": car_id,
            "track_m": round(track_m, 1),
            "speed_px_s": round(track["speed_smoothed"], 1),
            "stationary_s": round(stationary_s, 1),
        })
        boxes_for_preview.append({
            "car_id": car_id, "bbox": (x, y, w, h), "track_m": track_m,
            "color_name": hsv_cfg.get("name", ""),
        })

    # --- hazards: each computed independently, dropped the frame they stop being true ---
    hazards = []
    hazards.extend(_stopped_vehicle_hazards(car_tracks, sim_t))
    hazards.extend(_debris_hazards(fg_mask, hsv_frame, calib, debris_tracks, sim_t))
    multi = _multi_stop_hazard(car_tracks, sim_t)
    if multi is not None:
        hazards.append(multi)

    return car_results, hazards, boxes_for_preview, False, sim_t


def build_message(camera_id: int, car_results: list, hazards: list, occluded: bool) -> dict:
    return {
        "type": "vision",
        "camera": camera_id,
        "cars": car_results,
        "hazards": hazards,
        "occluded": occluded,
    }


# ----------------------------------------------------------------------
# Preview
# ----------------------------------------------------------------------

def draw_preview(frame, calib, boxes_for_preview, hazards, fps, state: ConnectionState,
                  last_msg, occluded):
    canvas = frame.copy()
    pts = np.array(calib["centerline_points_px"], dtype=np.int32)
    cv2.polylines(canvas, [pts], False, (0, 255, 255), 2)

    for b in boxes_for_preview:
        x, y, w, h = b["bbox"]
        cv2.rectangle(canvas, (x, y), (x + w, y + h), (0, 255, 0), 2)
        label = f"#{b['car_id']} {b['track_m']:.0f}m"
        cv2.putText(canvas, label, (x, max(0, y - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2, cv2.LINE_AA)

    for hz in hazards:
        px, py = track_m_to_pixel(hz["track_m"], calib=calib)
        px, py = int(px), int(py)
        if hz["kind"] == "STOPPED_VEHICLE":
            color = (0, 165, 255)
            label = f"STOPPED #{hz['car']} {hz['conf']:.2f}"
            cv2.circle(canvas, (px, py), 22, color, 3)
        elif hz["kind"] == "DEBRIS":
            color = (0, 0, 255)
            label = f"DEBRIS {hz['conf']:.2f}"
            cv2.drawMarker(canvas, (px, py), color, markerType=cv2.MARKER_TILTED_CROSS,
                            markerSize=24, thickness=3)
        elif hz["kind"] == "MULTI_STOP":
            color = (255, 0, 255)
            label = f"MULTI-STOP {hz['cars']} {hz['conf']:.2f}"
            cv2.circle(canvas, (px, py), 34, color, 3)
        else:
            color = (255, 255, 255)
            label = hz["kind"]
        cv2.putText(canvas, label, (max(0, px - 20), max(15, py - 30)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2, cv2.LINE_AA)

    cv2.putText(canvas, f"FPS: {fps:.1f}", (10, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)

    status_text = "CONNECTED" if state.connected else "DISCONNECTED"
    status_color = (0, 255, 0) if state.connected else (0, 0, 255)
    cv2.putText(canvas, status_text, (10, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, status_color, 2, cv2.LINE_AA)

    if occluded:
        cv2.putText(canvas, "OCCLUDED (hand?) -- timers paused", (10, 80),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2, cv2.LINE_AA)

    if last_msg is not None:
        last_text = json.dumps(last_msg)
        if len(last_text) > 90:
            last_text = last_text[:87] + "..."
        cv2.putText(canvas, last_text, (10, canvas.shape[0] - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1, cv2.LINE_AA)

    cv2.imshow("flagzero vision (q=quit)", canvas)


# ----------------------------------------------------------------------
# Capture source
# ----------------------------------------------------------------------

def open_capture(args) -> cv2.VideoCapture:
    if args.source:
        cap = cv2.VideoCapture(args.source)
        if not cap.isOpened():
            print(f"Could not open video source {args.source}")
            sys.exit(1)
        return cap
    if args.index is not None:
        cap = cv2.VideoCapture(args.index, _backend())
        if not cap.isOpened():
            print(f"Could not open camera index {args.index}. "
                  f"Run camera_test.py to confirm the right index.")
            sys.exit(1)
        return cap
    print("Pass --index N (live camera) or --source file.mp4")
    sys.exit(1)


# ----------------------------------------------------------------------
# Main loop
# ----------------------------------------------------------------------

async def async_main(args) -> None:
    calib = load_calibration()
    cap = open_capture(args)

    state = ConnectionState()
    car_tracks: dict = {}
    debris_tracks: dict = {}
    bg_subtractor = cv2.createBackgroundSubtractorMOG2(
        history=BG_HISTORY_FRAMES, varThreshold=BG_VAR_THRESHOLD, detectShadows=False
    )
    clock = SimClock()
    last_msg_holder = {"msg": None}

    # Last known good (non-occluded) results, carried forward while occluded.
    last_car_results: list = []
    last_hazards: list = []
    last_boxes: list = []

    ws_task = asyncio.create_task(maintain_connection(state, args.ws_url))

    send_interval = 1.0 / SEND_HZ
    last_send = 0.0
    prev_t = time.time()
    fps = 0.0

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                if args.source:
                    print("[vision] end of video source.")
                    break
                print("[vision] frame grab failed, retrying...")
                await asyncio.sleep(0.1)
                continue

            now = time.time()
            wall_dt = now - prev_t
            prev_t = now
            if wall_dt > 0:
                inst_fps = 1.0 / wall_dt
                fps = fps * 0.9 + inst_fps * 0.1 if fps else inst_fps

            # Never let a processing hiccup kill the loop -- log and keep going.
            try:
                car_results, hazards, boxes_for_preview, occluded, _sim_t = process_frame(
                    frame, calib, car_tracks, debris_tracks, bg_subtractor, clock, now, wall_dt
                )

                if occluded:
                    car_results, hazards, boxes_for_preview = last_car_results, last_hazards, last_boxes
                else:
                    last_car_results, last_hazards, last_boxes = car_results, hazards, boxes_for_preview

                if now - last_send >= send_interval:
                    msg = build_message(args.camera_id, car_results, hazards, occluded)
                    sent = await maybe_send(state, msg)
                    if sent:
                        last_msg_holder["msg"] = msg
                    last_send = now

                if not args.no_preview:
                    draw_preview(frame, calib, boxes_for_preview, hazards, fps, state,
                                 last_msg_holder["msg"], occluded)
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord("q"):
                        break
            except Exception as e:
                print(f"[vision] frame processing error (continuing): {e}")

            await asyncio.sleep(0)  # yield so the websocket task can run
    finally:
        ws_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ws_task
        cap.release()
        if not args.no_preview:
            cv2.destroyAllWindows()


def main() -> None:
    parser = argparse.ArgumentParser(description="FlagZero overhead vision pipeline.")
    parser.add_argument("--index", type=int, default=None,
                         help="Camera index (from camera_test.py).")
    parser.add_argument("--source", type=str, default=None,
                         help="Video file to run the pipeline on instead of a live camera.")
    parser.add_argument("--camera-id", type=int, default=1,
                         help="Value reported in the 'camera' field of vision messages.")
    parser.add_argument("--ws-url", type=str, default=DEFAULT_WS_URL,
                         help="Race-control server websocket URL.")
    parser.add_argument("--no-preview", action="store_true",
                         help="Run headless, no preview window (for the demo).")
    args = parser.parse_args()

    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("\n[vision] stopped by user.")


if __name__ == "__main__":
    main()
