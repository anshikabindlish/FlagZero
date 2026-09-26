# FlagZero v2 Protocol

Every WebSocket message is a single JSON object with a `type` field. This
file is the one source of truth for message shapes -- Person A (server),
Person B (vision), and Person C (phones + dashboard) all build against this
doc, not against each other's code.

Server: `flagzero/server.py`, FastAPI + uvicorn, port 8000.
Endpoints:
- `ws://<host>:8000/ws/car?car=N` -- one connection per phone
- `ws://<host>:8000/ws/vision` -- the overhead-camera pipeline
- `ws://<host>:8000/ws/dash` -- one or more dashboards

All timestamps are milliseconds. The server stamps its own receive time on
everything; every latency figure is computed using server time, not the
sender's clock (phone clocks aren't trustworthy/synced).

Warning levels: `0 NORMAL, 1 CAUTION, 2 YELLOW, 3 DBL_YELLOW, 4 SLOW_ZONE, 5 RED`
Severity levels: `0-4` (4 = RED FLAG RECOMMENDED; level 5 / RED only ever
reaches a phone after race control clicks "Confirm red" on the dashboard).

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
Sent once the 10-second countdown resolves, either way.

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
{"type": "countdown", "secs": 10, "peak_g": 5.2}
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

## Vision -> server (`/ws/vision`, 10 Hz)

```json
{"type": "vision", "camera": 1,
 "cars": [{"car": 17, "track_m": 1423.4, "speed_px_s": 0.0, "stationary_s": 3.1}],
 "hazards": [{"kind": "STOPPED_VEHICLE|DEBRIS|MULTI_STOP", "car": 17,
              "track_m": 1423, "conf": 0.93}],
 "occluded": false}
```
`cars` reports every tracked car's position on track. `hazards` is the
current list of active hazards (present only while true, dropped once
cleared -- not a one-off event). `car` is omitted on a `DEBRIS` hazard
(debris isn't tied to a specific car). `occluded: true` means a hand/object
is blocking the view (e.g. someone placing debris) -- the server should NOT
treat car positions as reliable while this is true.

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
| `kind` | string | The hazard/event kind that created it (`IMPACT`, `STOPPED_VEHICLE`, `DEBRIS`, etc). |
| `sources` | list[string] | Which inputs corroborate this incident, e.g. `["imu", "camera"]`. |
| `car` | int or null | The car involved, if any (null for track-level hazards like debris). |
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
| `GET /join` | `web/join.html` (QR codes to the two car pages) |
| `GET /api/montecarlo?n=10000&...` | Monte Carlo results, computed live (see A5) |
