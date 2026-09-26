# Changes to the v2 plan: phones, dashboard, track (Person C)

What Person C added or changed on top of the v2 build prompts, and where it lives.
Message shapes are in `protocol.md` and the dashboard data in `state_message.md`;
this file is the "why" and the list of edits to Person A's code.
All 87 tests pass: 86 by default (`python -m pytest -q`), plus B's full-clip test with `FZ_SLOW=1`.

## 1. Team decisions (rules)

| Rule | Where it's enforced |
|---|---|
| **Only race control returns a flag to green** (dashboard Reset / Scene). Drivers can escalate and inform, never clear. | `engine.on_car`: phone `green_flag` is ignored; `driver_request FALSE_ALARM` is only a report |
| **No response in 15 s = automatic RED** (medical emergency); a *recommended* red still waits for Confirm red | `config.AUTO_RED_ON_TIMEOUT`, `COUNTDOWN_S = 15`, `severity.py` |
| **Driver OK = the crashed car rejoins** and drives on under the current yellow/green flags; it stays stopped on red, on no response, or after asking for red | `engine._resume_if_ok`, `router.compute` (only a *stopped* crashed car is skipped) |
| **Cars behave like a race**: the crashed car stops where it is (no teleport); cars slow to 120 km/h under DBL YELLOW and drive past; RED caps everyone | `config.SPEED_CAP_KMH = {3: 120, 4: 80, 5: 60}`, `ROUTER_FLAG_ZONE_M = 500` |
| **Real Interlagos, no special corner** | `track.json` (FastF1 export), scenes line car 21 up ~9 s behind car 17 wherever it is |

## 2. Phone car node (`web/car.html`, `web/car.js`)

- v2 protocol on `/ws/car?car=N`: `hello`, `tel` (10 Hz), `imu_event` (`cls` KERB/SPIN/IMPACT/SEVERE/ROLLOVER), `imu_update`, `ok_pressed`, `countdown_result`, and it acks every `warning` and `ping`.
- On-device detection state machine: IDLE → CAPTURE (300 ms) → classify and send → POST (1.5 s stillness → `imu_update`). Thresholds are in `CFG` at the top of `car.js`.
- Full-screen flag display by `level` (0–5), with the distance counting down, a beep pattern per level, and vibration on Android.
- 15 s I'M OK countdown, which starts locally if the server's `countdown` hasn't arrived within 500 ms (fail-safe).
- Driver buttons: **I'M OK**, **RECOMMEND RED FLAG** (`driver_request RED_FLAG`), **REPORT FALSE ALARM** (`driver_request FALSE_ALARM`, report only), and **CONTINUE UNDER YELLOW** (closes the panel). There is no green-flag button.
- NO LINK after 3 s without a message; important messages are queued offline and flushed on reconnect.

## 3. Race-control dashboard (`web/dashboard.html`, `web/dashboard.js`)

Reads `state_message.md` fields directly. `/dashboard?mock=1` runs on built-in fake data.
1. Active incident: kind, corner and name, source chips with confidence, fused %, medical, and **"Driver says"** (recommends red / reports a false alarm).
2. Approaching cars sorted by ETA, with each car's actual flag.
3. Driver status: SIM vitals, I'm-OK response and countdown, medical.
4. RED FLAG RECOMMENDED banner with **Confirm red**, a RED FLAG OUT bar (automatic or confirmed), and Reset / Scene buttons.
5. Track map from `/api/track`: real Interlagos, cars coloured by their flag, hazard marker.
6. Live g-force trace of the incident car (from `latency.tel`).
7. Latency: detect → warning per trial with median/p90/max, and per-phone RTT.
8. **Proof panel:**
   - "This incident" counterfactual (`/api/montecarlo/incident`)
   - marshal-vs-FlagZero timeline
   - sliders that re-run `/api/montecarlo`
   - results table with a `/api/montecarlo/last` fallback
   - secondary impacts by following-gap band

`web/join.html`: QR codes for car 17, car 21 and the dashboard (`?base=https://<tunnel>`).

