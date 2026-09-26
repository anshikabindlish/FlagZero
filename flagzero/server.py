"""
flagzero/server.py

The hub. One FastAPI process everything else connects to:
  - phones            -> ws://<host>:8000/ws/car?car=N
  - the vision pipeline -> ws://<host>:8000/ws/vision
  - dashboard(s)      -> ws://<host>:8000/ws/dash

THIS FILE IS CURRENTLY A SKELETON (H0-0:45 deliverable). Every websocket
right now just logs whatever it receives (with a server-side receive
timestamp, per docs/protocol.md's rule that all latency is measured on
server time) and echoes it straight back -- that's enough for Person B and
Person C to confirm their end can open a socket, send a message, and get a
reply, before any real fusion/severity/routing logic exists.

The real logic (core/state.py, core/router.py, core/fusion.py, etc.) gets
wired in behind these same endpoints over the next few prompts (A1-A6) --
the endpoints and message shapes here should not need to change when that
happens, only what's *inside* them.

Run it:
    uvicorn flagzero.server:app --host 0.0.0.0 --port 8000 --reload

(run from the REPO ROOT, i.e. the folder that CONTAINS this "flagzero"
folder -- that's what makes "flagzero.server:app" resolve.)
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Dict, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse

from flagzero import config

app = FastAPI(title="FlagZero v2")

# Path to the web/ folder, relative to this file, so it works regardless of
# what directory uvicorn was launched from.
WEB_DIR = Path(__file__).resolve().parent / "web"

# ---------------------------------------------------------------------------
# Connection registries
# ---------------------------------------------------------------------------
# Phones, keyed by car number -- lets the router (built in A2) address a
# specific car directly ("send this warning to car 21").
car_sockets: Dict[int, WebSocket] = {}

# Vision and dashboard can each have more than one connection (e.g. a backup
# dashboard on a second laptop), so these are plain sets.
vision_sockets: Set[WebSocket] = set()
dash_sockets: Set[WebSocket] = set()


def now_ms() -> int:
    return int(time.time() * 1000)


def log_message(channel: str, data: dict) -> None:
    """Every inbound message gets logged with the server's own receive
    timestamp -- this is the timestamp all latency figures use later."""
    print(f"[{now_ms()}] <- {channel}: {json.dumps(data)}")


async def send_json(ws: WebSocket, data: dict) -> None:
    try:
        await ws.send_text(json.dumps(data))
    except Exception:
        pass  # socket likely already closed; the disconnect handler will clean it up


# ---------------------------------------------------------------------------
# /ws/car -- one connection per phone
# ---------------------------------------------------------------------------
@app.websocket("/ws/car")
async def ws_car(websocket: WebSocket, car: int):
    await websocket.accept()
    car_sockets[car] = websocket
    print(f"[{now_ms()}] car {car} connected ({len(car_sockets)} total)")

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                print(f"[{now_ms()}] car {car} sent unparseable line: {raw!r}")
                continue

            log_message(f"car {car}", data)

            # SKELETON BEHAVIOUR: echo everything straight back so B/C can
            # confirm round-trip works. Real handling (imu_event ->
            # incidents/fusion/severity, tel -> sim override, etc.) replaces
            # this in A1-A4.
            await send_json(websocket, data)

    except WebSocketDisconnect:
        pass
    finally:
        car_sockets.pop(car, None)
        print(f"[{now_ms()}] car {car} disconnected ({len(car_sockets)} total)")


# ---------------------------------------------------------------------------
# /ws/vision -- the overhead camera pipeline
# ---------------------------------------------------------------------------
@app.websocket("/ws/vision")
async def ws_vision(websocket: WebSocket):
    await websocket.accept()
    vision_sockets.add(websocket)
    print(f"[{now_ms()}] vision connected ({len(vision_sockets)} total)")

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                print(f"[{now_ms()}] vision sent unparseable line: {raw!r}")
                continue

            log_message("vision", data)

            # SKELETON BEHAVIOUR: echo back. Real handling (car position
            # override in sim, hazard -> incident creation) replaces this
            # once core/state.py and sim/sim.py exist (A1, A3).
            await send_json(websocket, data)

    except WebSocketDisconnect:
        pass
    finally:
        vision_sockets.discard(websocket)
        print(f"[{now_ms()}] vision disconnected ({len(vision_sockets)} total)")


# ---------------------------------------------------------------------------
# /ws/dash -- one or more dashboards
# ---------------------------------------------------------------------------
@app.websocket("/ws/dash")
async def ws_dash(websocket: WebSocket):
    await websocket.accept()
    dash_sockets.add(websocket)
    print(f"[{now_ms()}] dashboard connected ({len(dash_sockets)} total)")

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                print(f"[{now_ms()}] dashboard sent unparseable line: {raw!r}")
                continue

            log_message("dashboard", data)

            # SKELETON BEHAVIOUR: echo back. Real handling of confirm_red /
            # reset / scene commands is wired in during A4.
            await send_json(websocket, data)

    except WebSocketDisconnect:
        pass
    finally:
        dash_sockets.discard(websocket)
        print(f"[{now_ms()}] dashboard disconnected ({len(dash_sockets)} total)")


# ---------------------------------------------------------------------------
# 20 Hz broadcast loop -> /ws/dash
# ---------------------------------------------------------------------------
async def broadcast_loop():
    """
    Runs for the lifetime of the server. Right now broadcasts an empty
    "state" message on every tick, purely to prove the loop and the
    broadcast plumbing work end to end. core/state.py (A1) replaces the
    empty payload with the real world snapshot.
    """
    tick_interval = 1.0 / config.DASH_BROADCAST_HZ
    while True:
        if dash_sockets:
            message = json.dumps({
                "type": "state",
                "cars": [],
                "incidents": [],
                "latency": {"tunnel_rtt_ms": None, "last_detect_to_warn_ms": None},
                "red_pending": False,
            })
            # gather so one slow/dead socket doesn't stall the others
            await asyncio.gather(
                *(send_json_raw(ws, message) for ws in list(dash_sockets)),
                return_exceptions=True,
            )
        await asyncio.sleep(tick_interval)


async def send_json_raw(ws: WebSocket, message: str) -> None:
    try:
        await ws.send_text(message)
    except Exception:
        pass


@app.on_event("startup")
async def start_broadcast_loop():
    asyncio.create_task(broadcast_loop())


# ---------------------------------------------------------------------------
# Static HTTP routes
# ---------------------------------------------------------------------------
@app.get("/car")
async def get_car_page(car: int):
    """JS on the page itself reads `car` back out of the URL query string;
    the server just needs to serve the same file regardless of which car."""
    path = WEB_DIR / "car.html"
    if path.exists():
        return FileResponse(path)
    return HTMLResponse(f"<h1>car.html not built yet</h1><p>Expected at {path}</p>", status_code=200)


@app.get("/dashboard")
async def get_dashboard_page():
    path = WEB_DIR / "dashboard.html"
    if path.exists():
        return FileResponse(path)
    return HTMLResponse(f"<h1>dashboard.html not built yet</h1><p>Expected at {path}</p>", status_code=200)


@app.get("/join")
async def get_join_page():
    path = WEB_DIR / "join.html"
    if path.exists():
        return FileResponse(path)
    return HTMLResponse(f"<h1>join.html not built yet</h1><p>Expected at {path}</p>", status_code=200)


@app.get("/")
async def root():
    return HTMLResponse(
        "<h1>FlagZero v2 server is running</h1>"
        "<p>Try <a href='/dashboard'>/dashboard</a>, <a href='/join'>/join</a>, "
        "or <a href='/car?car=17'>/car?car=17</a>.</p>"
    )
