# Dashboard data (for Person C)

Everything the dashboard needs arrives on `/ws/dash` as a `"state"` message 10 times a second.

## State message

```json
{"type": "state", "server_ms": 1790000000000,
 "cars": [{"car": 21, "track_m": 671.3, "speed_kmh": 169.6, "state": "RUNNING",
           "phone": true, "warning": 2}],
 "incidents": [{
   "id": 1, "kind": "IMPACT", "track_m": 1423.0, "corner": "T4", "car": 17,
   "severity": 4, "label": "RED_FLAG_RECOMMENDED", "fused_conf": 0.9916,
   "sources": [{"src": "IMU", "kind": "IMPACT", "conf": 0.88, "peak_g": 5.2, "ms": 0},
               {"src": "CAMERA", "kind": "STOPPED_VEHICLE", "conf": 0.93, "ms": 0},
               {"src": "NO_RESPONSE", "kind": "TIMEOUT", "conf": null, "ms": 0}],
   "still": true, "countdown": "TIMEOUT", "countdown_left_s": null, "medical": "URGENT",
   "approaching": [{"car": 21, "dist_m": 544.9, "speed_kmh": 204.4, "eta_s": 9.6, "warning": 2}]}],
 "vitals": {"17": {"hr": 108, "spo2": 96.3, "sim": true, "response": "NO_RESPONSE", "medical": "URGENT"}},
 "latency": {"tunnel_rtt_ms": 110, "rtt_car17_ms": 95, "rtt_car21_ms": 110,
             "last_detect_to_warn_ms": 430, "last_detect_to_sent_ms": 41,
             "tel": {"17": {"g": 1.03, "gyro": 14, "ms": 0}}},
 "red_pending": false, "red_confirmed": true, "red_auto": true, "phones_connected": [17, 21]}
```

| Panel | Where the data is |
| --- | --- |
| 1 Active incident | `incidents[0]`: `kind`, `corner`, `track_m`, `label`, `fused_conf`, `sources`, `car` |
| 2 Approaching cars | `incidents[0].approaching` (already sorted by ETA) |
| 3 Driver status | `vitals["17"]` (always show a SIM badge), `incidents[0].countdown_left_s` (15 -> 0) |
| 4 Red flag banner | `red_confirmed` = RED is out. `red_auto: true` means it went out automatically because the driver did not press OK within 15 s. `red_pending` = severity 4 without a timeout, waiting for a manual Confirm red (`{"type":"confirm_red"}`) |
| 5 Track map | shape from `GET /api/track` (`polyline` x/y, `corners`, `paper_track`), dots from `cars` |
| 6 g-force trace | `latency.tel["17"].g` |
| 7 Latency | `latency.tunnel_rtt_ms`, `latency.last_detect_to_warn_ms` |
| 8 Timeline + sliders | `GET /api/montecarlo?n=10000&visibility=0.5&marshal_react_max=2.5&speed_min=150&speed_max=250&sightline_min=50&sightline_max=250` → `timeline` |
| 9 Results table | same call → `baseline` and `flagzero`; fallback `GET /api/montecarlo/last` |

Warning levels on `cars[].warning`: 0 NORMAL, 1 CAUTION, 2 YELLOW, 3 DBL YELLOW, 4 SLOW ZONE, 5 RED.

## Dashboard → server

`{"type":"confirm_red"}`, `{"type":"reset"}`, and `{"type":"scene","n":1}` or `{"type":"scene","n":2}`. A scene message resets everything and puts car 21 about 700 m before T4, so its warning counts down live.
