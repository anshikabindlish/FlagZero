"""
flagzero/tools/mock_car.py -- fake phone from the keyboard (Person C)
=====================================================================
Connects to /ws/car?car=N like a real phone and sends v2 events when you type
a letter and press Enter. Prints every message the server sends back (pings
and warnings are acked automatically, like the real phone).

    i = IMPACT      s = SPIN       k = KERB       r = ROLLOVER    v = SEVERE
    t = imu_update still:true      o = ok_pressed + countdown_result OK
    x = countdown_result TIMEOUT   f = driver_request FALSE_ALARM
    d = driver_request RED_FLAG    g = green_flag                  q = quit

Run from the repo root:
    py -m flagzero.tools.mock_car --car 17
    py -m flagzero.tools.mock_car --car 21 --url wss://<tunnel>.trycloudflare.com
"""
import argparse
import asyncio
import json

import websockets

EVENTS = {
    "i": [{"type": "imu_event", "cls": "IMPACT", "peak_g": 4.8, "dur_ms": 40, "gyro_peak": 180, "rot_deg": 25, "conf": 0.84, "capture_ms": 300}],
    "v": [{"type": "imu_event", "cls": "SEVERE", "peak_g": 7.5, "dur_ms": 55, "gyro_peak": 260, "rot_deg": 40, "conf": 0.9, "capture_ms": 300}],
    "s": [{"type": "imu_event", "cls": "SPIN", "peak_g": 1.8, "dur_ms": 0, "gyro_peak": 420, "rot_deg": 190, "conf": 0.72, "capture_ms": 300}],
    "k": [{"type": "imu_event", "cls": "KERB", "peak_g": 2.3, "dur_ms": 8, "gyro_peak": 60, "rot_deg": 5, "conf": 0.5, "capture_ms": 300}],
    "r": [{"type": "imu_event", "cls": "ROLLOVER", "peak_g": 3.1, "dur_ms": 30, "gyro_peak": 520, "rot_deg": 170, "conf": 0.9, "capture_ms": 300}],
    "t": [{"type": "imu_update", "still": True}],
    "o": [{"type": "ok_pressed"}, {"type": "countdown_result", "result": "OK"}],
    "x": [{"type": "countdown_result", "result": "TIMEOUT"}],
    "f": [{"type": "driver_request", "request": "FALSE_ALARM"}],
    "d": [{"type": "driver_request", "request": "RED_FLAG"}],
    "g": [{"type": "green_flag"}],
}


async def receiver(ws, car):
    async for raw in ws:
        m = json.loads(raw)
        if m.get("type") in ("ping", "warning") and m.get("id") is not None:
            await ws.send(json.dumps({"type": "ack", "car": car, "id": m["id"]}))
        if m.get("type") == "ping":
            continue
        print(f"  <- {m}")


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--car", type=int, default=17)
    ap.add_argument("--url", default="ws://localhost:8000", help="server base, e.g. wss://xyz.trycloudflare.com")
    args = ap.parse_args()

    url = f"{args.url.rstrip('/')}/ws/car?car={args.car}"
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"type": "hello", "car": args.car, "platform": "mock"}))
        print(f"connected as car {args.car} to {url}")
        print("keys: i impact, v severe, s spin, k kerb, r rollover, t still, o ok, x timeout, f false alarm, d red request, g green, q quit")
        rx = asyncio.create_task(receiver(ws, args.car))
        loop = asyncio.get_running_loop()
        while True:
            key = (await loop.run_in_executor(None, input)).strip().lower()[:1]
            if key == "q":
                break
            for msg in EVENTS.get(key, []):
                await ws.send(json.dumps({"car": args.car, **msg}))
                print(f"  -> {msg}")
            if key not in EVENTS and key:
                print("  unknown key")
        rx.cancel()


if __name__ == "__main__":
    asyncio.run(main())
