"""Print the car markers (A4, 300 dpi). Tape each one onto its sticky note / car.

    python flagzero/vision/markers.py              # cars 17, 8, 21 -> vision/markers/
    python flagzero/vision/markers.py --cars 17 8 --size-cm 3

The arrow on each card = the car's NOSE. Place cars with the arrow pointing in
the driving direction; the heading-error and spin logic depend on it.
Print at 100 % ("actual size"), not "fit to page". Matte paper beats glossy.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import cv2
import numpy as np

from flagzero.vision import vision_config as C

DPI = 300
A4 = (2480, 3508)            # px at 300 dpi
BODY = {17: (180, 105, 255), 8: (230, 150, 40), 21: (60, 190, 60)}   # BGR


def cm(v):
    return int(round(v / 2.54 * DPI))


def card(car, size_cm):
    d = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, C.ARUCO_DICT))
    gen = getattr(cv2.aruco, "generateImageMarker", None) or cv2.aruco.drawMarker
    marker_id = next((k for k, v in C.MARKER_TO_CAR.items() if v == car), car)
    ms = cm(size_cm)
    quiet = ms // 6                       # white border the detector needs
    body = max(cm(0.35), ms // 10)
    side = ms + 2 * quiet + 2 * body
    img = np.full((side + cm(1.6), side, 3), 255, np.uint8)
    col = BODY.get(car, (160, 160, 160))
    cv2.rectangle(img, (0, 0), (side - 1, side - 1), col, -1)
    cv2.rectangle(img, (body, body), (side - body - 1, side - body - 1), (255, 255, 255), -1)
    m = gen(d, marker_id, ms)
    o = body + quiet
    img[o:o + ms, o:o + ms] = cv2.cvtColor(m, cv2.COLOR_GRAY2BGR)
    # nose arrow + label below the card (cut this strip off, or keep it as a tab)
    y = side + cm(0.25)
    cx = side // 2
    cv2.arrowedLine(img, (cx, y + cm(1.2)), (cx, y), (0, 0, 0), max(3, cm(0.12)), tipLength=0.35)
    cv2.putText(img, f"#{car}", (cm(0.2), y + cm(1.0)), cv2.FONT_HERSHEY_SIMPLEX,
                DPI / 130, (0, 0, 0), max(2, DPI // 70), cv2.LINE_AA)
    cv2.putText(img, "nose", (cx + cm(0.25), y + cm(0.5)), cv2.FONT_HERSHEY_SIMPLEX,
                DPI / 260, (0, 0, 0), max(1, DPI // 150), cv2.LINE_AA)
    cv2.rectangle(img, (0, 0), (img.shape[1] - 1, img.shape[0] - 1), (200, 200, 200), 2)
    return img


def sheet(cars, size_cm, copies):
    page = np.full((A4[1], A4[0], 3), 255, np.uint8)
    cards = [card(c, size_cm) for c in cars for _ in range(copies)]
    margin, gap = cm(1.5), cm(1.0)
    x, y, row_h = margin, margin + cm(1.2), 0
    cv2.putText(page, f"FlagZero car markers - {C.ARUCO_DICT} - print at 100% scale - "
                f"marker {size_cm:g} cm - arrow = car nose", (margin, margin + cm(0.4)), cv2.FONT_HERSHEY_SIMPLEX,
                DPI / 210, (0, 0, 0), 2, cv2.LINE_AA)
    for c in cards:
        h, w = c.shape[:2]
        if x + w > A4[0] - margin:
            x, y, row_h = margin, y + row_h + gap, 0
        if y + h > A4[1] - margin:
            break
        page[y:y + h, x:x + w] = c
        x += w + gap
        row_h = max(row_h, h)
    return page


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cars", type=int, nargs="+", default=[17, 8, 21])
    ap.add_argument("--size-cm", type=float, default=3.0,
                    help="black marker square; 2.5-4 cm works for a phone ~50 cm above A3")
    ap.add_argument("--copies", type=int, default=2)
    ap.add_argument("--out", type=Path, default=C.MARKERS_DIR)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    page = sheet(a.cars, a.size_cm, a.copies)
    png = a.out / "car_markers_A4.png"
    cv2.imwrite(str(png), page)
    print(f"wrote {png}")
    try:
        from PIL import Image
        pdf = a.out / "car_markers_A4.pdf"
        Image.fromarray(cv2.cvtColor(page, cv2.COLOR_BGR2RGB)).save(pdf, resolution=DPI)
        print(f"wrote {pdf}  (print this one at 100 %)")
    except Exception as e:
        print(f"(no PDF: {type(e).__name__} - pip install pillow, or print the PNG at 300 dpi / 100 %)")


if __name__ == "__main__":
    main()
