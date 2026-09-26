# FlagZero — vision (Person B)

The overhead iPhone watches the paper track and sends `/ws/vision` messages at 10 Hz.
It no longer just waits for a car to sit still: it tracks every car's **speed, heading,
yaw rate and position in the corridor** and raises *predicted* hazards (spin, heading off
track, closing on a stopped car) before anything has come to rest.

```
flagzero/
  vision/
    vision.py          main loop: camera/file -> pipeline -> /ws/vision (+ preview, record, metrics)
    calibrate.py       click the centre line, set corridor width, tune HSV -> vision_calib.json
    camera_test.py     find the Continuity Camera index
    markers.py         printable ArUco car markers (A4)
    vision_config.py   EVERY threshold lives here
    calib.py  detector.py  tracker.py  hazards.py  scene.py  pipeline.py  sender.py
  tools/
    make_test_video.py synthetic paper-track clip + calibration (test with no camera)
    mock_vision.py     keyboard-driven fake camera (s/d/m/p/o/l/h/c)
    replay.py          replay a recording (JSONL or video) - the demo fallback
  tests/test_vision.py
```

## 0. Install (Mac, Windows or Linux)

```bash
pip install -r requirements.txt             # repo root: opencv-python>=4.7 (has ArUco), numpy, websockets
```

## 1. Five-minute test, no camera needed

```bash
python flagzero/tools/make_test_video.py          # ~45 s, writes recordings/synthetic.mp4 + calib
start_demo.bat --no-tunnel                        # terminal 1: the real server (Mac: ./start_demo.command --no-tunnel)
python flagzero/vision/vision.py --source flagzero/recordings/synthetic.mp4 \
       --calib flagzero/recordings/synthetic_calib.json               # terminal 2
```