## 4. Edits to Person A's code

| File | Change |
|---|---|
| `core/engine.py` | `driver_request` (RED_FLAG → stop the car + DRIVER source; FALSE_ALARM → report); phone `green_flag` ignored; `_resume_if_ok` (car rejoins after OK); `position_scene` = car 21 ~9 s behind car 17 (no fixed corner); `_snapshot_incidents` / `incident_snapshot` (approaching cars at detection + this incident's measured detect→warn) |
| `core/incidents.py` | `handle_driver_request` |
| `core/severity.py` | a `DRIVER`/`RED_FLAG` source → severity 4 (waits for Confirm red) |
| `core/router.py` | 500 m flag zone (a car always sees the incident's flag inside it; stops YELLOW/DBL-YELLOW flip-flopping under the speed cap); only a *stopped* crashed car is skipped |
| `config.py` | `ROUTER_FLAG_ZONE_M`, `SPEED_CAP_KMH[3] = 120`, `SCENE_CAR21_BEHIND_S`, `MC_GAP_S = (1, 10)` |
| `sim/montecarlo.py` | `incident()` counterfactual for one real incident (same physics as `run()`) |
| `server.py` | `GET /api/montecarlo/incident` |
| `track.json` | real Interlagos from FastF1; the generic circuit is kept as `track_generic.json` for the generic-track tests |
| `sim/sim.py`, `tools/scene_check.py` | use car 17's real position instead of the old T4 at 1423 m |
| `core/incidents.py`, `core/severity.py` (vision) | B's predictive camera kinds accepted (`SPIN_RISK`, `CLOSING`, `OFF_TRACK`, `SLOWING`); `SPIN_RISK`/`CLOSING`/real `OFF_TRACK` → severity 2; camera sources keep `predicted` |
| `tests/` | `test_a1_sim.py` uses `track_generic.json` for its generic-track checks, plus `test_interlagos_track`; `test_a2_a6.py` scenes use real positions; new `test_c_phone_extras.py` |

### Why `MC_GAP_S` changed from (0.8, 3) to (1, 10)
With the next car only 0.8–3 s behind, it is already inside its own stopping distance in ~94% of runs, so no warning system can help and both columns read ~93%. 1–10 s is a realistic spread for 20 cars on a ~80 s lap:
- secondary impacts **55% → 26%**
- median time to warn **4.6 s → 0.49 s**
- speed at the hazard **92 → 43 km/h**

The dashboard's per-gap-band chart keeps the "too close for any system" band (under ~2 s) visible, so the claim stays honest.

## 5. Tools

| Tool | What it does |
|---|---|
| `start_demo.bat` / `start_demo.command` → `tools/start_demo.py` | Starts the server and a cloudflared tunnel, waits (via DNS-over-HTTPS) until the tunnel resolves publicly, then opens the dashboard and join page via localhost. Opening a new tunnel name too early makes the PC and Wi-Fi resolvers cache NXDOMAIN. |
| `tools/mock_car.py` | Keyboard fake phone |
| `tools/export_track.py` | Regenerates `track.json` from FastF1 (needs `pip install fastf1`) |

## 6. Person B (vision): integrated

B's `vision` branch was merged into `flagzero/`:
- `flagzero/vision/`: the pipeline, calibration, markers and config
- `flagzero/tools/`: `make_test_video`, `mock_vision`, `replay` (B's `vision_listen` stand-in server was dropped: the real server replaces it)
- `tests/test_vision.py`: 19 tests
- `flagzero/recordings/`: the synthetic clip's calibration and ground truth

Changes made while integrating:
- `T4_M` → `PAPER_MARK_M` (no special corner), and the preview/calibration labels changed to match.
- `LAP_LEN_M` is read from `track.json` (4,247 m).
- The test's import path now points at the repo root.
- `start_demo --vision N` starts the camera together with the server and tunnel.

The paper still represents 1380–1480 m (`TRACK_START_M`/`TRACK_END_M` in `vision_config.py`).
