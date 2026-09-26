"""
flagzero/vision/vision.py

Overhead track-camera pipeline. Per frame, for each configured car colour:
HSV mask -> morphology -> largest blob -> centroid -> project onto the
calibrated polyline -> track_m, with a smoothed px/s speed estimate. Sends
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
import platform
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import websockets

from flagzero.vision.calibrate import load_calibration, pixel_to_track_m

DEFAULT_WS_URL = "ws://localhost:8000/ws/vision"

SEND_HZ = 10.0
RECONNECT_DELAY_S = 2.0

MIN_BLOB_AREA_PX = 150
MORPH_KERNEL_SIZE = 7
SPEED_EMA_ALPHA = 0.3
STATIONARY_SPEED_THRESHOLD_PX_S = 5.0
# Heuristic only: a near-zero grayscale std usually means something is
# physically blocking the whole lens (hand, lens cap), not just a busy scene.
OCCLUSION_STD_THRESHOLD = 8.0


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
# Per-frame processing
# ----------------------------------------------------------------------

def _new_track() -> dict:
    return {"prev_xy": None, "prev_t": None, "speed_smoothed": 0.0, "stationary_since": None}


def process_frame(frame: np.ndarray, calib: dict, car_tracks: dict, now: float):
    """
    Returns (car_results, boxes_for_preview, occluded):
      car_results       -- list of {"car","track_m","speed_px_s","stationary_s"}
                            for cars actually detected this frame
      boxes_for_preview -- list of {"car_id","bbox","track_m","color_name"}
      occluded          -- bool, whole-camera occlusion heuristic
    """
    hsv_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    occluded = bool(gray.std() < OCCLUSION_STD_THRESHOLD)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (MORPH_KERNEL_SIZE, MORPH_KERNEL_SIZE))

    car_results = []
    boxes_for_preview = []

    for car_id_str, hsv_cfg in calib["car_colors_hsv"].items():
        car_id = int(car_id_str)
        track = car_tracks.setdefault(car_id, _new_track())

        lower = np.array([hsv_cfg["h_min"], hsv_cfg["s_min"], hsv_cfg["v_min"]])
        upper = np.array([hsv_cfg["h_max"], hsv_cfg["s_max"], hsv_cfg["v_max"]])
        mask = cv2.inRange(hsv_frame, lower, upper)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            track["prev_xy"] = None  # lost sight of it; don't extrapolate speed across the gap
            continue

        largest = max(contours, key=cv2.contourArea)
        if cv2.contourArea(largest) < MIN_BLOB_AREA_PX:
            track["prev_xy"] = None
            continue

        x, y, w, h = cv2.boundingRect(largest)
        m = cv2.moments(largest)
        if m["m00"] == 0:
            track["prev_xy"] = None
            continue
        cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]

        track_m, _dist_px = pixel_to_track_m(cx, cy, calib=calib)

        if track["prev_xy"] is not None and track["prev_t"] is not None:
            dt = now - track["prev_t"]
            if dt > 0:
                dx, dy = cx - track["prev_xy"][0], cy - track["prev_xy"][1]
                inst_speed = (dx * dx + dy * dy) ** 0.5 / dt
                track["speed_smoothed"] = (
                    SPEED_EMA_ALPHA * inst_speed + (1 - SPEED_EMA_ALPHA) * track["speed_smoothed"]
                )
        track["prev_xy"] = (cx, cy)
        track["prev_t"] = now

        if track["speed_smoothed"] < STATIONARY_SPEED_THRESHOLD_PX_S:
            if track["stationary_since"] is None:
                track["stationary_since"] = now
        else:
            track["stationary_since"] = None
        stationary_s = (now - track["stationary_since"]) if track["stationary_since"] else 0.0

        car_results.append({
            "car": car_id,
            "track_m": round(track_m, 1),
            "speed_px_s": round(track["speed_smoothed"], 1),
            "stationary_s": round(stationary_s, 1),
        })
        boxes_for_preview.append({
            "car_id": car_id,
            "bbox": (x, y, w, h),
            "track_m": track_m,
            "color_name": hsv_cfg.get("name", ""),
        })

    return car_results, boxes_for_preview, occluded


def build_message(camera_id: int, car_results: list, occluded: bool) -> dict:
    return {
        "type": "vision",
        "camera": camera_id,
        "cars": car_results,
        "hazards": [],
        "occluded": occluded,
    }


# ----------------------------------------------------------------------
# Preview
# ----------------------------------------------------------------------

def draw_preview(frame, calib, boxes_for_preview, fps, state: ConnectionState, last_msg):
    canvas = frame.copy()
    pts = np.array(calib["centerline_points_px"], dtype=np.int32)
    cv2.polylines(canvas, [pts], False, (0, 255, 255), 2)

    for b in boxes_for_preview:
        x, y, w, h = b["bbox"]
        cv2.rectangle(canvas, (x, y), (x + w, y + h), (0, 255, 0), 2)
        label = f"#{b['car_id']} {b['track_m']:.0f}m"
        cv2.putText(canvas, label, (x, max(0, y - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2, cv2.LINE_AA)

    cv2.putText(canvas, f"FPS: {fps:.1f}", (10, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)

    status_text = "CONNECTED" if state.connected else "DISCONNECTED"
    status_color = (0, 255, 0) if state.connected else (0, 0, 255)
    cv2.putText(canvas, status_text, (10, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, status_color, 2, cv2.LINE_AA)

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
    last_msg_holder = {"msg": None}

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
            dt = now - prev_t
            prev_t = now
            if dt > 0:
                inst_fps = 1.0 / dt
                fps = fps * 0.9 + inst_fps * 0.1 if fps else inst_fps

            # Never let a processing hiccup kill the loop -- log and keep going.
            try:
                car_results, boxes_for_preview, occluded = process_frame(
                    frame, calib, car_tracks, now
                )

                if now - last_send >= send_interval:
                    msg = build_message(args.camera_id, car_results, occluded)
                    sent = await maybe_send(state, msg)
                    if sent:
                        last_msg_holder["msg"] = msg
                    last_send = now

                if not args.no_preview:
                    draw_preview(frame, calib, boxes_for_preview, fps, state,
                                 last_msg_holder["msg"])
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
