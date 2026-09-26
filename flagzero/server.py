"""FlagZero race-control server.

Run from the repo root:
    python3 -m uvicorn flagzero.server:app --host 0.0.0.0 --port 8000 --reload

One process. Phones connect to /ws/car?car=N, the camera to /ws/vision,
dashboards to /ws/dash. A 20 Hz loop advances the simulation and broadcasts a
"state" message to every dashboard at 10 Hz.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from flagzero import config
from flagzero.core.state import WorldState, now_ms
from flagzero.sim.sim import Simulation

log = logging.getLogger("flagzero")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

world = WorldState()
sim = Simulation(world)
_ping_id = 0
_pings_sent: dict[int, tuple[int, int]] = {}   # ping id -> (car, sent_ms)


# ---------------------------------------------------------------- helpers
async def send_json(ws: WebSocket, msg: dict) -> bool:
    try:
        await ws.send_text(json.dumps(msg))
        return True
    except Exception:
        return False


async def broadcast_dash(msg: dict) -> None:
    dead = []
    for ws in list(world.dash_sockets):
        if not await send_json(ws, msg):
            dead.append(ws)
    for ws in dead:
        world.dash_sockets.discard(ws)


async def send_to_car(car: int, msg: dict) -> bool:
    ws = world.car_sockets.get(car)
    return bool(ws) and await send_json(ws, msg)


# ---------------------------------------------------------------- main loop
async def main_loop() -> None:
    dt = 1.0 / config.SERVER_TICK_HZ
    every = max(1, config.SERVER_TICK_HZ // config.DASH_BROADCAST_HZ)
    tick = 0
    next_t = time.monotonic()
    last_ping = 0.0
    global _ping_id
    while True:
        now = time.monotonic()
        sim.tick(dt, now_s=now)
        # later: router.update(world), severity.update(world) go here (A2, A3)
        if tick % every == 0:
            await broadcast_dash(world.state_message())
        if now - last_ping >= config.PHONE_PING_INTERVAL_S:
            last_ping = now
            for car in list(world.car_sockets):
                _ping_id += 1
                _pings_sent[_ping_id] = (car, now_ms())
                await send_to_car(car, {"type": "ping", "id": _ping_id})
            if len(_pings_sent) > 500:
                for k in sorted(_pings_sent)[:250]:
                    _pings_sent.pop(k, None)
        tick += 1
        next_t += dt
        await asyncio.sleep(max(0.0, next_t - time.monotonic()))


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(main_loop())
    log.info("sim loop started: %d cars, %d Hz", len(world.cars), config.SERVER_TICK_HZ)
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="FlagZero", lifespan=lifespan)


# ---------------------------------------------------------------- pages
def _page(name: str, title: str) -> Response:
    f = config.WEB_DIR / name
    if f.exists():
        return FileResponse(f)
    return HTMLResponse(f"<h2>{title}</h2><p>{name} not built yet (Person C).</p>")


@app.get("/")
def index() -> HTMLResponse:
    return HTMLResponse(
        "<h2>FlagZero v2 server is running</h2><ul>"
        "<li><a href='/dashboard'>/dashboard</a></li>"
        "<li><a href='/join'>/join</a></li>"
        "<li><a href='/car?car=17'>/car?car=17</a></li>"
        "<li><a href='/car?car=21'>/car?car=21</a></li>"
        "<li><a href='/api/state'>/api/state</a> (live JSON)</li>"
        "<li><a href='/api/track'>/api/track</a></li></ul>"
    )


@app.get("/car")
def car_page() -> Response:
    return _page("car.html", "Car node")


@app.get("/dashboard")
def dashboard_page() -> Response:
    return _page("dashboard.html", "Dashboard")


@app.get("/join")
def join_page() -> Response:
    return _page("join.html", "Join")


@app.get("/api/track")
def api_track() -> FileResponse:
    return FileResponse(config.TRACK_FILE)


@app.get("/api/state")
def api_state() -> JSONResponse:
    return JSONResponse(world.state_message())


@app.get("/{name}.{ext}")
def web_asset(name: str, ext: str) -> Response:
    """Serves car.js, dashboard.js, style.css ... from flagzero/web/."""
    if ext not in {"js", "css", "png", "svg", "ico", "json", "wav", "mp3"}:
        return Response(status_code=404)
    f = (config.WEB_DIR / f"{name}.{ext}").resolve()
    if config.WEB_DIR.resolve() not in f.parents or not f.exists():
        return Response(status_code=404)
    return FileResponse(f)


# ---------------------------------------------------------------- websockets
@app.websocket("/ws/car")
async def ws_car(ws: WebSocket) -> None:
    await ws.accept()
    try:
        car = int(ws.query_params.get("car", "0"))
    except ValueError:
        car = 0
    world.car_sockets[car] = ws
    log.info("phone connected: car %s", car)
    try:
        while True:
            raw = await ws.receive_text()
            recv_ms = now_ms()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg["_recv_ms"] = recv_ms
            kind = msg.get("type")
            if kind == "ack" and msg.get("id") in _pings_sent:
                pcar, sent = _pings_sent.pop(msg["id"])
                world.latency[f"rtt_car{pcar}_ms"] = recv_ms - sent
            elif kind == "tel":
                world.latency.setdefault("tel", {})[str(car)] = {
                    "g": msg.get("g"), "gyro": msg.get("gyro"), "ms": recv_ms}
            else:
                log.info("car %s -> %s", car, msg)
                # later: incidents.handle_car_message(world, sim, msg) (A3)
    except WebSocketDisconnect:
        pass
    finally:
        if world.car_sockets.get(car) is ws:
            world.car_sockets.pop(car, None)
        log.info("phone disconnected: car %s", car)


@app.websocket("/ws/vision")
async def ws_vision(ws: WebSocket) -> None:
    await ws.accept()
    world.vision_sockets.add(ws)
    log.info("vision connected")
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg["_recv_ms"] = now_ms()
            if msg.get("type") != "vision":
                continue
            now = time.monotonic()
            for c in msg.get("cars", []):
                if "car" in c and "track_m" in c:
                    sim.apply_camera(int(c["car"]), float(c["track_m"]), now_s=now)
            world.last_vision = msg
            # later: incidents.handle_vision_hazards(world, sim, msg["hazards"]) (A3)
    except WebSocketDisconnect:
        pass
    finally:
        world.vision_sockets.discard(ws)
        log.info("vision disconnected")


@app.websocket("/ws/dash")
async def ws_dash(ws: WebSocket) -> None:
    await ws.accept()
    world.dash_sockets.add(ws)
    log.info("dashboard connected (%d open)", len(world.dash_sockets))
    await send_json(ws, world.state_message())
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = msg.get("type")
            log.info("dashboard -> %s", msg)
            if kind == "reset":
                world.incidents.clear()
                world.red_pending = world.red_confirmed = False
                sim.release_all()
                for c in world.cars.values():
                    c.warning = 0
                for car in list(world.car_sockets):
                    await send_to_car(car, {"type": "reset"})
            elif kind == "confirm_red":
                world.red_confirmed = True     # A4 sends RED to the phones
            elif kind == "scene":
                pass                           # A4 pre-positions cars per scene
    except WebSocketDisconnect:
        pass
    finally:
        world.dash_sockets.discard(ws)
