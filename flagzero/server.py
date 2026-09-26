"""FlagZero race-control server.

Run from the repo root:
    python3 -m uvicorn flagzero.server:app --host 0.0.0.0 --port 8000 --reload

One process. Phones connect to /ws/car?car=N, the camera to /ws/vision,
dashboards to /ws/dash. A 20 Hz loop runs the engine (sim, severity, router,
medical) and broadcasts a "state" message to every dashboard at 10 Hz.
All the logic lives in core/engine.py; this file only moves messages.
"""
from __future__ import annotations

from typing import Optional

import asyncio
import contextlib
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from flagzero import config
from flagzero.core.engine import Engine
from flagzero.core.state import now_ms
from flagzero.sim import montecarlo

log = logging.getLogger("flagzero")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

engine = Engine()
world = engine.world


# ---------------------------------------------------------------- helpers
async def send_json(ws: WebSocket, msg: dict) -> bool:
    try:
        await ws.send_text(json.dumps(msg))
        return True
    except Exception:
        return False


async def broadcast_dash(msg: dict) -> None:
    dead = [ws for ws in list(world.dash_sockets) if not await send_json(ws, msg)]
    for ws in dead:
        world.dash_sockets.discard(ws)


async def send_out(out: list[tuple[int, dict]]) -> None:
    for car, msg in out:
        ws = world.car_sockets.get(car)
        if ws is not None:
            await send_json(ws, msg)


def connected() -> set[int]:
    return set(world.car_sockets)


# ---------------------------------------------------------------- main loop
async def main_loop() -> None:
    dt = 1.0 / config.SERVER_TICK_HZ
    every = max(1, config.SERVER_TICK_HZ // config.DASH_BROADCAST_HZ)
    tick = 0
    next_t = time.monotonic()
    last_ping = 0.0
    while True:
        try:
            now = time.monotonic()
            await send_out(engine.tick(dt, connected(), now_s=now))
            if tick % every == 0:
                await broadcast_dash(world.state_message())
            if now - last_ping >= config.PHONE_PING_INTERVAL_S:
                last_ping = now
                await send_out(engine.pings(connected()))
        except Exception:
            log.exception("main loop error (continuing)")
        tick += 1
        next_t += dt
        await asyncio.sleep(max(0.0, next_t - time.monotonic()))


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(main_loop())
    log.info("engine started: %d cars, %d Hz", len(world.cars), config.SERVER_TICK_HZ)
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
        "<li><a href='/api/track'>/api/track</a></li>"
        "<li><a href='/api/montecarlo?n=10000&seed=42'>/api/montecarlo</a></li>"
        "<li><a href='/api/latency'>/api/latency</a></li></ul>"
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


# ---------------------------------------------------------------- APIs
@app.get("/api/track")
def api_track() -> FileResponse:
    return FileResponse(config.TRACK_FILE)


@app.get("/api/state")
def api_state() -> JSONResponse:
    return JSONResponse(world.state_message())


@app.get("/api/montecarlo")
def api_montecarlo(n: int = config.MC_N, seed: Optional[int] = None,
                   visibility: Optional[float] = None,
                   marshal_react_min: Optional[float] = None, marshal_react_max: Optional[float] = None,
                   speed_min: Optional[float] = None, speed_max: Optional[float] = None,
                   sightline_min: Optional[float] = None, sightline_max: Optional[float] = None,
                   gap_min: Optional[float] = None, gap_max: Optional[float] = None) -> JSONResponse:
    """Re-run the Monte Carlo with the dashboard's slider values."""
    res = montecarlo.run(n, seed, visibility=visibility,
                         marshal_react_min=marshal_react_min, marshal_react_max=marshal_react_max,
                         speed_min=speed_min, speed_max=speed_max,
                         sightline_min=sightline_min, sightline_max=sightline_max,
                         gap_min=gap_min, gap_max=gap_max)
    montecarlo.save(res)
    return JSONResponse(res)


@app.get("/api/montecarlo/last")
def api_montecarlo_last() -> Response:
    """Static fallback for the never-cut results table."""
    f = config.RESULTS_DIR / "montecarlo.json"
    return FileResponse(f) if f.exists() else JSONResponse({"error": "no saved run yet"}, status_code=404)


@app.get("/api/latency")
def api_latency() -> JSONResponse:
    from flagzero.core import latency
    return JSONResponse({"live": world.latency,
                         "detect_to_ack_ms": latency.stats("detect_to_ack_ms"),
                         "detect_to_sent_ms": latency.stats("detect_to_sent_ms")})


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
async def _read_json(ws: WebSocket) -> tuple[Optional[dict], int]:
    raw = await ws.receive_text()
    t = now_ms()                      # stamp receive time before parsing
    try:
        msg = json.loads(raw)
        return (msg if isinstance(msg, dict) else None), t
    except json.JSONDecodeError:
        return None, t


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
            msg, t = await _read_json(ws)
            if msg is not None:
                await send_out(engine.on_car(car, msg, t))
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
            msg, t = await _read_json(ws)
            if msg is not None and msg.get("type") == "vision":
                engine.on_vision(msg, t, time.monotonic())
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
            msg, _ = await _read_json(ws)
            if msg is not None:
                await send_out(engine.on_dash(msg, connected()))
    except WebSocketDisconnect:
        pass
    finally:
        world.dash_sockets.discard(ws)
