"""
Regenerates vision/sample_frame.jpg: a synthetic stand-in for an overhead
iPhone shot of the paper track, so stub_detector.py has something to read
before the Continuity Camera pipeline is wired up. Re-run any time you want
a fresh dummy frame, e.g. with the red dot moved.

    python -m vision.generate_sample_frame
"""
import os

import cv2
import numpy as np

OUT_PATH = os.path.join(os.path.dirname(__file__), "sample_frame.jpg")


def main():
    # "paper" background
    img = np.full((720, 1280, 3), 235, dtype=np.uint8)

    # taped-sheet seam, just for visual realism
    cv2.line(img, (640, 0), (640, 720), (200, 200, 200), 2)

    # centre line -- this is what the real pipeline will track deviation against
    cv2.line(img, (60, 360), (1220, 360), (30, 30, 30), 4)

    # a red blob standing in for "car off-line / spinning"
    cv2.circle(img, (820, 240), 26, (0, 0, 220), -1)

    cv2.imwrite(OUT_PATH, img)
    print(f"[vision] wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
