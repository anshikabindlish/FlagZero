# /shared — contracts everyone imports

Nobody hardcodes event names, label strings, zone positions, or WebSocket
message shapes in their own module — import them from here instead, so a
change in one place propagates everywhere.

| File | What it's for | Who imports it |
|---|---|---|
| `track_config.json` / `track_config.py` | Lap length + named zone positions (e.g. `T4` → 1423 m) | `/core`, `/vision` |
| `schemas.py` | Incident + Decision builders/validators, WS envelope helper, shared enums | `/core`, `/vision` |
| `incident.schema.json` | Formal JSON Schema for Incident, for docs/tooling/non-Python consumers | everyone (reference) |
| `decision.schema.json` | Formal JSON Schema for Decision | everyone (reference) |
| `ws_messages.md` | The 4 WebSocket message shapes, with examples | `/core`, `/dashboard` |
| `serial_protocol.md` | Full Laptop⟷UNO command grammar and telemetry JSON shape | `/core`, `/hardware` |

**Needs your confirmation before H1** (see root README for the full list):
default track length, the severity↔label mapping, and the `car_id`/scene
handling approach.
