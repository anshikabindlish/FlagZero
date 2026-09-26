# /core — Person A

Owns: fusing IMU + camera Incidents into a Decision, the serial link to the
Arduino, and the WebSocket server the dashboard connects to.

## Stub: `fake_event_generator.py`

Run from the **repo root**:

```
pip install -r core/requirements.txt
python -m core.fake_event_generator
```

Starts a WebSocket server on `ws://localhost:8765` and, every 4 seconds,
broadcasts a scripted `incident` + the `severity_update` derived from it,
plus a dummy `eta_update` and `monte_carlo_result` — see
`shared/ws_messages.md` for the message shapes. Point `dashboard/index.html`
at it to see all four types render.

It also logs anything a connected client sends back, which is the quickest
way for /vision to smoke-test pushing a real detection in before real
fusion logic exists — see the commented-out send block at the bottom of
`vision/stub_detector.py`.

## `scene_config.json`

Since one physical Arduino rig "plays" whichever car a demo scene needs
(see `shared/serial_protocol.md`), the mapping from "this rig's telemetry"
to "which car_id" lives here, not in firmware. Your real serial-ingest code
should read `active_car_id` from this file when it tags incoming IMU
telemetry with a car_id and builds an Incident from it.

## Replacing the stub

Swap the scripted `SCENARIOS` loop in `fake_event_generator.py` for:

1. A pyserial read loop over the UNO's telemetry JSON (see
   `shared/serial_protocol.md`), turned into Incidents via
   `shared.schemas.new_incident(...)`.
2. Whatever Incidents `/vision` sends over the same WebSocket connection.
3. Real fusion logic turning those into a Decision via
   `shared.schemas.new_decision(...)`.
4. A pyserial *write* of the resulting `W,...` / `C,...` / `I` command back
   to the UNO.

The `incident` / `severity_update` / `eta_update` / `monte_carlo_result`
broadcast shapes shouldn't need to change when you do this.
