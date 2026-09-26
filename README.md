# FlagZero

Motorsport safety AI demo — an overhead camera + an IMU-and-vitals-carrying
Arduino watch a paper "track," `/core` fuses what they see into a flag
decision, `/dashboard` shows it, and the Arduino displays it trackside.

## Layout

```
flagzero/
├── shared/      contracts everyone imports — schemas, track config, protocols
├── core/        Person A — fusion, serial link, WebSocket server
├── vision/      Person B — OpenCV detection over the Continuity Camera feed
├── hardware/    Person C (1/2) — Arduino UNO sketch
└── dashboard/   Person C (2/2) — browser UI
```

Each folder has its own README with details; the table below is the fast
path to get all four stubs running side by side.

## Run everything (stub versions)

| Module | Command |
|---|---|
| core | `pip install -r core/requirements.txt && python -m core.fake_event_generator` |
| vision | `pip install -r vision/requirements.txt && python -m vision.stub_detector` |
| hardware | open `hardware/flagzero_stub/flagzero_stub.ino` in the Arduino IDE, select **Arduino Uno**, upload |
| dashboard | open `dashboard/index.html` in a browser while core is running |

Run the Python modules from the **repo root**, not from inside their own
folder — `shared` needs to resolve as a sibling package (that's what the
`python -m core.fake_event_generator` form buys you over
`python core/fake_event_generator.py`).

Start core first, then dashboard (it auto-reconnects every 2s if it can't
connect yet, so order isn't critical, just convenient).

## Needs your confirmation before H1

1. **Track length** — defaulted to **5000 m** in `shared/track_config.json`
   (a round number; T4 at 1423 m sits about 28.5% of the way around the
   lap). Edit that one file if you want a different length — nothing else
   in the repo hardcodes it.
2. **Severity ↔ label mapping** — the brief listed 6 labels
   (`NORMAL … RED_FLAG_RECOMMENDED` plus `SLOW_ZONE`) against 5 severity
   rungs (0–4). I mapped 5 of them onto the rungs 1:1 and treated
   `SLOW_ZONE` as an orthogonal/local state rather than a rung of its own —
   see the comment above `SEVERITY_LABELS` in `shared/schemas.py`. Say the
   word if you want a different mapping and I'll adjust it there.
3. **`car_id` when one Arduino plays multiple cars** — the UNO's telemetry
   JSON never includes `car_id`; `/core` is meant to tag it per
   `core/scene_config.json` instead, so switching which car a scene
   represents is a laptop-side edit, not a re-flash. Flag it if you'd
   rather the firmware carry `car_id` itself.
4. **LCD wiring** — `hardware/PINOUT.md` assumes a 1602A on an I2C backpack
   (2 wires) rather than parallel HD44780 (6+ digital pins), since that's
   what's in most starter kits. If yours is parallel, say so and I'll redo
   the pinout and the sketch's LCD calls.
5. **Baud rate** — 115200, arbitrary but must match on both ends; change
   freely as long as the UNO sketch and whatever reads it on the laptop
   agree.

## Contracts at a glance

- **Incident** (`shared/incident.schema.json`) — one raw detection, IMU or
  camera-origin.
- **Decision** (`shared/decision.schema.json`) — the fused flag state.
- **WebSocket messages** (`shared/ws_messages.md`) — `incident`,
  `severity_update`, `eta_update`, `monte_carlo_result`, all core⟷dashboard.
- **Serial protocol** (`shared/serial_protocol.md`) — short text commands
  laptop→UNO, small JSON UNO→laptop.
