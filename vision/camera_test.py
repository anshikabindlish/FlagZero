"""Find which camera index is the iPhone (Continuity Camera) and check it works.

    python flagzero/vision/camera_test.py              # probe 0..3, thumbnails
    python flagzero/vision/camera_test.py --index 1    # open one full size, 's' = save still
    python flagzero/vision/camera_test.py --source clip.mp4
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import cv2
import numpy as np

from flagzero.vision import vision_config as C

FIXES = """
Nothing opened? Check, in this order:
  1. macOS camera permission for the app running Python (Terminal, iTerm, VS Code):
     System Settings > Privacy & Security > Camera -> enable, then QUIT and reopen it.
  2. iPhone #1 and MacBook #1 signed into the SAME Apple ID, Wi-Fi + Bluetooth on.
  3. iPhone locked, in LANDSCAPE, not moving, rear camera facing the paper
     (Continuity Camera only starts when the phone is locked and stationary).
  4. Close FaceTime / Photo Booth / Zoom - only one app can hold the camera.
Fallbacks:
  - OnePlus + DroidCam (phone app + desktop client) shows up as another index.
  - The MacBook webcam with the paper track taped upright on a wall/box.
  - --source file.mp4 to run everything on a recording.
"""


def backend():
    if sys.platform == "darwin":
        return cv2.CAP_AVFOUNDATION
    if sys.platform.startswith("win"):
        return cv2.CAP_DSHOW
    return cv2.CAP_ANY


def probe(max_index=4):
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, backend())
        ok = cap.isOpened()
        frame = None
        if ok:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
            for _ in range(10):                      # some cameras need warm-up frames
                ok2, frame = cap.read()
                if ok2 and frame is not None and frame.mean() > 2:
                    break
                time.sleep(0.05)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) if ok else 0
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) if ok else 0
        print(f"index {i}: {'OPEN ' if ok else 'closed'} {w}x{h}"
              + ("  (frames OK)" if frame is not None else ("  (no frames)" if ok else "")))
        if frame is not None:
            found.append((i, frame))
        cap.release()
    if not found:
        print(FIXES)
        return
    thumbs = []
    for i, f in found:
        t = cv2.resize(f, (400, int(400 * f.shape[0] / f.shape[1])))
        cv2.putText(t, f"index {i}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        thumbs.append(t)
    h = max(t.shape[0] for t in thumbs)
    row = np.hstack([cv2.copyMakeBorder(t, 0, h - t.shape[0], 0, 4, cv2.BORDER_CONSTANT) for t in thumbs])
    print("\nThe iPhone is usually the highest index / the 1920x1080 one. Any key closes.")
    try:
        cv2.imshow("camera indexes", row)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
    except cv2.error:
        C.FRAMES_DIR.mkdir(parents=True, exist_ok=True)
        p = C.FRAMES_DIR / "probe.png"
        cv2.imwrite(str(p), row)
        print(f"(no display) thumbnails saved to {p}")


def live(index=None, source=None):
    cap = cv2.VideoCapture(str(source)) if source else cv2.VideoCapture(index, backend())
    if not source:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    if not cap.isOpened():
        print(f"could not open {source or index}")
        print(FIXES)
        return
    fps, t_prev = 0.0, time.monotonic()
    print("s = save still to vision/frames/still.png, q = quit")
    while True:
        ok, f = cap.read()
        if not ok:
            if source:
                break
            continue
        now = time.monotonic()
        fps = 0.9 * fps + 0.1 / max(now - t_prev, 1e-6)
        t_prev = now
        show = f.copy()
        cv2.putText(show, f"{f.shape[1]}x{f.shape[0]}  {fps:.0f} fps", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 0), 2)
        cv2.imshow("camera", show if show.shape[1] <= 1400 else
                   cv2.resize(show, (1280, int(1280 * show.shape[0] / show.shape[1]))))
        k = cv2.waitKey(1) & 0xFF
        if k in (ord("q"), 27):
            break
        if k == ord("s"):
            C.FRAMES_DIR.mkdir(parents=True, exist_ok=True)
            p = C.FRAMES_DIR / "still.png"
            cv2.imwrite(str(p), f)
            print(f"saved {p}")
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", type=int)
    ap.add_argument("--source", type=Path)
    ap.add_argument("--max-index", type=int, default=4)
    a = ap.parse_args()
    if a.index is None and a.source is None:
        probe(a.max_index)
    else:
        live(a.index, a.source)
