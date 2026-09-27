"""Play demo Scene 1 or 2 against the running server, pretending to be both
phones. Proves the whole core works with no hardware.

Server must be running. Then:
    python3 -m flagzero.tools.scene_check 1     (car 17 spins -> car 21 goes YELLOW)
    python3 -m flagzero.tools.scene_check 2     (crash -> no OK in 15 s -> automatic RED)
Use --url wss://<tunnel>.trycloudflare.com to test through the tunnel.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import urllib.request

import websockets


async def phone(ws, name, log):
    async for raw in ws:
        m = json.loads(raw)
        if m["type"] in ("ping", "warning"):
            await ws.send(json.dumps({"type": "ack", "id": m["id"]}))
        if m["type"] != "ping":
            log.append((name, m))


async def wait_state(d, cond, timeout=15.0):
    async def loop():
        while True:
            st = json.loads(await d.recv())
            if st.get("type") == "state" and cond(st):
                return st
    return await asyncio.wait_for(loop(), timeout)


async def run(scene: int, base: str) -> None:
    log: list = []
    async with websockets.connect(f"{base}/ws/car?car=17") as p17, \
            websockets.connect(f"{base}/ws/car?car=21") as p21, \
            websockets.connect(f"{base}/ws/dash") as d:
        tasks = [asyncio.create_task(phone(p17, "car17", log)), asyncio.create_task(phone(p21, "car21", log))]
        for ws, car, plat in ((p17, 17, "ios"), (p21, 21, "android")):
            await ws.send(json.dumps({"type": "hello", "car": car, "platform": plat}))
        await d.send(json.dumps({"type": "scene", "n": scene}))
        await asyncio.sleep(0.5)
        st0 = await wait_state(d, lambda s: s["cars"])
        pos17 = round(next(c["track_m"] for c in st0["cars"] if c["car"] == 17), 1)

        if scene == 1:
            print(f"car 17 spins at {pos17} m and keeps going ...")
            await p17.send(json.dumps({"type": "imu_event", "car": 17, "cls": "SPIN", "peak_g": 1.8,
                                       "dur_ms": 0, "gyro_peak": 420, "rot_deg": 190, "conf": 0.72,
                                       "capture_ms": 300}))
            st = await wait_state(d, lambda s: s["incidents"])
        else:
            print(f"car 17 crashes at {pos17} m, phone dropped on the cushion ...")
            await p17.send(json.dumps({"type": "imu_event", "car": 17, "cls": "IMPACT", "peak_g": 5.2,
                                       "dur_ms": 40, "gyro_peak": 380, "rot_deg": 35, "conf": 0.88,
                                       "capture_ms": 300}))
            await asyncio.sleep(1.5)
            await p17.send(json.dumps({"type": "imu_update", "car": 17, "still": True}))
            print("nobody presses OK within 15 s (phone reports TIMEOUT) ...")
            await p17.send(json.dumps({"type": "countdown_result", "car": 17, "result": "TIMEOUT"}))
            st = await wait_state(d, lambda s: s["red_confirmed"])

        inc = st["incidents"][0]
        print(f"\nDASHBOARD  {inc['kind']} at {inc['corner']} {inc['track_m']} m | severity {inc['severity']} "
              f"{inc['label']} | fused {inc['fused_conf']*100:.1f}% | sources "
              f"{[s['src'] + ':' + s['kind'] for s in inc['sources']]} | medical {inc['medical']}")
        for a in inc["approaching"][:4]:
            print(f"  approaching #{a['car']:>2}: {a['dist_m']:>6} m  {a['speed_kmh']:>5} km/h  "
                  f"ETA {a['eta_s']} s  level {a['warning']}")
        if scene == 2:
            print(f"  vitals car 17: {st['vitals'].get('17')}")
            print(f"  RED raised automatically: {st['red_auto']} (no Confirm red click needed)")
        await asyncio.sleep(2.0)
        for t in tasks:
            t.cancel()

    w21 = [m for n, m in log if n == "car21" and m["type"] == "warning" and m["level"] > 0]
    print("\nCAR 21 PHONE saw:", " -> ".join(f"{m['label']} {m['dist_m']}m" for m in w21[::4][:8]),
          f"... last: {w21[-1]['label'] if w21 else 'nothing'}")
    print("CAR 17 PHONE saw:", [m["type"] + (":" + m["status"] if m["type"] == "medical" else "")
                               for n, m in log if n == "car17" and m["type"] != "warning"])
    http = base.replace("ws", "http", 1)
    lat = json.loads(urllib.request.urlopen(f"{http}/api/latency").read())
    print("LATENCY detect -> phone ack:", lat["detect_to_ack_ms"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scene", type=int, choices=[1, 2])
    ap.add_argument("--url", default="ws://localhost:8000")
    a = ap.parse_args()
    asyncio.run(run(a.scene, a.url.rstrip("/")))


if __name__ == "__main__":
    main()
