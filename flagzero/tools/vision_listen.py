"""Stand-in for Person A's server: listens on /ws/vision and prints what arrives.

    python flagzero/tools/vision_listen.py            # ws://localhost:8000/ws/vision
    python flagzero/tools/vision_listen.py --port 8765 --log out.jsonl

Use it to test vision.py before server.py exists (don't run both on port 8000).
Prints one line per second plus every hazard the moment it first appears.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import websockets

try:
    from websockets.asyncio.server import serve     # websockets >= 13
    NEW_API = True
except ImportError:                                 # pragma: no cover
    from websockets import serve
    NEW_API = False


class Stats:
    def __init__(self, log: Path | None):
        self.n = 0
        self.active = set()
        self.first_seen = {}
        self.t0 = time.monotonic()
        self.last_line = 0.0
        self.log = log.open("w") if log else None


async def main(host, port, log):
    st = Stats(log)

    async def handler(ws, path=None):
        path = path or getattr(getattr(ws, "request", None), "path", "/")
        print(f"+ client connected on {path}")
        try:
            async for raw in ws:
                recv_ms = int(time.time() * 1000)
                msg = json.loads(raw)
                st.n += 1
                if st.log:
                    st.log.write(json.dumps({"recv_ms": recv_ms, "msg": msg}) + "\n")
                    st.log.flush()
                kinds = {(h["kind"], h.get("car")) for h in msg.get("hazards", [])}
                for k in kinds - st.active:
                    h = next(h for h in msg["hazards"] if (h["kind"], h.get("car")) == k)
                    tag = "PREDICTED " if h.get("predicted") else ""
                    t = time.monotonic() - st.t0
                    st.first_seen.setdefault(k, t)
                    print(f"  >> t={t:6.2f}s NEW {tag}{h['kind']} car={h.get('car', '-')} "
                          f"@{h['track_m']} m conf={h['conf']} {h.get('detail', {})}")
                for k in st.active - kinds:
                    print(f"  << t={time.monotonic() - st.t0:6.2f}s cleared {k[0]} car={k[1]}")
                st.active = kinds
                now = time.monotonic()
                if now - st.last_line > 1.0:
                    st.last_line = now
                    cars = " ".join(f"#{c['car']}={c['track_m']}m" for c in msg.get("cars", []))
                    lat = recv_ms - msg.get("t_ms", recv_ms)
                    print(f"{st.n:6d} msgs | {cars} | occluded={msg.get('occluded')} | "
                          f"send->recv {lat} ms")
        except websockets.ConnectionClosed:
            pass
        print("- client disconnected")

    async with serve(handler, host, port):
        print(f"listening on ws://{host}:{port}/ws/vision  (Ctrl-C to stop)")
        await asyncio.Future()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--log", type=Path)
    a = ap.parse_args()
    try:
        asyncio.run(main(a.host, a.port, a.log))
    except KeyboardInterrupt:
        pass
