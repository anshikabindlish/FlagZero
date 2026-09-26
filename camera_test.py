"""
flagzero/vision/camera_test.py

Find and test the overhead camera (iPhone #1 via Continuity Camera on macOS).

Usage
-----
    # Scan indices 0-3, print resolution + open/closed, show a thumbnail
    # window per camera that opens. Press any key in a thumbnail window
    # (or 'q' in the terminal-focused window) to advance.
    python -m flagzero.vision.camera_test

    # Open one index full-size, show live FPS, 's' saves a still
    python -m flagzero.vision.camera_test --index 1

    # Fall back to a recorded clip instead of a live camera
    python -m flagzero.vision.camera_test --source path/to/file.mp4

Keys (in --index / --source mode)
----------------------------------
    s   save a still to vision/frames/still.png (timestamped, won't clobber)
    q   quit

Notes
-----
- Uses cv2.CAP_AVFOUNDATION on macOS (this is the backend Continuity Camera
  shows up under). On other platforms it falls back to the default backend
  (CAP_ANY) so a teammate can still run the scan on Windows/Linux with a
  built-in or USB webcam.
- All paths use pathlib, no OS-specific commands.
"""

from __future__ import annotations

import argparse
import platform
import sys
import time
from pathlib import Path

import cv2

# Directory this file lives in: flagzero/vision/
VISION_DIR = Path(__file__).resolve().parent
FRAMES_DIR = VISION_DIR / "frames"

SCAN_RANGE = range(0, 4)  # indices 0..3
THUMB_MAX_W = 320  # px, thumbnail width cap for the scan window


def _backend():
    """Pick the right OpenCV backend flag for this OS."""
    if platform.system() == "Darwin":
        return cv2.CAP_AVFOUNDATION
    return cv2.CAP_ANY


def _print_fix_suggestions(any_opened: bool) -> None:
    print("\n" + "=" * 60)
    if any_opened:
        print("Some cameras opened, but if the one you expected didn't:")
    else:
        print("No cameras opened at all. Things to check:")
    print("=" * 60)
    print(
        dedent_fixes(
            """
            1. macOS camera permission for your terminal / editor:
               System Settings -> Privacy & Security -> Camera
               -> enable it for "Terminal" (or "iTerm", "Code", whichever
               app you launched this script from). If you just enabled it,
               fully quit and reopen that app (a re-run in the same
               process often doesn't pick it up).

            2. Continuity Camera requires:
               - iPhone #1 signed into the SAME Apple ID as this Mac
               - Wi-Fi + Bluetooth on for both devices
               - iPhone unlocked (or at least woken/nearby -- test both)
               - iPhone held/mounted landscape and stationary
               - No other app currently holding the camera (close Photo
                 Booth, FaceTime, Zoom, another Python process, etc.)

            3. If the iPhone still doesn't show as a cv2 index:
               - Toggle it off/on: on the iPhone, swipe down from the
                 top-right -> the video-source control near the camera
                 icon (or just lock/unlock the phone) to force macOS to
                 re-advertise the Continuity Camera device.
               - Try unplugging/replugging if you're using a cable as a
                 fallback link instead of wireless Continuity Camera.

            4. Fallbacks if you need to keep working without the iPhone:
               --source some_clip.mp4   replay a recorded clip instead
               OnePlus 15R + DroidCam: install DroidCam on the phone and
                 the DroidCam client/virtual-cam driver on a laptop, it
                 will show up as a normal webcam index -- scan again.
               Built-in laptop webcam pointed at the paper track, held
                 upright behind/above it, works as a last-resort stand-in
                 for the overhead shot (perspective will be worse; expect
                 to redo any homography/calibration).
            """
        )
    )
    print("=" * 60 + "\n")


def dedent_fixes(text: str) -> str:
    lines = [ln[12:] if ln.startswith(" " * 12) else ln for ln in text.splitlines()]
    return "\n".join(lines).strip("\n")


def scan_indices() -> None:
    """Try indices 0..3, print resolution/opened, show a thumbnail each."""
    print(f"Scanning camera indices {list(SCAN_RANGE)} "
          f"(backend={'AVFOUNDATION' if platform.system() == 'Darwin' else 'default'}) ...\n")

    any_opened = False

    for idx in SCAN_RANGE:
        cap = cv2.VideoCapture(idx, _backend())
        opened = cap.isOpened()

        if not opened:
            print(f"  index {idx}: NOT opened")
            cap.release()
            continue

        # Give the device a moment and grab a frame to confirm it's real,
        # not just a phantom index that "opens" but returns nothing.
        ok, frame = cap.read()
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        if not ok or frame is None:
            print(f"  index {idx}: opened but no frame returned (resolution reported {w}x{h})")
            cap.release()
            continue

        any_opened = True
        print(f"  index {idx}: OPENED  resolution={w}x{h}")

        # Show a small thumbnail so you can visually tell cameras apart.
        thumb = frame
        if w > THUMB_MAX_W:
            scale = THUMB_MAX_W / w
            thumb = cv2.resize(frame, (THUMB_MAX_W, int(h * scale)))

        win_name = f"index {idx} ({w}x{h}) -- press any key to continue"
        cv2.imshow(win_name, thumb)
        print(f"    -> showing thumbnail window, click it and press any key to continue")
        cv2.waitKey(0)
        cv2.destroyWindow(win_name)

        cap.release()

    cv2.destroyAllWindows()
    print()
    if any_opened:
        print("Scan done. Re-run with --index N on whichever index looked like the")
        print("overhead track view to preview it full-size.")
    _print_fix_suggestions(any_opened)


def preview(index: int | None, source: str | None) -> None:
    """Open one camera (or file) full-size, overlay FPS, 's' saves a still."""
    if source is not None:
        cap = cv2.VideoCapture(source)
        label = f"source={source}"
    else:
        cap = cv2.VideoCapture(index, _backend())
        label = f"index={index}"

    if not cap.isOpened():
        print(f"Could not open camera/source ({label}).")
        _print_fix_suggestions(any_opened=False)
        sys.exit(1)

    FRAMES_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Opened {label}. Window focused: 's' = save still, 'q' = quit.")

    prev_t = time.time()
    fps = 0.0
    win_name = f"camera_test ({label})"

    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            print("Stream ended or frame grab failed.")
            break

        now = time.time()
        dt = now - prev_t
        prev_t = now
        if dt > 0:
            # Simple smoothing so the readout doesn't jitter every frame.
            inst_fps = 1.0 / dt
            fps = fps * 0.9 + inst_fps * 0.1 if fps else inst_fps

        cv2.putText(
            frame,
            f"FPS: {fps:.1f}",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )

        cv2.imshow(win_name, frame)
        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            break
        elif key == ord("s"):
            out_path = FRAMES_DIR / "still.png"
            if out_path.exists():
                # Don't clobber a previous grab -- timestamp it instead.
                stamp = time.strftime("%Y%m%d-%H%M%S")
                out_path = FRAMES_DIR / f"still_{stamp}.png"
            cv2.imwrite(str(out_path), frame)
            print(f"Saved still -> {out_path}")

    cap.release()
    cv2.destroyAllWindows()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find and preview the overhead camera for FlagZero vision."
    )
    parser.add_argument(
        "--index", type=int, default=None,
        help="Open this camera index full-size instead of scanning 0-3.",
    )
    parser.add_argument(
        "--source", type=str, default=None,
        help="Path to a video file to preview instead of a live camera.",
    )
    args = parser.parse_args()

    if args.index is None and args.source is None:
        scan_indices()
    else:
        preview(index=args.index, source=args.source)


if __name__ == "__main__":
    main()
