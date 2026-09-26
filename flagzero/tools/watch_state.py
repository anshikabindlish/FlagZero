"""Print the live "state" feed from the server, so you can check the sim
without a dashboard page.

Run (server must be running):  python3 -m flagzero.tools.watch_state
Ctrl+C to stop.
"""
from __future__ import annotations

import argparse
import asyncio
import json

import websockets


async def watch(url: str, every: int) -> None:
    async with websockets.connect(url) as ws:
        print(f"connected to {url}")
        n = 0
        async for raw in ws:
            msg = json.loads(raw)
            n += 1
            if msg.get("type") != "state" or n % every:
                continue
            cars = {c["car"]: c for c in msg["cars"]}
            line = "  ".join(
                f"#{k}: {cars[k]['track_m']:7.1f} m {cars[k]['speed_kmh']:5.0f} km/h {cars[k]['state']}"
                for k in (17, 21, 8) if k in cars)
            print(f"{len(cars)} cars | {line}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://localhost:8000/ws/dash")
    ap.add_argument("--every", type=int, default=10, help="print 1 in N messages")
    args = ap.parse_args()
    try:
        asyncio.run(watch(args.url, args.every))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