Expected, in the preview window and on the dashboard (http://localhost:8000/dashboard):

| t (s) | event | what happened in the clip |
|---|---|---|
| 5.8 | **PREDICTED SPIN_RISK** #17 @1420 m | car 17 starts rotating (spin begins 5.5) |
| 7.6 | **PREDICTED CLOSING** #8 → #17, TTC 0.7 s | car 8 charges at the stopped car |
| 8.3 | STOPPED_VEHICLE #17 @1423 m, `backwards: true` | 2 s after it came to rest |
| 10.3 | STOPPED_VEHICLE #8 + MULTI_STOP [8, 17] | |
| 10.4–11.5 | `occluded: true` | a hand sweeps in; timers pause |
| 13.6 | DEBRIS @1454 m | 2 s after the hand left |

The spin is flagged **2.5 s before** the stopped-car detection. That's the head start to show.

`python -m pytest tests/test_vision.py -q` runs 18 tests in ~2 s (`FZ_SLOW=1` adds the full clip).

## 2. Venue setup

1. **Print markers:** `python flagzero/vision/markers.py` → `vision/markers/car_markers_A4.png`.
   Print at 100 %. Tape #17 on the pink note and #8 on the blue one. **The arrow is the nose**:
   always point it in the driving direction. No printer? Sticky notes still work (colour mode),
   and a dark marker dot near the front edge gives them a heading.
2. **Paper track:** the paper represents `TRACK_START_M`–`TRACK_END_M` of the Interlagos lap
   (default 1380–1480 m; no corner is special, so pick any stretch in `vision_config.py`).
   Calibration maps the drawn centre line linearly onto it; the 1423 m mark is drawn for reference.
3. **Mount iPhone #1** overhead, landscape, locked, not moving. Even light, no glare on the markers.
4. `python flagzero/vision/camera_test.py`: note the index that shows the paper (usually the 1920×1080 one).
5. `python flagzero/vision/calibrate.py --index N`
   - click ~10 points along the centre line **in driving order**
   - `[` `]` until the green band covers the track
   - `k` confirms the ArUco markers are readable
   - `h` for HSV sliders, only if using sticky notes
   - `s` to save (it prints the reprojection error and where the 1423 m mark landed), `q` to quit
6. Run with A's server up: `python flagzero/vision/vision.py --index N`.
   Add `--no-preview` for the demo and `--record` to save video plus a JSONL log.
   Or start everything at once: `py -m flagzero.tools.start_demo --vision N` (server + tunnel + camera).

Keys in the preview window: `r` reset (re-arms cars, retakes the debris reference; **press before each scene**), `b` retake the debris reference only, `s` save a still, `q` quit.

## 3. What gets sent (additions to flagzero/docs/protocol.md)

All v2 fields are unchanged. Extra fields are additive, so A's server can ignore what it doesn't use.

```json
{"type":"vision","camera":1,"t_ms":1790458549844,"occluded":false,
 "cars":[{"car":17,"track_m":1423.0,"speed_px_s":0.0,"stationary_s":3.1,
          "speed_kmh":0.0,"heading_err_deg":179.6,"yaw_rate_dps":0.0,
          "lateral":0.30,"off_track":false,"risk":0.97,"visible":true,"src":"aruco"}],
 "hazards":[
   {"kind":"SPIN_RISK","car":17,"track_m":1420.5,"conf":0.90,"predicted":true,
    "detail":{"yaw_rate_dps":505,"heading_err_deg":94}},
   {"kind":"CLOSING","car":8,"track_m":1423.0,"conf":0.68,"predicted":true,
    "detail":{"ahead":17,"ttc_s":0.73,"gap_m":20.5}},
   {"kind":"STOPPED_VEHICLE","car":17,"track_m":1423.0,"conf":0.93,"predicted":false,
    "detail":{"stationary_s":3.1,"backwards":true,"off_track":false}},
   {"kind":"DEBRIS","track_m":1453.8,"conf":0.83,"predicted":false,"detail":{"age_s":2.4}}]}
```

| kind | predicted? | raised when | typical conf |
|---|---|---|---|
| `SPIN_RISK` | yes | yaw > 120°/s, or nose > 40° off the track direction while moving | 0.8–0.9 |
| `OFF_TRACK` | yes / no | lateral drift will cross the edge within 0.5 s (predicted), or already outside | 0.58 / 0.75+ |
| `SLOWING` | yes | deceleration > 250 km/h per s from > 60 km/h | 0.5 |
| `CLOSING` | yes | follower reaches the car ahead in < 1 s (gap ≤ 25 m) | 0.6–0.9 |
| `STOPPED_VEHICLE` | no | still (< 3 px/1 s) for 2 s, **after the car has moved** (parked cars ignored) | 0.85–0.97 |
| `DEBRIS` | no | non-car blob in the corridor for 2 s | 0.75–0.9 |
| `MULTI_STOP` | no | ≥ 2 stopped cars within 30 m and 5 s (`MULTI_STOP_ENABLED`) | 0.9 |

**How the server uses them (implemented in `core/incidents.py` and `core/severity.py`):**
- `SPIN_RISK`, `CLOSING` and a real `OFF_TRACK` → severity 2 (YELLOW) straight away. That's the predictive flag.
- `SLOWING` and predicted `OFF_TRACK` have conf < 0.6, so they are severity 1 under the existing rule.
- Severity 4 corroboration counts only `STOPPED_VEHICLE` / `MULTI_STOP` (both `predicted: false`).
- Each camera source keeps `predicted`, and the dashboard marks it on the source chip.
- `SPIN_RISK car 17` followed by `STOPPED_VEHICLE car 17` and the phone's `IMPACT` is the same car, so they merge into one incident that escalates.
- Only `STOPPED_VEHICLE` stops the car in the sim; the camera's `cars[].track_m` overrides the sim position while the car is visible.

Checked end to end on the synthetic clip against `server.py`: car 21's phone went YELLOW on the predicted
`SPIN_RISK` 2.5 s before `STOPPED_VEHICLE`, then DOUBLE YELLOW on `MULTI_STOP`, then back to green once it passed.

## 4. Demo tips

- **Move cars with a magnet under the paper**, or a stick. A hand in frame pauses the stop and debris timers on purpose (so a car counts as stopped only after the hand leaves). Predictive hazards keep working as long as the marker is visible.
- Scene 2 line: slide #17 along the paper, twist it hard. **PREDICTED SPIN_RISK** appears and car 21 goes yellow *while #17 is still moving*. Then it stops and the phone drop plus escalation follow as planned.
- Speeds are true paper scale (a hand-speed slide is ~20–60 km/h). To show race-like numbers on screen, raise `SPEED_DISPLAY_SCALE`; the thresholds use the same scaled km/h, so re-check `MOVING_KMH` and `SLOWING_*` afterwards.

## 5. Fallbacks and proof

```bash
python flagzero/vision/vision.py --index N --record                # rehearsal: saves recordings/<ts>.mp4 + .jsonl
python flagzero/tools/replay.py --jsonl flagzero/recordings/<ts>.jsonl   # exact messages, original timing
python flagzero/tools/replay.py --video flagzero/recordings/<ts>.mp4     # re-run the whole pipeline on it
python flagzero/tools/mock_vision.py [--scene 2]                    # keyboard fake camera for A and C
python flagzero/vision/vision.py --index N --metrics debris         # p = placed, n = miss; 10 trials
python flagzero/vision/vision.py --index N --metrics stopped        # -> results/vision_metrics.csv
```

Point any of these at the tunnel with `--url wss://<name>.trycloudflare.com/ws/vision`.

## 6. Tuning cheatsheet (`vision_config.py`)

| symptom | change |
|---|---|
| markers not found | bigger print (`--size-cm 4`), matte paper, more light, camera lower |
| spin fires on normal driving | raise `SPIN_YAW_DPS` / `SPIN_HEADING_ERR_DEG` |
| spin fires too late | lower `SPIN_CONFIRM_S`, `SPIN_YAW_DPS` |
| "stopped" while held still by hand | fine, that's the hand pause; else raise `STILL_PX` |
| debris from shadows/lighting | raise `DEBRIS_DIFF_THRESHOLD`, press `b` after lights change |
| crumpled paper not seen | lower `DEBRIS_DIFF_THRESHOLD` (white on white is low contrast); grey or lined paper helps |
| hand not detected | lower `HAND_MIN_AREA_FRAC` |
