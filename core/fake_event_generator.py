"""
/core stub -- fake event generator
===================================
Purpose: give /vision and /dashboard something real to point at from H0:30,
before fusion logic or the serial link to the Arduino exist.

What it does:
  - Runs a WebSocket server on ws://localhost:8765
  - Every few seconds, "detects" a scripted incident (alternating imu/camera
    source, per shared/schemas.py), builds a trivial derived Decision from
    it, and broadcasts both to every connected client using the message
    envelope documented in shared/ws_messages.md.
  - Also emits a periodic eta_update and monte_carlo_result so all four
    message types get exercised end-to-end.
  - Logs any message a client sends back, which is the hook /vision can use
    to push its dummy detections in for a quick smoke test -- see the
    commented-out send block at the bottom of vision/stub_detector.py.

Run from the repo root:
    python -m core.fake_event_generator

Then point dashboard/index.html (or any ws client) at ws://localhost:8765.

Replace the scripted SCENARIOS loop with real fusion logic (IMU serial read
+ vision detections in, Decision out) as the hackathon progresses -- the
message shapes shouldn't need to change when you do.
"""
import asyncio
import itertools
import json
import random

import websockets

from shared import schemas, track_config

HOST = "localhost"
PORT = 8765
TICK_SECONDS = 4  # how often the stub "detects" something

# A tiny script so every demo run looks the same while testing. Swap car_id
# / event / source / severity as fits whatever scene you're rehearsing.
SCENARIOS = itertools.cycle([
    {"event": "spin", "source": "imu", "car_id": 17, "severity": 2},
    {"event": "crash", "source": "camera", "car_id": 4, "severity": 4},
    {"event": "disabled", "source": "imu", "car_id": 9, "severity": 3},
    {"event": "debris", "source": "camera", "car_id": None, "severity": 1},
])

CONNECTED = set()


async def handler(websocket):
    CONNECTED.add(websocket)
    print(f"[core] client connected ({len(CONNECTED)} total)")
    try:
        async for raw in websocket:
            # Anything a client sends us gets logged -- this is the hook
            # /vision can use to push real detections in later.
            try:
                msg = json.loads(raw)
                print(f"[core] received from client: {msg}")
            except json.JSONDecodeError:
                print(f"[core] received non-JSON from client: {raw!r}")
    finally:
        CONNECTED.discard(websocket)
        print(f"[core] client disconnected ({len(CONNECTED)} left)")


async def broadcast(envelope: dict):
    if not CONNECTED:
        return
    payload = json.dumps(envelope)
    results = await asyncio.gather(
        *(ws.send(payload) for ws in CONNECTED), return_exceptions=True
    )
    for r in results:
        if isinstance(r, Exception):
            print(f"[core] broadcast error: {r!r}")


async def tick_loop():
    zone = "T4"
    pos_m = track_config.zone_position_m(zone)

    while True:
        await asyncio.sleep(TICK_SECONDS)
        scenario = next(SCENARIOS)

        incident = schemas.new_incident(
            event=scenario["event"],
            source=scenario["source"],
            car_id=scenario["car_id"],
            location=zone,
            track_position_m=pos_m,
            confidence=round(random.uniform(0.75, 0.99), 2),
        )
        schemas.validate_incident(incident)
        print(f"[core] incident: {incident}")
        await broadcast(schemas.ws_envelope("incident", incident))

        # trivial "fusion": just carry the scripted severity straight through
        decision = schemas.new_decision(
            severity=scenario["severity"],
            fused_confidence=incident["confidence"],
            location=zone,
            track_position_m=pos_m,
            medical_response=(scenario["severity"] >= 4),
            car_id=scenario["car_id"],
            contributing_incident_ids=[incident["id"]],
        )
        schemas.validate_decision(decision)
        print(f"[core] decision: {decision}")
        await broadcast(schemas.ws_envelope("severity_update", decision))

        # exercise the other two message types too, with made-up numbers
        await broadcast(schemas.ws_envelope("eta_update", {
            "car_id": scenario["car_id"],
            "location": zone,
            "track_position_m": pos_m,
            "eta_s": round(random.uniform(2, 15), 1),
            "speed_mps": round(random.uniform(20, 45), 1),
        }))
        await broadcast(schemas.ws_envelope("monte_carlo_result", {
            "scenario": f"{zone}_collision_risk",
            "trials": 10000,
            "p_collision": round(random.uniform(0.05, 0.7), 3),
            "p_severity_gte_3": round(random.uniform(0.02, 0.4), 3),
            "recommended_label": schemas.SEVERITY_LABELS.get(scenario["severity"], "CAUTION"),
        }))


async def main():
    async with websockets.serve(handler, HOST, PORT):
        print(f"[core] fake event generator listening on ws://{HOST}:{PORT}")
        await tick_loop()


if __name__ == "__main__":
    asyncio.run(main())
