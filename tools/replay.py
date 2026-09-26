"""Replay a recording into /ws/vision - the Scene 1 / Scene 2 fallback.

    # JSONL log from `vision.py --record`: exact messages, original timing
    python flagzero/tools/replay.py --jsonl flagzero/recordings/20260926_181500.jsonl
    # raw video from `vision.py --record`: runs the full live pipeline on it
    python flagzero/tools/replay.py --video flagzero/recordings/20260926_181500.mp4

Options: --speed 2 (twice as fast), --loop, --url wss://.../ws/vision.
The server can't tell a replay from the live camera; t_ms is re-stamped to now.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flagzero.vision import vision_config as C
from flagzero.vision.sender import VisionSender


def replay_jsonl(path: Path, url: str, speed: float, loop: bool):
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    if not rows:
        sys.exit("empty log")
    sender = VisionSender(url)
    for _ in range(30):                          # give the socket a moment
        if sender.connected:
            break
        time.sleep(0.1)
    print(f"{len(rows)} messages, {rows[-1]['t'] - rows[0]['t']:.1f} s  -> {url}")
    try:
        while True:
            t0_log, t0 = rows[0]["t"], time.monotonic()
            shown = set()
            for r in rows:
                wait = (r["t"] - t0_log) / speed - (time.monotonic() - t0)
                if wait > 0:
                    time.sleep(wait)
                msg = r["msg"]
                msg["t_ms"] = int(time.time() * 1000)
                sender.send(msg)
                for h in msg.get("hazards", []):
                    key = (h["kind"], h.get("car"))
                    if key not in shown:
                        shown.add(key)
                        print(f"  t={r['t'] - t0_log:5.1f}s  {h['kind']} car={h.get('car', '-')} "
                              f"{h['track_m']} m conf={h['conf']}")
            if not loop:
                break
            print("-- loop --")
        time.sleep(0.5)                          # let the last message flush
    except KeyboardInterrupt:
        pass
    print(sender.status())
    sender.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--jsonl", type=Path)
    g.add_argument("--video", type=Path)
    ap.add_argument("--url", default=C.SERVER_WS_URL)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--calib", type=Path, default=C.CALIB_PATH)
    ap.add_argument("--no-preview", action="store_true")
    a = ap.parse_args()
    if a.jsonl:
        replay_jsonl(a.jsonl, a.url, a.speed, a.loop)
    else:
        from flagzero.vision import vision
        argv = ["--source", str(a.video), "--url", a.url, "--calib", str(a.calib)]
        argv += ["--loop"] if a.loop else []
        argv += ["--no-preview"] if a.no_preview else []
        vision.main(argv)


if __name__ == "__main__":
    main()
