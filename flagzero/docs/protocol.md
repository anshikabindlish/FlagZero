# FlagZero v2 Protocol

Every WebSocket message is a single JSON object with a `type` field. This
file is the one source of truth for message shapes -- Person A (server) and
Person C (phones, warning lights, dashboard) build against this doc, not
against each other's code.

Server: `flagzero/server.py`, FastAPI + uvicorn, port 8000.
Endpoints:
- `ws://<host>:8000/ws/car?car=N` -- one connection per phone
- `ws://<host>:8000/ws/dash` -- one or more dashboards

All timestamps are milliseconds. The server stamps its own receive time on
everything; every latency figure is computed using server time, not the
sender's clock (phone clocks aren't trustworthy/synced).

Warning levels: `0 NORMAL, 1 CAUTION, 2 YELLOW, 3 DBL_YELLOW, 4 SLOW_ZONE, 5 RED`
Severity levels: `0-4` (4 = RED FLAG RECOMMENDED). Level 5 / RED reaches the
phones either when race control clicks "Confirm red" on the dashboard, or
automatically when a crashed driver doesn't press I'm OK within 15 s
(`AUTO_RED_ON_TIMEOUT`).

**Only race control returns a flag to green** (dashboard `reset` / `scene`).
Drivers can escalate (I'm OK, recommend red) and inform (report a false
alarm), but no phone message clears a flag.

---

## Phone -> server (`/ws/car?car=17`)

```json
{"type": "hello", "car": 17, "platform": "ios|android"}
```
Sent once, right after the socket opens.

```json
{"type": "tel", "car": 17, "g": 1.03, "gyro": 14}
```
Telemetry, sent continuously at 10 Hz. `g` = dynamic acceleration magnitude
(gravity removed), `gyro` = rotation rate magnitude, deg/s.

```json
{"type": "imu_event", "car": 17, "cls": "IMPACT", "peak_g": 5.2, "dur_ms": 40,
 "gyro_peak": 380, "rot_deg": 35, "conf": 0.88, "capture_ms": 300}
```
One-off, sent the instant the phone's on-device state machine classifies a
motion event. `cls` is one of `KERB | SPIN | IMPACT | SEVERE | ROLLOVER`.

```json
{"type": "imu_update", "car": 17, "still": true}
```
Sent during the POST window after an event, if the phone has stayed still.

```json
{"type": "ok_pressed", "car": 17}
```
Driver pressed the big "I'm OK" button during a countdown.

```json
{"type": "countdown_result", "car": 17, "result": "OK|TIMEOUT"}
```
Sent once the 15-second countdown resolves, either way. After `OK` (and no red
out or recommended for this incident) the crashed car rejoins and drives on
under the current flags; after `TIMEOUT` it stays stopped and RED goes out
automatically.

```json
{"type": "driver_request", "car": 17, "request": "RED_FLAG|FALSE_ALARM"}
```
Buttons on the crashed car's phone after an impact. `RED_FLAG`: the driver
recommends a red (e.g. stopped somewhere dangerous) -> a `DRIVER`/`RED_FLAG`
source, severity 4, `red_pending` until race control confirms; the car stays
stopped. `FALSE_ALARM`: the driver *reports* it was only a wobble -> a
`DRIVER`/`FALSE_ALARM` source shown on the dashboard; flags stay out until race
control resets. Neither adds to `fused_conf`.

```json
{"type": "green_flag", "car": 21}
```
Ignored (logged). Kept only so an old phone page can't clear flags: only race
control can return to green.

```json
{"type": "ack", "id": 123}
```
Acknowledges receipt of a server message that carried that `id` (currently
just `warning` and `ping`). Used to measure round-trip delivery time.

## Server -> phone

```json
{"type": "warning", "id": 123, "level": 2, "label": "YELLOW", "corner": "T4",
 "dist_m": 210, "eta_s": 4.2}
```
Sent when this car's warning level changes, and every 250ms while a warning
is active (so `dist_m`/`eta_s` count down live on screen).

```json
{"type": "countdown", "secs": 15, "peak_g": 5.2}
```
Tells this phone to show the "PRESS OK" countdown, triggered by an
IMPACT-or-worse `imu_event` from this same car.

```json
{"type": "medical", "status": "MONITOR|URGENT"}
```

```json
{"type": "reset"}
```
Clear all local state, return to idle/green.

```json
{"type": "ping", "id": 124}
```
Heartbeat; phone should reply with `{"type":"ack","id":124}`. Used both for
tunnel-RTT measurement and for the phone's own "no ping in 3s -> NO LINK"
detection.

---

## Server -> dashboard (`/ws/dash`, 10 Hz)

```json
{"type": "state",
 "cars": [{"car": 21, "track_m": 1213, "speed_kmh": 190, "warning": 2}],
 "incidents": [ /* Incident objects, see below */ ],
 "latency": {"tunnel_rtt_ms": 110, "last_detect_to_warn_ms": 430},
 "red_pending": true}
```
Broadcast to every connected dashboard, whether or not anything changed
(keeps dashboards trivially in sync -- they never need to diff).

## Dashboard -> server

```json
{"type": "confirm_red"}
```
Race control confirms the red flag; every approaching phone gets level 5 RED.

```json
{"type": "reset"}
```
Clears all incidents, stops countdowns, sends `{"type":"reset"}` to every phone.

```json
{"type": "scene", "n": 1}
```
Pre-positions sim cars for demo Scene 1 or Scene 2 (`n` is `1` or `2`).

---

## The Incident object

Appears inside `state.incidents[]`, and is what fusion/severity/the router
all operate on internally.

| Field | Type | Meaning |
|---|---|---|
| `id` | int | Unique, assigned on creation. |
| `kind` | string | The headline event kind (`ROLLOVER`, `SEVERE`, `IMPACT`, `SPIN`, `KERB`). |
| `sources` | list[object] | Corroborating inputs, each `{"src", "kind", "conf", "ms"}`: `src` is `IMU`, `NO_RESPONSE` or `DRIVER` (`conf` is null for the last two). |
| `car` | int or null | The car involved. |
| `track_m` | float | Position along the lap, metres. |
| `corner` | string | Nearest named corner code, e.g. `"T4"`. |
| `severity` | int | 0-4, current fused severity. |
| `fused_conf` | float | 0.0-1.0, noisy-OR fused confidence across sources. |
| `medical` | string or null | `null`, `"MONITOR"`, or `"URGENT"`. |
| `created_ms` | int | Server time the incident was first created. |
| `updated_ms` | int | Server time it was last updated by a new corroborating signal. |

---

## HTTP routes (non-WebSocket)

| Route | Serves |
|---|---|
| `GET /car?car=N` | `web/car.html` (phone page; JS reads `car` from the query string) |
| `GET /dashboard` | `web/dashboard.html` |
| `GET /join` | `web/join.html` (QR codes; `?base=https://<tunnel>` points them at the tunnel) |
| `GET /<name>.js` | page scripts from `web/` (`car.js`, `dashboard.js`) |
| `GET /api/track` | `track.json` (real Interlagos) |
| `GET /api/state` | the current `state` message as JSON |
| `GET /api/montecarlo?n=10000&...` | Monte Carlo results, computed live (see A5) |
| `GET /api/montecarlo/last` | the last saved run (the dashboard's fallback) |
| `GET /api/montecarlo/incident?id=` | marshal vs FlagZero replay of the cars that were really approaching one incident (latest if no id) |
| `GET /api/latency` | latency stats |
