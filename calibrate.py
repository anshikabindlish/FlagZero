"""
flagzero/vision/calibrate.py

Interactive calibration for the overhead track camera.

Builds vision/vision_calib.json containing:
  - the clicked centre-line polyline (pixel coords, driving order)
  - a linear pixel-length -> track-metres mapping over TRACK_M_RANGE
  - the track corridor half-width (px)
  - per-car HSV colour ranges (pink=17, blue=8 by default)

and exposes pixel_to_track_m(x, y) for the rest of the vision pipeline.

Usage
-----
    # Full interactive wizard: capture a still, click ~10 centreline points,
    # set corridor width, tune HSV per car, save, reload, sanity-check.
    python -m flagzero.vision.calibrate --index 1

    # Same, but calibrate against an existing still instead of the camera
    python -m flagzero.vision.calibrate --still vision/frames/still.png

    # Skip straight to loading the saved calibration and re-running the
    # sanity check / showing the overlay (no clicking, no camera needed)
    python -m flagzero.vision.calibrate --reload

    # Skip HSV tuning (keep whatever is already in the JSON, or the
    # hard-coded defaults if there's nothing saved yet)
    python -m flagzero.vision.calibrate --index 1 --skip-hsv

Keys
----
Centreline clicking window:
    left-click   add a point (driving order)
    u            undo last point
    d            done (need >= 4 points)
    q            abort entire script

Corridor width window:
    trackbar     drag "half-width px"
    d            confirm and continue
    q            skip, keep default

HSV tuning window (per car):
    trackbars    H min/max, S min/max, V min/max
    left-click   print the HSV value under the cursor (for quick re-tuning)
    n            next car / done
    q            abort remaining tuning, keep defaults for this car
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

import cv2
import numpy as np

VISION_DIR = Path(__file__).resolve().parent
FRAMES_DIR = VISION_DIR / "frames"
CALIB_PATH = VISION_DIR / "vision_calib.json"

TRACK_M_RANGE = (1380.0, 1480.0)   # paper track spans this lap-distance range
T4_TRACK_M = 1423.0                # blind crest, for the visual sanity marker

MIN_CENTERLINE_POINTS = 4
DEFAULT_CORRIDOR_HALF_WIDTH_PX = 40

# Car number -> default HSV range (OpenCV hue is 0-179)
CAR_COLOR_DEFAULTS = {
    17: {"name": "pink", "h_min": 140, "h_max": 179, "s_min": 60, "s_max": 255, "v_min": 60, "v_max": 255},
    8: {"name": "blue", "h_min": 90, "h_max": 130, "s_min": 60, "s_max": 255, "v_min": 60, "v_max": 255},
}


def _backend():
    return cv2.CAP_AVFOUNDATION if platform.system() == "Darwin" else cv2.CAP_ANY


# ----------------------------------------------------------------------
# Step 0: get a still to calibrate against
# ----------------------------------------------------------------------

def capture_still(index: int) -> np.ndarray:
    """Open the camera, show a live preview, SPACE grabs the still, q aborts."""
    cap = cv2.VideoCapture(index, _backend())
    if not cap.isOpened():
        print(f"Could not open camera index {index}. Run camera_test.py first "
              f"to confirm the right index.")
        sys.exit(1)

    print("Live preview -- press SPACE to grab the calibration still, q to abort.")
    frame = None
    while True:
        ok, live = cap.read()
        if not ok:
            print("Frame grab failed.")
            sys.exit(1)
        cv2.imshow("capture (SPACE=grab, q=quit)", live)
        key = cv2.waitKey(1) & 0xFF
        if key == ord(" "):
            frame = live.copy()
            break
        if key == ord("q"):
            cap.release()
            cv2.destroyAllWindows()
            sys.exit(0)

    cap.release()
    cv2.destroyAllWindows()

    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    still_path = FRAMES_DIR / "calib_still.png"
    cv2.imwrite(str(still_path), frame)
    print(f"Saved calibration still -> {still_path}")
    return frame


def load_still(path: Path) -> np.ndarray:
    img = cv2.imread(str(path))
    if img is None:
        print(f"Could not read still image at {path}")
        sys.exit(1)
    return img


# ----------------------------------------------------------------------
# Step 1: click the centre line
# ----------------------------------------------------------------------

def click_centerline(image: np.ndarray) -> list[list[float]]:
    points: list[list[float]] = []
    win = "click centreline: left-click=add, u=undo, d=done, q=abort"

    def redraw():
        canvas = image.copy()
        for i, (x, y) in enumerate(points):
            cv2.circle(canvas, (int(x), int(y)), 4, (0, 0, 255), -1)
            if i > 0:
                cv2.line(canvas, (int(points[i - 1][0]), int(points[i - 1][1])),
                          (int(x), int(y)), (0, 255, 255), 2)
        cv2.imshow(win, canvas)

    def on_mouse(event, x, y, flags, userdata):
        if event == cv2.EVENT_LBUTTONDOWN:
            points.append([float(x), float(y)])
            redraw()

    cv2.namedWindow(win)
    cv2.setMouseCallback(win, on_mouse)
    redraw()

    print(f"Click ~10 points along the centre line, in driving order "
          f"(need at least {MIN_CENTERLINE_POINTS}).")

    while True:
        key = cv2.waitKey(20) & 0xFF
        if key == ord("u") and points:
            points.pop()
            redraw()
        elif key == ord("d"):
            if len(points) < MIN_CENTERLINE_POINTS:
                print(f"Need at least {MIN_CENTERLINE_POINTS} points, "
                      f"have {len(points)}.")
                continue
            break
        elif key == ord("q"):
            cv2.destroyWindow(win)
            print("Aborted.")
            sys.exit(0)

    cv2.destroyWindow(win)
    print(f"Centreline: {len(points)} points captured.")
    return points


def _cumulative_lengths(points: list[list[float]]) -> tuple[list[float], float]:
    pts = np.array(points, dtype=float)
    seg_vectors = np.diff(pts, axis=0)
    seg_lengths = np.linalg.norm(seg_vectors, axis=1)
    cum = np.concatenate(([0.0], np.cumsum(seg_lengths)))
    return cum.tolist(), float(cum[-1])


# ----------------------------------------------------------------------
# Step 2: corridor half-width
# ----------------------------------------------------------------------

def _segment_normals(points: list[list[float]]) -> list[np.ndarray]:
    """One outward normal per vertex, averaged from adjacent segment directions."""
    pts = np.array(points, dtype=float)
    n = len(pts)
    dirs = []
    for i in range(n - 1):
        v = pts[i + 1] - pts[i]
        norm = np.linalg.norm(v)
        dirs.append(v / norm if norm else np.array([1.0, 0.0]))

    normals = []
    for i in range(n):
        if i == 0:
            d = dirs[0]
        elif i == n - 1:
            d = dirs[-1]
        else:
            d = (dirs[i - 1] + dirs[i])
            norm = np.linalg.norm(d)
            d = d / norm if norm else dirs[i - 1]
        # rotate direction 90 degrees to get the perpendicular
        normals.append(np.array([-d[1], d[0]]))
    return normals


def tune_corridor(image: np.ndarray, points: list[list[float]]) -> int:
    win = "corridor width: drag trackbar, d=confirm, q=skip (use default)"
    cv2.namedWindow(win)
    cv2.createTrackbar("half-width px", win, DEFAULT_CORRIDOR_HALF_WIDTH_PX, 200, lambda v: None)

    normals = _segment_normals(points)
    pts = np.array(points, dtype=float)

    half_width = DEFAULT_CORRIDOR_HALF_WIDTH_PX
    print("Set the corridor half-width (px either side of the centre line), "
          "'d' to confirm, 'q' to skip and keep the default.")

    while True:
        half_width = cv2.getTrackbarPos("half-width px", win)
        canvas = image.copy()
        left = pts + normals * half_width
        right = pts - normals * half_width
        cv2.polylines(canvas, [pts.astype(np.int32)], False, (0, 255, 255), 2)
        cv2.polylines(canvas, [left.astype(np.int32)], False, (255, 0, 0), 1)
        cv2.polylines(canvas, [right.astype(np.int32)], False, (255, 0, 0), 1)
        cv2.imshow(win, canvas)

        key = cv2.waitKey(20) & 0xFF
        if key == ord("d"):
            break
        if key == ord("q"):
            print(f"Skipped, using default half-width = {DEFAULT_CORRIDOR_HALF_WIDTH_PX}px")
            half_width = DEFAULT_CORRIDOR_HALF_WIDTH_PX
            break

    cv2.destroyWindow(win)
    print(f"Corridor half-width = {half_width}px")
    return int(half_width)


# ----------------------------------------------------------------------
# Step 3: per-car HSV tuning
# ----------------------------------------------------------------------

def tune_car_colors(cars: dict[int, dict], get_frame) -> dict[int, dict]:
    result = {}
    for car_id, defaults in cars.items():
        result[car_id] = dict(defaults)
        win_orig = f"car {car_id} ({defaults['name']}) -- click=print HSV, n=next, q=abort tuning"
        win_mask = f"car {car_id} ({defaults['name']}) mask"

        cv2.namedWindow(win_orig)
        cv2.namedWindow(win_mask)
        cv2.createTrackbar("H min", win_orig, defaults["h_min"], 179, lambda v: None)
        cv2.createTrackbar("H max", win_orig, defaults["h_max"], 179, lambda v: None)
        cv2.createTrackbar("S min", win_orig, defaults["s_min"], 255, lambda v: None)
        cv2.createTrackbar("S max", win_orig, defaults["s_max"], 255, lambda v: None)
        cv2.createTrackbar("V min", win_orig, defaults["v_min"], 255, lambda v: None)
        cv2.createTrackbar("V max", win_orig, defaults["v_max"], 255, lambda v: None)

        last_frame_holder = {"frame": None}

        def on_mouse(event, x, y, flags, userdata, holder=last_frame_holder):
            if event == cv2.EVENT_LBUTTONDOWN and holder["frame"] is not None:
                hsv = cv2.cvtColor(holder["frame"], cv2.COLOR_BGR2HSV)
                h, s, v = hsv[y, x]
                print(f"  HSV at ({x},{y}): H={h} S={s} V={v}")

        cv2.setMouseCallback(win_orig, on_mouse)

        print(f"\nTuning car {car_id} ({defaults['name']}). "
              f"Click on the car in the frame to print its HSV. "
              f"'n' when the mask looks clean, 'q' to keep defaults and stop tuning.")

        aborted = False
        while True:
            frame = get_frame()
            last_frame_holder["frame"] = frame
            hsv_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

            h_min = cv2.getTrackbarPos("H min", win_orig)
            h_max = cv2.getTrackbarPos("H max", win_orig)
            s_min = cv2.getTrackbarPos("S min", win_orig)
            s_max = cv2.getTrackbarPos("S max", win_orig)
            v_min = cv2.getTrackbarPos("V min", win_orig)
            v_max = cv2.getTrackbarPos("V max", win_orig)

            lower = np.array([h_min, s_min, v_min])
            upper = np.array([h_max, s_max, v_max])
            mask = cv2.inRange(hsv_frame, lower, upper)

            cv2.imshow(win_orig, frame)
            cv2.imshow(win_mask, mask)

            key = cv2.waitKey(20) & 0xFF
            if key == ord("n"):
                result[car_id].update({
                    "h_min": h_min, "h_max": h_max,
                    "s_min": s_min, "s_max": s_max,
                    "v_min": v_min, "v_max": v_max,
                })
                break
            if key == ord("q"):
                print(f"  Kept defaults for car {car_id}.")
                aborted = True
                break

        cv2.destroyWindow(win_orig)
        cv2.destroyWindow(win_mask)
        if aborted:
            continue

    return result


# ----------------------------------------------------------------------
# Save / load
# ----------------------------------------------------------------------

def save_calibration(data: dict) -> None:
    CALIB_PATH.write_text(json.dumps(data, indent=2))
    print(f"Saved calibration -> {CALIB_PATH}")


def load_calibration() -> dict:
    if not CALIB_PATH.exists():
        raise FileNotFoundError(
            f"No calibration at {CALIB_PATH}. Run without --reload first."
        )
    return json.loads(CALIB_PATH.read_text())


_CALIB_CACHE: dict | None = None


def _get_cached_calib() -> dict:
    global _CALIB_CACHE
    if _CALIB_CACHE is None:
        _CALIB_CACHE = load_calibration()
    return _CALIB_CACHE


# ----------------------------------------------------------------------
# Core projection: pixel -> track metres
# ----------------------------------------------------------------------

def _project_point_to_polyline(x: float, y: float, pts: list[list[float]],
                                cum_lengths: list[float]) -> tuple[float, float]:
    """Return (cumulative_length_px_at_projection, distance_to_polyline_px)."""
    p = np.array([x, y], dtype=float)
    best_dist = float("inf")
    best_len = 0.0
    for i in range(len(pts) - 1):
        a = np.array(pts[i], dtype=float)
        b = np.array(pts[i + 1], dtype=float)
        ab = b - a
        seg_len_sq = float(np.dot(ab, ab))
        if seg_len_sq == 0.0:
            t = 0.0
        else:
            t = float(np.dot(p - a, ab) / seg_len_sq)
            t = max(0.0, min(1.0, t))
        proj = a + t * ab
        dist = float(np.linalg.norm(p - proj))
        if dist < best_dist:
            best_dist = dist
            seg_len = cum_lengths[i + 1] - cum_lengths[i]
            best_len = cum_lengths[i] + t * seg_len
    return best_len, best_dist


def pixel_to_track_m(x: float, y: float, calib: dict | None = None) -> tuple[float, float]:
    """
    Project (x, y) onto the nearest centreline segment and return
    (track_m, dist_from_line_px).
    """
    calib = calib if calib is not None else _get_cached_calib()
    pts = calib["centerline_points_px"]
    cum = calib["seg_cum_lengths_px"]
    total = calib["total_length_px"]
    m0, m1 = calib["track_m_range"]

    proj_len, dist_px = _project_point_to_polyline(x, y, pts, cum)
    frac = 0.0 if total == 0 else proj_len / total
    track_m = m0 + frac * (m1 - m0)
    return track_m, dist_px


def track_m_to_pixel(m: float, calib: dict | None = None) -> tuple[float, float]:
    """Inverse of pixel_to_track_m, for drawing sanity markers (e.g. T4)."""
    calib = calib if calib is not None else _get_cached_calib()
    pts = calib["centerline_points_px"]
    cum = calib["seg_cum_lengths_px"]
    total = calib["total_length_px"]
    m0, m1 = calib["track_m_range"]

    frac = 0.0 if m1 == m0 else (m - m0) / (m1 - m0)
    target_len = frac * total

    for i in range(len(cum) - 1):
        if cum[i] <= target_len <= cum[i + 1]:
            seg_len = cum[i + 1] - cum[i]
            t = 0.0 if seg_len == 0 else (target_len - cum[i]) / seg_len
            x = pts[i][0] + t * (pts[i + 1][0] - pts[i][0])
            y = pts[i][1] + t * (pts[i + 1][1] - pts[i][1])
            return x, y
    return tuple(pts[-1])


# ----------------------------------------------------------------------
# Sanity check + overlay
# ----------------------------------------------------------------------

def sanity_check(calib: dict) -> None:
    pts = calib["centerline_points_px"]
    cum = calib["seg_cum_lengths_px"]
    total = calib["total_length_px"]
    m0, m1 = calib["track_m_range"]

    print("\nSanity check: re-projecting clicked points onto the saved polyline")
    print("-" * 60)
    worst_err = 0.0
    for i, (x, y) in enumerate(pts):
        track_m, dist_px = pixel_to_track_m(x, y, calib=calib)
        expected_frac = cum[i] / total if total else 0.0
        expected_m = m0 + expected_frac * (m1 - m0)
        err_m = abs(track_m - expected_m)
        worst_err = max(worst_err, err_m)
        print(f"  point {i:2d}  clicked=({x:6.1f},{y:6.1f})  "
              f"track_m={track_m:7.2f}  expected={expected_m:7.2f}  "
              f"error={err_m:6.3f} m  line_dist={dist_px:5.2f}px")
    print("-" * 60)
    print(f"Worst re-projection error: {worst_err:.3f} m "
          f"({'looks fine' if worst_err < 0.5 else 'check your clicks / vertex spacing'})")

    tx, ty = track_m_to_pixel(T4_TRACK_M, calib=calib)
    print(f"\nT4 ({T4_TRACK_M} m) maps to pixel ({tx:.0f}, {ty:.0f}) "
          f"-- check this lands near the crest you drew.")


def draw_overlay(image: np.ndarray, calib: dict) -> np.ndarray:
    canvas = image.copy()
    pts = np.array(calib["centerline_points_px"], dtype=float)
    half_w = calib["corridor_half_width_px"]
    normals = _segment_normals(calib["centerline_points_px"])

    left = pts + np.array(normals) * half_w
    right = pts - np.array(normals) * half_w
    cv2.polylines(canvas, [pts.astype(np.int32)], False, (0, 255, 255), 2)
    cv2.polylines(canvas, [left.astype(np.int32)], False, (255, 0, 0), 1)
    cv2.polylines(canvas, [right.astype(np.int32)], False, (255, 0, 0), 1)

    tx, ty = track_m_to_pixel(T4_TRACK_M, calib=calib)
    cv2.circle(canvas, (int(tx), int(ty)), 8, (0, 0, 255), 2)
    cv2.putText(canvas, f"T4 {T4_TRACK_M:.0f}m", (int(tx) + 10, int(ty)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2, cv2.LINE_AA)

    return canvas


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate the overhead track camera.")
    parser.add_argument("--index", type=int, default=None, help="Camera index (from camera_test.py).")
    parser.add_argument("--still", type=str, default=None, help="Use an existing still instead of the camera.")
    parser.add_argument("--reload", action="store_true", help="Skip clicking; load saved calibration and re-check.")
    parser.add_argument("--skip-hsv", action="store_true", help="Don't run HSV tuning; keep existing/default values.")
    args = parser.parse_args()

    if args.reload:
        calib = load_calibration()
        print(f"Loaded calibration from {CALIB_PATH}")
        sanity_check(calib)

        still_path = FRAMES_DIR / "calib_still.png"
        if still_path.exists():
            image = load_still(still_path)
            overlay = draw_overlay(image, calib)
            cv2.imshow("calibration overlay (any key to close)", overlay)
            cv2.waitKey(0)
            cv2.destroyAllWindows()
        return

    # --- get a still to work against ---
    if args.still:
        image = load_still(Path(args.still))
    elif args.index is not None:
        image = capture_still(args.index)
    else:
        print("Pass --index N (live camera) or --still path.png, or use --reload.")
        sys.exit(1)

    # --- step 1: centreline ---
    points = click_centerline(image)
    cum_lengths, total_length = _cumulative_lengths(points)

    # --- step 2: corridor width ---
    half_width_px = tune_corridor(image, points)

    # --- step 3: HSV per car ---
    if args.skip_hsv:
        car_colors = {cid: dict(v) for cid, v in CAR_COLOR_DEFAULTS.items()}
        print("Skipping HSV tuning, using existing/default ranges.")
    else:
        if args.index is not None:
            cap = cv2.VideoCapture(args.index, _backend())
            if not cap.isOpened():
                print("Camera unavailable for HSV tuning, falling back to the still.")
                get_frame = lambda: image
            else:
                get_frame = lambda: cap.read()[1]
        else:
            get_frame = lambda: image
        car_colors = tune_car_colors(CAR_COLOR_DEFAULTS, get_frame)
        if args.index is not None:
            try:
                cap.release()
            except NameError:
                pass

    # --- step 4: save, then reload to prove the round trip works ---
    calib = {
        "image_size_px": [image.shape[1], image.shape[0]],
        "centerline_points_px": points,
        "seg_cum_lengths_px": cum_lengths,
        "total_length_px": total_length,
        "track_m_range": list(TRACK_M_RANGE),
        "corridor_half_width_px": half_width_px,
        "car_colors_hsv": {str(k): v for k, v in car_colors.items()},
        "calibrated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    save_calibration(calib)

    global _CALIB_CACHE
    _CALIB_CACHE = None  # force a fresh read from disk
    reloaded = load_calibration()
    print("Reloaded calibration from disk OK.")

    # --- steps 5 & 6: projection + sanity check ---
    sanity_check(reloaded)

    overlay = draw_overlay(image, reloaded)
    cv2.imshow("calibration overlay (any key to close)", overlay)
    cv2.waitKey(0)
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
