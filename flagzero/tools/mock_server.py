"""
flagzero/tools/mock_server.py -- stand-in for server.py's phone side (Person C)
================================================================================
Lets the phones be tested end to end before Person A's server.py exists.
Same port and paths as the real server, so the tunnel and phone URLs don't change:

    GET  /car?car=N      -> web/car.html
    GET  /web/car.js     -> web/car.js
    WS   /ws/car?car=N   -> phone socket (v2 protocol + docs/phone_changes.md)

Behaviour (a simplified version of what server.py should do):
  - IMPACT / SEVERE / ROLLOVER from car X: X gets a countdown (15 s); every other
    car gets a DBL YELLOW (level 3) warning with dist_m counting down from 400 m.
    SPIN -> YELLOW (level 2), no countdown. KERB -> ignored.
  - countdown_result OK -> medical MONITOR.  TIMEOUT -> medical URGENT + RED.
  - driver_request RED_FLAG -> RED.  (The real server only *recommends* red;
    race control confirms it on the dashboard. This mock skips that step.)
  - driver_request FALSE_ALARM (below RED only) or green_flag -> reset everyone.
  - Pings every phone each second; prints detect -> warning-ack latency.

Run from the repo root (instead of server.py, same port 8000):
    py -m flagzero.tools.mock_server
    cloudflared tunnel --url http://localhost:8000
"""
import asyncio
import itertools
import json
import pathlib
import time
from urllib.parse import parse_qs, urlparse

from websockets.asyncio.server import broadcast, serve
from websockets.datastructures import Headers
from websockets.http11 import Response

PORT = 8000
CORNER = "T4"
START_DISTANCE_M = 400
CLOSING_SPEED_MPS = 50
COUNTDOWN_S = 15

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
STATIC = {"/car": "car.html", "/web/car.html": "car.html", "/web/car.js": "car.js"}
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8"}

cars = {}           # car number -> websocket
incident = None     # {"car", "level", "start"} or None
ids = itertools.count(1)
pending_acks = {}   # warning/ping id -> (car, server send time, kind)
detect_time = None
latency_logged = False


def now_ms():
    return time.time() * 1000


def process_request(connection, request):
    path = urlparse(request.path).path
    if path == "/ws/car":
        return None
    name = STATIC.get(path)
    if name is None:
        return connection.respond(404, "Not found\n")
    body = (WEB / name).read_bytes()
    headers = Headers([
        ("Content-Type", CONTENT_TYPES[pathlib.Path(name).suffix]),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
        ("Connection", "close"),
    ])
    return Response(200, "OK", headers, body)


async def send_to(car, msg):
    ws = cars.get(car)
    if ws:
        try:
            await ws.send(json.dumps(msg))
        except Exception:
            pass


def reset_all(reason):
    global incident
    print(f"[mock] GREEN FLAG ({reason})")
    incident = None
    broadcast(list(cars.values()), json.dumps({"type": "reset"}))


async def set_red(reason):
    if incident:
        incident["level"] = 5
        print(f"[mock] RED FLAG ({reason})")


async def handle(ws):
    global incident, detect_time, latency_logged
    car = int(parse_qs(urlparse(ws.request.path).query).get("car", ["0"])[0])
    cars[car] = ws
    print(f"[mock] car {car} connected; cars: {sorted(cars)}")
    try:
        async for raw in ws:
            m = json.loads(raw)
            t = m.get("type")
            if t == "tel":
                continue
            if t == "ack":
                sent = pending_acks.pop(m.get("id"), None)
                if sent and sent[2] == "warning" and detect_time and not latency_logged:
                    latency_logged = True
                    print(f"[mock] car {car} acked its first warning {now_ms() - detect_time:.0f} ms after the imu_event arrived")
                continue
            print(f"[mock] car {car}: {m}")
            if t == "imu_event" and m.get("cls") in ("IMPACT", "SEVERE", "ROLLOVER", "SPIN"):
                crash = m["cls"] != "SPIN"
                incident = {"car": car, "level": 3 if crash else 2, "start": time.time()}
                detect_time, latency_logged = now_ms(), False
                if crash:
                    await send_to(car, {"type": "countdown", "secs": COUNTDOWN_S, "peak_g": m.get("peak_g")})
            elif t == "countdown_result":
                await send_to(car, {"type": "medical", "status": "MONITOR" if m["result"] == "OK" else "URGENT"})
                if m["result"] == "TIMEOUT":
                    await set_red("no driver response")
            elif t == "driver_request" and incident:
                if m["request"] == "RED_FLAG":
                    await set_red("driver recommended red")
                elif m["request"] == "FALSE_ALARM" and incident["level"] < 5:
                    reset_all(f"car {car} reported a false alarm")
            elif t == "green_flag":
                reset_all(f"green flag from car {car}")
    finally:
        if cars.get(car) is ws:
            del cars[car]
        print(f"[mock] car {car} disconnected")


async def warning_loop():
    """Every 250 ms, send each car its warning so dist_m / eta_s count down live."""
    while True:
        await asyncio.sleep(0.25)
        if not incident:
            continue
        dist = max(0.0, START_DISTANCE_M - CLOSING_SPEED_MPS * (time.time() - incident["start"]))
        for car in list(cars):
            at_scene = car == incident["car"]
            wid = next(ids)
            pending_acks[wid] = (car, now_ms(), "warning")
            await send_to(car, {
                "type": "warning", "id": wid, "level": incident["level"],
                "label": ["NORMAL", "CAUTION", "YELLOW", "DBL YELLOW", "SLOW ZONE", "RED"][incident["level"]],
                "corner": CORNER,
                "dist_m": None if at_scene else round(dist),
                "eta_s": None if at_scene else round(dist / CLOSING_SPEED_MPS, 1),
            })


async def ping_loop():
    while True:
        await asyncio.sleep(1)
        for car in list(cars):
            pid = next(ids)
            pending_acks[pid] = (car, now_ms(), "ping")
            await send_to(car, {"type": "ping", "id": pid})
        if len(pending_acks) > 500:  # don't grow forever if a phone never acks
            pending_acks.clear()


async def main():
    async with serve(handle, "0.0.0.0", PORT, process_request=process_request):
        print(f"[mock] mock server on http://localhost:{PORT}/car?car=17  (stand-in for server.py)")
        print(f"[mock] next: cloudflared tunnel --url http://localhost:{PORT}")
        await asyncio.gather(warning_loop(), ping_loop())


if __name__ == "__main__":
    asyncio.run(main())
