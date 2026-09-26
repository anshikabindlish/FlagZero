# Phone car node: changes to the v2 plan (for Person A / server.py)

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
- [ ] `driver_request RED_FLAG` → severity 4, `red_pending = true`, "driver requested" source
- [ ] `green_flag` → same as dashboard `reset`
- [ ] Ping each phone at least once per second, and match `ack.id` for latency
- [ ] Accept `test: true` on `imu_event`. It can be treated like a real event, or flagged on the dashboard.
