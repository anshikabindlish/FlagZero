# Phone car node: changes to the v2 plan (for Person A / server.py)

> **Status (integrate branch):** wired into A's `server.py`. Person C's changes to A's files, all small:
> - `core/engine.py` + `core/incidents.py`: handle `driver_request` (FALSE_ALARM → reset if below red; RED_FLAG → `DRIVER` source) and `green_flag` (= dashboard reset).
> - `core/severity.py`: a `DRIVER`/`RED_FLAG` source → severity 4 (RED FLAG RECOMMENDED, waits for Confirm red).
> - `core/router.py` + `config.py`: `ROUTER_FLAG_ZONE_M = 500` (inside it a car always sees the incident's flag), `SPEED_CAP_KMH[3] = 120` (cars slow for DBL YELLOW and drive past), and scene positions retuned for Interlagos.
> - `track.json` is now the real Interlagos (the generic circuit is kept as `track_generic.json`, and `tests/test_a1_sim.py` uses it for its generic-track checks).
> - **No special corner:** T4 is an ordinary corner now. There's no paper-track window in `track.json`, nothing highlighted on the dashboard, and scenes line car 21 up ~9 s behind car 17 wherever it is (`SCENE_CAR21_BEHIND_S`). Person B: the camera's stretch of track is now purely your vision calibration's choice.
> - **Driver OK = car rejoins:** after `ok_pressed` / `countdown_result OK`, the crashed car drives on under the current yellow/green flags (incident and flags stay until cleared). It stays stopped on TIMEOUT, on a red, or after `driver_request RED_FLAG`. The router only skips the crashed car while it's STOPPED.
> - New tests: `tests/test_c_phone_extras.py`. All 59 tests pass.
>
> The page paths are `/car.js` and `/dashboard.js` (server.py serves `/<name>.js` from `web/`), and the dashboard reads `state_message.md` fields directly. Section 1 and parts of section 5 below describe the earlier stand-in server and are kept for reference.

From Person C. The phone page follows the v2 build-prompts protocol exactly, **plus the additions below**. Everything here is already implemented on the phone side in `flagzero/web/car.html` + `car.js`. `flagzero/tools/mock_server.py` is a working reference for the server side of every item.

## 1. Serving the page

| Route | Serves |
|---|---|
| `GET /car?car=N` | `flagzero/web/car.html` |
| `GET /web/car.js` | `flagzero/web/car.js` |

`car.html` loads its script from `/web/car.js`. In FastAPI:

```python
app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")

@app.get("/car")
def car_page():
    return FileResponse(WEB_DIR / "car.html")
```

## 2. Unchanged from the v2 protocol

The phone uses these exactly as in the v2 build prompts. Every phone message also carries `"car": N`.

**Phone → server, on `/ws/car?car=N`:**
- `hello {platform}`
- `tel {g, gyro}` at 10 Hz
- `imu_event {cls: KERB|SPIN|IMPACT|SEVERE|ROLLOVER, peak_g, dur_ms, gyro_peak, rot_deg, conf, capture_ms}`
  - It can also carry `test: true` when sent from the on-screen "test crash" button.
- `imu_update {still: true}`, sent about 1.5 s after an event if the phone stays still
- `ok_pressed`
- `countdown_result {result: OK|TIMEOUT}`
- `ack {id}`: the phone acks **every `warning` and every `ping`**

**Server → phone:**
- `warning {id, level 0–5, label, corner, dist_m, eta_s}`
  - The phone displays by `level`.
  - `dist_m` / `eta_s` may be `null` (e.g. for the crashed car itself).
- `countdown {secs, peak_g}`
- `medical {status: MONITOR|URGENT}`
- `reset`
- `ping {id}`

## 3. Changes and additions

### 3.1 Countdown is 15 s, not 10
Put `COUNTDOWN_S = 15` in `config.py` and send `{"type":"countdown","secs":15,...}`. The phone uses the server's `secs`, falling back to 15 locally.

### 3.2 New phone → server message: `driver_request`
```json
{"type":"driver_request","car":17,"request":"FALSE_ALARM"}
{"type":"driver_request","car":17,"request":"RED_FLAG"}
```
- **`FALSE_ALARM`**: the driver pressed I'M OK and reports that it was only a wobble or loss of grip, not a crash.
  - The phone only offers it after OK, and only while that car's level is below 5.
  - **Server:** if the incident is below red, clear it exactly like a dashboard `reset`: clear incidents, stop countdowns, send `reset` to every phone. **Ignore it if the incident is already red.**
- **`RED_FLAG`**: the driver recommends a red flag, e.g. because they're OK but stopped at a dangerous spot.
  - If sent during the countdown, the phone also sends `ok_pressed` + `countdown_result OK` first, because the driver is responsive.
  - **Server:** raise the incident to **severity 4 / `red_pending = true`**, the same as a TIMEOUT, so the dashboard shows RED FLAG RECOMMENDED. Level 5 RED still only goes to phones after **Confirm red**. Show "driver requested" as a source chip on the dashboard.

### 3.2b No driver response = automatic red (change from the plan)
When a car sends `countdown_result TIMEOUT` (15 s with no I'M OK), the driver is unresponsive, which is a medical emergency. **Send level 5 RED to every car immediately. Don't wait for Confirm red.** Also set medical URGENT, add `NO_RESPONSE` as a source, and include `"red_active": true` in the dashboard `state` message so the dashboard shows "RED FLAG · AUTO (NO RESPONSE)". Race control can still clear it with Reset.
`driver_request RED_FLAG` from a responsive driver still only *recommends* red (`red_pending`) and needs Confirm red.

### 3.3 New phone → server message: `green_flag`
```json
{"type":"green_flag","car":21}
```
- A "RACE CONTROL · GREEN FLAG" button on the phones, shown whenever a flag is out, so testing and rehearsals don't wait on anything.
- **Server:** treat it exactly like the dashboard's `{"type":"reset"}`.
- Once the dashboard has a Reset button, we can hide the phone button for the real demo. In real racing, only race control ends a red flag.

### 3.4 No automatic reset
Flags stay out until the dashboard `reset`, a phone `green_flag`, or a driver `FALSE_ALARM` clears them.

### 3.5 Fail-safe behaviour on the phone (no server change needed, just be aware)
- **Local countdown:** after IMPACT, SEVERE or ROLLOVER, the phone starts the countdown itself if no `countdown` arrives within 500 ms. The server may still send one; the phone ignores a second one.
- **Offline queue:** while the socket is down, `imu_event`, `imu_update`, `ok_pressed`, `countdown_result`, `driver_request` and `green_flag` are queued and sent on reconnect, so the server may receive them late.
- **Link status:** the phone shows NO LINK if no message (including `ping`) arrives for 3 s. **Please ping each phone at least once a second.**

## 4. Summary checklist for server.py

- [ ] Serve `/car` and mount `/web`
- [ ] `COUNTDOWN_S = 15`
- [ ] `driver_request FALSE_ALARM` → reset everything (ignore if already red)
- [ ] `countdown_result TIMEOUT` → automatic level 5 RED to all cars, `red_active: true` (no Confirm red)
- [ ] `driver_request RED_FLAG` → severity 4, `red_pending = true`, "driver requested" source
- [ ] `green_flag` → same as dashboard `reset`
- [ ] Ping each phone at least once per second, and match `ack.id` for latency
- [ ] Accept `test: true` on `imu_event`. It can be treated like a real event, or flagged on the dashboard.

## 5. What the dashboard needs from server.py

`flagzero/web/dashboard.html` + `dashboard.js` (served at `GET /dashboard`, connects to `/ws/dash`). It reads the v2 `state` message and is tolerant of missing fields: a panel shows "—" if its field isn't there yet. The extra fields below make every panel work. `mock_server.py` sends all of them.

### 5.1 Extra fields on the 10 Hz `state` message
```json
{"type":"state",
 "cars":[{"car":21,"track_m":1213,"speed_kmh":190,"warning":2}],
 "incidents":[{"id":"inc-3","kind":"IMPACT","car":17,"corner":"T4","track_m":1423,
               "severity":3,"fused_conf":0.88,"sources":["IMU","CAMERA","NO_RESPONSE","DRIVER"],
               "medical":"MONITOR|URGENT|null","created_ms":0,"updated_ms":0,
               "approaching":[{"car":21,"dist_m":210,"speed_kmh":180,"eta_s":4.2,"warning":3}]}],
 "red_pending":false,
 "red_active":false,
 "drivers":{"17":{"hr":142,"spo2":97,"vitals":"SIM","response":"WAITING|OK|NO_RESPONSE|null",
                  "countdown_s":9,"medical":"MONITOR|URGENT|null"}},
 "latency":{"per_car":{"17":110,"21":95},"tunnel_rtt_ms":110,"last_detect_to_warn_ms":430}}
```
- `incidents[].approaching`: optional. Without it, the dashboard works out distance and ETA itself from `cars`.
- `sources` values that get coloured tags: `IMU`, `CAMERA`, `NO_RESPONSE`, `DRIVER`, `TEST`.
- `red_active`: a red flag is out, either automatic (no response) or confirmed. `red_pending`: waiting for Confirm red.
- `drivers`: keyed by car number as a string. SIM vitals: 70–80 bpm at rest, ramping to 135–145 over 5 s after an impact, SpO₂ 96–98.
- `latency.last_detect_to_warn_ms`: the dashboard records each new value as a trial and shows the median, p90 and max.

### 5.2 Forward phone telemetry to dashboards
For the live g-force trace, forward each phone's `tel` and `imu_event` to every `/ws/dash` client as-is, adding `car` and optionally `t_ms`:
```json
{"type":"tel","car":17,"g":0.21,"gyro":14}
{"type":"imu_event","car":17,"cls":"IMPACT","peak_g":5.2,"conf":0.84}
```

### 5.3 `GET /track.json`: the track is now the real Interlagos
`flagzero/track.json` is committed. It's the **real Interlagos layout** (Autódromo José Carlos Pace), exported from FastF1 with `flagzero/tools/export_track.py`, using the 2023 São Paulo GP qualifying pole-lap trace.
- **Lap:** 4,247 m (telemetry distance; the official figure is 4,309 m).
- **Corners:** 15 real corners with names, e.g. T4 Descida do Lago at 1,389 m.
- **Speeds:** a real speed profile (`speed_profile_kmh`, [lap_m, km/h] every 10 m) that the sim can use directly.
- **Demo corner:** still **T4**, now at **1,389 m** instead of 1,423 m.

**Person B:** the paper track now maps to `paper_window` = **[1339, 1439] m** instead of 1380–1480.
**Person A:** use `lap_length_m`, `corners`, `speed_profile_kmh` and `demo_corner` from this file instead of the generic 4,000 m circuit. `tools/mock_server.py` has a small reference race sim: race pace = 0.9 × the profile, and flags cap speed near the hazard (YELLOW 180, DBL YELLOW 120, SLOW ZONE 80 km/h from 500 m before to 100 m after; RED 80 km/h everywhere, queue at the pit entry). The crashed car stops where it is, and nobody teleports.

Field names:
Serve `flagzero/track.json` at `/track.json`. The dashboard accepts
`{"lap_length_m", "points":[{x,y,d}] or [[x,y,d]], "corners":[{"name","pos_m","sightline_m","blind"}], "paper_window":[1380,1480]}`
(`d` = lap distance; it's computed from the polyline if missing, and several alternative field names work too). Without the file it draws a built-in 4,000 m layout with T4 at 1423 m.

### 5.4 Checklist
- [ ] `GET /dashboard` serves `web/dashboard.html`
- [ ] `state` includes `drivers`, `red_active`, `latency.per_car` (and optionally `incidents[].approaching`)
- [ ] Forward `tel` + `imu_event` from phones to dashboards
- [ ] `GET /track.json`
