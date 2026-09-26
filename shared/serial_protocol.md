# Serial protocol — Laptop ⟷ Arduino UNO

The UNO has 2KB of RAM total, so:

- **Laptop → UNO** is short, fixed-grammar plain text, one command per
  line, newline (`\n`) terminated. No JSON in this direction — parsing
  JSON on the UNO burns RAM and Serial-buffer headroom for no benefit,
  since /core already knows exactly what it wants to say.
- **UNO → Laptop** is small JSON objects, one per line, because the laptop
  has RAM to spare and JSON is much easier for /core to parse than a
  custom text format.

Default: **115200 baud**, 8N1. Change it if you need to — just keep the UNO
sketch and whatever reads it on the laptop in sync.

## Laptop → UNO: command grammar

| Command | Format | Example | Meaning |
|---|---|---|---|
| Warning update | `W,<level>,<location>,<distance_m>` | `W,2,T4,210` | Set flag level, zone, and distance-to-hazard in one shot. Drives LEDs + LCD line 1 (level/location) + LCD line 2 (distance). |
| Countdown start | `C,START,<seconds>` | `C,START,10` | Start a `<seconds>`-long visible/audible countdown (e.g. red flag imminent). |
| Countdown stop | `C,STOP` | `C,STOP` | Cancel an active countdown. No arguments. |
| Idle / reset | `I` | `I` | All LEDs off, buzzer off, LCD back to an idle/all-clear screen. Send this between demo scenes. |

Field definitions:

- **`<level>`** — integer `0`–`4`, mirrors `Decision.severity` from
  `shared/decision.schema.json`. Suggested LED/buzzer behaviour for the
  hardware stub (tune once B/C settle on the final look):

  | level | meaning | green LED | yellow LED | red LED | buzzer |
  |---|---|---|---|---|---|
  | 0–1 | clear / caution | on | off | off | off |
  | 2 | yellow flag | off | slow blink | off | off |
  | 3 | double yellow | off | fast blink | off | short chirps |
  | 4 | red flag recommended | off | off | solid | continuous |

- **`<location>`** — zone code from `shared/track_config.json`, e.g. `T4`.
  Keep it ≤6 characters — the LCD is only 16 columns wide and needs room
  for the level/label too.
- **`<distance_m>`** — integer metres from car to hazard/zone (send `0` if
  not applicable). Shown on LCD line 2.

Parsing note for the UNO: read into a small fixed `char` buffer up to the
first `\n`, then split on commas (e.g. `strtok`). Never use the Arduino
`String` class in the hot path — it fragments the UNO's tiny heap over a
long demo session. `hardware/flagzero_stub/flagzero_stub.ino` already does
this the right way.

## UNO → Laptop: telemetry JSON

One compact JSON object per line:

```json
{"g_force":8.2,"yaw_rate":140,"still_ms":4200,"button":0,"hr":142,"spo2":97}
```

Or, if the MAX30102 isn't wired into this build yet:

```json
{"g_force":8.2,"yaw_rate":140,"still_ms":4200,"button":0,"vitals":"SIM"}
```

| Field | Type | Meaning |
|---|---|---|
| `g_force` | float | Acceleration vector magnitude from the MPU-6500, in g. |
| `yaw_rate` | float | Gyro yaw rate, deg/s. |
| `still_ms` | int | Milliseconds the IMU has read "stationary" (helps distinguish `disabled` from `crash`). |
| `button` | 0 or 1 | "I'm OK" button state (1 = pressed). |
| `hr` | int | Heart rate, bpm, from MAX30102. Omit (and send `"vitals":"SIM"` instead of `hr`/`spo2`) if not wired yet. |
| `spo2` | int | Blood oxygen %, from MAX30102. |

Target cadence: ~5 Hz (one line every ~200 ms) — responsive enough for
fusion without flooding the UNO's serial buffer.

**Note on `car_id`:** it deliberately does *not* appear in this JSON. One
physical Arduino rig plays whichever car a given demo scene needs, so the
firmware shouldn't need to know or care which car it currently represents.
Instead, `/core` tags incoming telemetry with whatever `car_id` the current
scene calls for — see `core/scene_config.json`. Swapping which car a scene
represents becomes a laptop-side config edit, not a firmware re-flash.
