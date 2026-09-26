"""
/vision stub -- dummy detector
===============================
Purpose: prove out "read a frame -> emit an Incident" without needing the
real Continuity Camera + OpenCV contour-tracking pipeline working yet.

What it does:
  - Loads vision/sample_frame.jpg (a synthetic stand-in for an overhead
    frame of the paper track -- see generate_sample_frame.py).
  - Runs a deliberately trivial "detection": looks for the reddest blob in
    the frame and treats its presence as a dummy "spin" event.
  - Builds and prints an Incident JSON (source="camera") using
    shared/schemas.py, so /core and /dashboard can be developed against the
    exact shape real detections will eventually have.

Run from the repo root:
    python -m vision.stub_detector

Wiring this into /core: the fake_event_generator's WebSocket server logs
anything a client sends it, so the quickest way to test end-to-end is to
open a websocket client here and send
    {"type": "incident", "data": <the dict below>}
Swap this print-only stub for that once B is ready -- see the commented-out
block at the bottom of main().
"""
import os

import cv2
import numpy as np

from shared import schemas, track_config

SAMPLE_IMAGE = os.path.join(os.path.dirname(__file__), "sample_frame.jpg")


def detect_dummy_event(image_path: str):
    """Deliberately naive: find the largest red-ish blob and call it a car
    in trouble. Replace with real centre-line-deviation / stillness /
    rollover-orientation detection."""
    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(
            f"Could not read {image_path} -- run "
            f"'python -m vision.generate_sample_frame' first if it's missing."
        )

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    red_mask = cv2.inRange(hsv, (0, 120, 70), (10, 255, 255))
    contours, _ = cv2.findContours(red_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        return None

    largest = max(contours, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(largest)
    area = cv2.contourArea(largest)
    confidence = float(np.clip(area / (img.shape[0] * img.shape[1]) * 20, 0.5, 0.99))
    return {"bbox": [int(x), int(y), int(w), int(h)], "confidence": round(confidence, 2)}


def main():
    detection = detect_dummy_event(SAMPLE_IMAGE)

    if detection is None:
        print("[vision] no dummy detection found in sample frame")
        return

    zone = "T4"
    incident = schemas.new_incident(
        event="spin",
        source="camera",
        car_id=4,
        location=zone,
        track_position_m=track_config.zone_position_m(zone),
        confidence=detection["confidence"],
        extra={"bbox": detection["bbox"]},
    )
    schemas.validate_incident(incident)
    print(f"[vision] dummy detection -> incident: {incident}")

    # --- to actually send this to /core once fake_event_generator is running: ---
    # import asyncio, json, websockets
    # async def send():
    #     async with websockets.connect("ws://localhost:8765") as ws:
    #         await ws.send(json.dumps({"type": "incident", "data": incident}))
    # asyncio.run(send())


if __name__ == "__main__":
    main()
