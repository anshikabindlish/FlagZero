"""FlagZero vision - live camera (or file) -> /ws/vision at 10 Hz.

    python flagzero/vision/vision.py --index 1                 # Continuity Camera
    python flagzero/vision/vision.py --source clip.mp4         # recorded video
    python flagzero/vision/vision.py --index 1 --record        # + save video/JSONL
    python flagzero/vision/vision.py --index 1 --metrics debris   # 10 timed trials

Keys in the preview window:
    q / Esc  quit                r  reset (re-arm cars, retake debris reference)
    b        retake debris reference only
    space    pause (file source)  s  save still to vision/frames/
    p        metrics: object placed now      n  metrics: mark this trial a miss
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import cv2

from flagzero.vision import vision_config as C
from flagzero.vision.calib import Calibration
from flagzero.vision.pipeline import VisionPipeline
from flagzero.vision.sender import VisionSender


# ---------------------------------------------------------------------- source
def open_source(args):
    if args.source:
        cap = cv2.VideoCapture(str(args.source))
        if not cap.isOpened():
            sys.exit(f"could not open video {args.source}")
        return cap, True
    backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else (
        cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY)
    cap = cv2.VideoCapture(args.index, backend)
    if not cap.isOpened():
        sys.exit(f"camera {args.index} did not open - run vision/camera_test.py first "
                 "(macOS: System Settings > Privacy & Security > Camera for your terminal)")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    return cap, False


def fit_width(frame, w):
    if w and frame.shape[1] > w:
        h = int(round(frame.shape[0] * w / frame.shape[1]))
        return cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
    return frame


# ---------------------------------------------------------------------- metrics
class Metrics:
    KIND = {"stopped": "STOPPED_VEHICLE", "debris": "DEBRIS"}

    def __init__(self, mode, trials, timeout_s=10.0):
        self.mode, self.kind = mode, self.KIND[mode]
        self.trials, self.timeout = trials, timeout_s
        self.rows = []
        self.placed_t = None
        print(f"\n[metrics] {trials} trials of {self.kind}. Place the object, press 'p' "
              f"the moment it lands. Remove it and wait for the hazard to clear between trials.\n")

    def active_kinds(self, msg):
        return {h["kind"] for h in msg["hazards"]}

    def on_key(self, key, t, msg):
        if key == ord("p"):
            if self.kind in self.active_kinds(msg):
                print("[metrics] hazard still active - clear it first (remove object / press r)")
                return
            self.placed_t = t
            print(f"[metrics] trial {len(self.rows) + 1}: placed at t={t:.2f}s")
        elif key == ord("n") and self.placed_t is not None:
            self._finish(t, False)

    def on_frame(self, t, msg):
        if self.placed_t is None:
            return False
        if self.kind in self.active_kinds(msg):
            self._finish(t, True)
        elif t - self.placed_t > self.timeout:
            self._finish(t, False)
        return len(self.rows) >= self.trials

    def _finish(self, t, hit):
        dt = t - self.placed_t
        self.rows.append({"trial": len(self.rows) + 1, "kind": self.kind,
                          "detected": int(hit), "time_to_detect_s": round(dt, 3) if hit else ""})
        print(f"[metrics] trial {len(self.rows)}: {'DETECTED in %.2f s' % dt if hit else 'MISS'}")
        self.placed_t = None

    def report(self):
        if not self.rows:
            return
        C.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        path = C.RESULTS_DIR / "vision_metrics.csv"
        new = not path.exists()
        with path.open("a", newline="") as f:
            w = csv.DictWriter(f, ["timestamp", "trial", "kind", "detected", "time_to_detect_s"])
            if new:
                w.writeheader()
            stamp = datetime.now().isoformat(timespec="seconds")
            for r in self.rows:
                w.writerow({"timestamp": stamp, **r})
        times = [r["time_to_detect_s"] for r in self.rows if r["detected"]]
        rate = len(times) / len(self.rows)
        print(f"\n[metrics] {self.kind}: detection rate {rate:.0%} ({len(times)}/{len(self.rows)})")
        if times:
            print(f"[metrics] time-to-detect median {statistics.median(times):.2f} s, "
                  f"max {max(times):.2f} s")
        print(f"[metrics] appended to {path}\n")


# ---------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", type=int, default=0, help="camera index (see camera_test.py)")
    ap.add_argument("--source", type=Path, help="video file instead of a camera")
    ap.add_argument("--calib", type=Path, default=C.CALIB_PATH)
    ap.add_argument("--url", default=C.SERVER_WS_URL)
    ap.add_argument("--no-server", action="store_true", help="don't send, just run")
    ap.add_argument("--no-preview", action="store_true")
    ap.add_argument("--proc-width", type=int, default=1280, help="downscale wider frames")
    ap.add_argument("--no-aruco", action="store_true")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--no-debris", action="store_true")
    ap.add_argument("--record", action="store_true", help="save raw video + JSONL log")
    ap.add_argument("--jsonl", type=Path, help="also write every sent message here")
    ap.add_argument("--fast", action="store_true", help="file source: don't wait real time")
    ap.add_argument("--loop", action="store_true", help="file source: loop forever")
    ap.add_argument("--metrics", choices=["stopped", "debris"])
    ap.add_argument("--trials", type=int, default=10)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if not args.calib.exists():
        sys.exit(f"no calibration at {args.calib} - run vision/calibrate.py first")
    cal = Calibration.load(args.calib)
    pipe = VisionPipeline(cal, use_aruco=not args.no_aruco, use_color=not args.no_color,
                          debris=not args.no_debris)
    sender = VisionSender(args.url, enabled=not args.no_server)
    cap, is_file = open_source(args)
    fps_file = (cap.get(cv2.CAP_PROP_FPS) or 30.0) if is_file else None
    metrics = Metrics(args.metrics, args.trials) if args.metrics else None

    writer = jlog = None
    if args.record:
        C.RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        stem = datetime.now().strftime("%Y%m%d_%H%M%S")
        rec_video = C.RECORDINGS_DIR / f"{stem}.mp4"
        jlog = (C.RECORDINGS_DIR / f"{stem}.jsonl").open("w")
        print(f"recording -> {rec_video} (+ .jsonl)")
    if args.jsonl:
        args.jsonl.parent.mkdir(parents=True, exist_ok=True)
        jlog2 = args.jsonl.open("w")
    else:
        jlog2 = None

    preview = not args.no_preview
    win = "FlagZero vision"
    frame_i, t0_wall = 0, time.monotonic()
    last_send, last_print = -1.0, 0.0
    fps_ema, t_prev = 0.0, time.monotonic()
    paused = False
    frame = None
    try:
        while True:
            if not paused or frame is None:
                ok, raw = cap.read()
                if not ok:
                    if is_file and args.loop:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        pipe.reset()
                        frame_i, t0_wall = 0, time.monotonic()
                        continue
                    if is_file:
                        break
                    print("camera frame grab failed - retrying")
                    time.sleep(0.2)
                    continue
                frame = fit_width(raw, args.proc_width)
                if args.record and writer is None:
                    writer = cv2.VideoWriter(str(rec_video), cv2.VideoWriter_fourcc(*"mp4v"),
                                             30.0, (frame.shape[1], frame.shape[0]))
                if writer is not None:
                    writer.write(frame)
                t = frame_i / fps_file if is_file else time.monotonic() - t0_wall
                frame_i += 1
                msg = pipe.process(frame, t)

                # pace file playback to real time so timers mean something live
                if is_file and not args.fast:
                    lag = t - (time.monotonic() - t0_wall)
                    if lag > 0:
                        time.sleep(lag)

                if t - last_send >= 1.0 / C.SEND_HZ - 1e-6:
                    last_send = t
                    sender.send(msg)
                    line = json.dumps({"t": round(t, 3), "msg": msg}, separators=(",", ":"))
                    for f in (jlog, jlog2):
                        if f:
                            f.write(line + "\n")

                if metrics and metrics.on_frame(t, msg):
                    break

                now = time.monotonic()
                fps_ema = 0.9 * fps_ema + 0.1 / max(now - t_prev, 1e-6)
                t_prev = now

                if not args.quiet and (not preview) and now - last_print > 1.0:
                    last_print = now
                    hz = ", ".join(f"{h['kind']}#{h.get('car', '-')}@{h['track_m']:.0f}:"
                                   f"{int(h['conf'] * 100)}%" for h in msg["hazards"]) or "none"
                    cars = " ".join(f"#{c['car']}={c['track_m']}m/{c['speed_kmh']:.0f}kmh"
                                    for c in msg["cars"])
                    print(f"t={t:6.1f} fps={fps_ema:4.0f} {sender.status()} | {cars} | "
                          f"occ={int(msg['occluded'])} | hazards: {hz}", flush=True)

            key = -1
            if preview:
                lines = [f"fps {fps_ema:.0f}  t {t:.1f}s  {sender.status()}",
                         f"sent: {len(msg['cars'])} cars, {len(msg['hazards'])} hazards"
                         + ("  [PAUSED]" if paused else "")]
                if metrics:
                    lines.append(f"METRICS {metrics.kind}: trial {len(metrics.rows) + 1}/{metrics.trials}"
                                 + (" - waiting for detection" if metrics.placed_t is not None
                                    else " - press p when placed"))
                try:
                    cv2.imshow(win, pipe.draw(frame, lines))
                    key = cv2.waitKey(1) & 0xFF
                except cv2.error:
                    print("no display available - continuing with --no-preview")
                    preview = False
            if key in (ord("q"), 27):
                break
            if key == ord("r"):
                pipe.reset()
                print("reset: cars re-armed, debris reference retaken")
            elif key == ord("b"):
                pipe.scene.reset_reference()
                print("debris reference retaken")
            elif key == ord(" ") and is_file:
                paused = not paused
            elif key == ord("s"):
                C.FRAMES_DIR.mkdir(parents=True, exist_ok=True)
                p = C.FRAMES_DIR / f"still_{datetime.now():%H%M%S}.png"
                cv2.imwrite(str(p), frame)
                print(f"saved {p}")
            if metrics and key != -1:
                metrics.on_key(key, t, msg)
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        if writer is not None:
            writer.release()
        for f in (jlog, jlog2):
            if f:
                f.close()
        if metrics:
            metrics.report()
        sender.close()
        if preview:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
