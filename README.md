# FlagZero v2

Motorsport incident-detection demo: phones are car nodes, an overhead iPhone
camera watches a paper track, and one FastAPI server fuses signals, sets
severity, predicts who's approaching, and warns them before they arrive.

## Roles / hardware

| Who | Machine | Runs |
|---|---|---|
| A | — | `server.py`, `core/*` |
| B (vision) | MacBook Air #1 | `server.py`, `vision.py`, `camera_test.py`, cloudflared tunnel |
| — | iPhone #1 | Overhead camera via Continuity Camera |
| — | iPhone #2 | Car #17 (crash car) — Safari, `/car?car=17` |
| — | OnePlus 15R | Car #21 (approaching car) — Chrome, `/car?car=21` |
| C (presenter) | MacBook Air #2 | Backup host, `/dashboard` |
| — | ThinkPad (Windows) | Second dashboard, `/dashboard` |

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run — race control host (MacBook Air #1)

```bash
# Terminal 1: the server
uvicorn flagzero.server:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2: public tunnel so phones off the LAN can reach it
cloudflared tunnel --url http://localhost:8000

# Terminal 3: vision pipeline (after confirming the camera index — see below)
python -m flagzero.vision.vision --index <N>
```

## Run — find and test the overhead camera (MacBook Air #1)

```bash
# Scan indices 0-3, show a thumbnail per open camera
python -m flagzero.vision.camera_test

# Once you know the iPhone's index, preview it full-size / grab a still
python -m flagzero.vision.camera_test --index 1
```

## Run — car / dashboard pages (any phone or laptop)

Open in a browser, pointed at the tunnel URL or the host's LAN IP:

```
http://<host>:8000/car?car=17      # iPhone #2, car #17
http://<host>:8000/car?car=21      # OnePlus 15R, car #21
http://<host>:8000/dashboard       # MacBook Air #2, ThinkPad
http://<host>:8000/join            # anyone joining mid-demo
```

## Simulate without hardware

```bash
python -m flagzero.tools.mock_car --car 17
python -m flagzero.tools.mock_vision
python -m flagzero.tools.replay <recorded_run.json>
python -m flagzero.sim.montecarlo --n 10000
```

## Conventions

- All timestamps are milliseconds; the **server** stamps receive time — every
  latency figure downstream uses server time, not client clocks.
- Every tunable number lives in `flagzero/config.py` — no magic numbers
  elsewhere.
- Cross-platform: use `pathlib`, no OS-specific shell-outs.
- Warning levels (phones): `0 NORMAL, 1 CAUTION, 2 YELLOW, 3 DBL YELLOW,
  4 SLOW ZONE, 5 RED`.
- Severity levels (race control): `0-4`, where `4` = RED FLAG RECOMMENDED.
  RED only shows on phones after race control clicks **Confirm Red**.
