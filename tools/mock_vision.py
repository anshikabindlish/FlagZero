"""Fake camera: send /ws/vision messages from the keyboard (no camera needed).

    python flagzero/tools/mock_vision.py                       # localhost:8000
    python flagzero/tools/mock_vision.py --url wss://xyz.trycloudflare.com/ws/vision
    python flagzero/tools/mock_vision.py --scene 2             # scripted Scene 2

Type a letter + Enter:
    s  car 17 stops at T4 (1423 m)      d  debris at 1423 m
    m  multi-stop (17 + 8 near T4)      p  car 17 spinning (predicted SPIN_RISK)
    o  car 17 heading off track         l  car 8 closing fast on car 17
    h  toggle hand-in-frame (occluded)  c  clear everything        q  quit
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flagzero.vision import vision_config as C
from flagzero.vision.sender import VisionSender


class MockWorld:
    def __init__(self):
        self.lock = threading.Lock()
        self.clear()

    def clear(self):
        self.t0 = time.monotonic()
        self.stop17 = self.debris = self.multi = self.spin = self.off = self.closing = None
        self.occluded = False

    def msg(self):
        now = time.monotonic()
        with self.lock:
            cars = [self._car(17, 1405.0 + ((now - self.t0) * 6) % 15, 22.0),
                    self._car(8, 1390.0, 20.0)]
            hz = []
            if self.spin:
                cars[0].update(yaw_rate_dps=410.0, heading_err_deg=95.0, speed_kmh=18.0)
                hz.append(self._hz("SPIN_RISK", 17, 1420.0, 0.86, True))
            if self.off:
                cars[0].update(lateral=0.9)
                hz.append(self._hz("OFF_TRACK", 17, 1418.0, 0.58, True))
            if self.closing:
                hz.append(self._hz("CLOSING", 8, 1423.0, 0.72, True,
                                   {"ahead": 17, "ttc_s": 0.7, "gap_m": 18.0}))
            if self.stop17:
                st = now - self.stop17 + 2.0
                cars[0].update(track_m=1423.0, speed_kmh=0.0, speed_px_s=0.0,
                               stationary_s=round(st, 1))
                hz.append(self._hz("STOPPED_VEHICLE", 17, 1423.0,
                                   min(0.97, 0.85 + 0.1 * (st - 2)), False))
            if self.multi:
                st = now - self.multi + 2.0
                cars[0].update(track_m=1423.0, speed_kmh=0.0, stationary_s=round(st, 1))
                cars[1].update(track_m=1413.0, speed_kmh=0.0, stationary_s=round(st, 1))
                hz.append(self._hz("MULTI_STOP", 17, 1418.0, 0.9, False, {"cars": [17, 8]}))
            if self.debris:
                hz.append(self._hz("DEBRIS", None, 1423.0, 0.93, False))
            return {"type": "vision", "camera": C.CAMERA_ID, "t_ms": int(time.time() * 1000),
                    "cars": cars, "hazards": hz, "occluded": self.occluded}

    @staticmethod
    def _car(car, m, kmh):
        return {"car": car, "track_m": round(m, 1), "speed_px_s": round(kmh * 3, 1),
                "speed_kmh": kmh, "stationary_s": 0.0, "heading_err_deg": 0.0,
                "yaw_rate_dps": 0.0, "lateral": 0.1, "off_track": False, "src": "mock",
                "risk": 0.0, "visible": True}

    @staticmethod
    def _hz(kind, car, m, conf, predicted, detail=None):
        h = {"kind": kind, "track_m": m, "conf": round(conf, 2), "predicted": predicted,
             "detail": detail or {}}
        if car is not None:
            h["car"] = car
        return h

    def key(self, k):
        now = time.monotonic()
        with self.lock:
            if k == "s":
                self.stop17, self.spin = now, None
            elif k == "d":
                self.debris = now
            elif k == "m":
                self.multi = now
            elif k == "p":
                self.spin = now
            elif k == "o":
                self.off = None if self.off else now
            elif k == "l":
                self.closing = None if self.closing else now
            elif k == "h":
                self.occluded = not self.occluded
            elif k == "c":
                self.clear()


SCENE2 = [(0.0, "c"), (1.0, "p"), (2.5, "l"), (3.5, "s"), (6.0, "l")]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=C.SERVER_WS_URL)
    ap.add_argument("--scene", type=int, choices=[1, 2], help="play a scripted scene then idle")
    ap.add_argument("--print", action="store_true", help="print every message sent")
    a = ap.parse_args()

    world = MockWorld()
    sender = VisionSender(a.url)
    stop = threading.Event()

    def pump():
        last = 0.0
        while not stop.is_set():
            m = world.msg()
            sender.send(m)
            if a.print:
                print(m)
            if time.monotonic() - last > 2.0:
                last = time.monotonic()
                kinds = [h["kind"] for h in m["hazards"]] or ["-"]
                print(f"  [{sender.status()}] hazards: {' '.join(kinds)}"
                      + ("  (hand)" if m["occluded"] else ""), flush=True)
            time.sleep(1.0 / C.SEND_HZ)

    threading.Thread(target=pump, daemon=True).start()
    print(__doc__)
    if a.scene:
        script = [(0.0, "c"), (1.0, "d")] if a.scene == 1 else SCENE2
        t0 = time.monotonic()
        for at, k in script:
            time.sleep(max(0.0, at - (time.monotonic() - t0)))
            print(f"[scene {a.scene}] t={at:.1f}s key '{k}'")
            world.key(k)
    try:
        for line in sys.stdin:
            k = line.strip().lower()[:1]
            if k == "q":
                break
            if k:
                world.key(k)
    except KeyboardInterrupt:
        pass
    stop.set()
    sender.close()


if __name__ == "__main__":
    main()
