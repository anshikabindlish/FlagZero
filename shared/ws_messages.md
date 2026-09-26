# WebSocket message types — /core ⟷ /dashboard

Every message on the wire is a JSON object with the same envelope:

```json
{ "type": "<message_type>", "data": { ... } }
```

`type` is one of the four below. `data` is the payload shape for that type.
Build these with `shared/schemas.py`'s `ws_envelope()` helper rather than
hand-rolling the envelope, so nobody typos the wrapper.

## `incident`

A single raw detection from IMU or camera input, unmodified — see
`shared/incident.schema.json`.

```json
{
  "type": "incident",
  "data": {
    "id": "b1f2c1a0-...",
    "event": "crash",
    "source": "camera",
    "car_id": 4,
    "location": "T4",
    "track_position_m": 1423,
    "confidence": 0.91,
    "timestamp": 1732650000.42
  }
}
```

## `severity_update`

The fused/decided flag state — see `shared/decision.schema.json`. This is
what drives the dashboard's flag display and (via /core's serial link) the
trackside LEDs/LCD/buzzer.

```json
{
  "type": "severity_update",
  "data": {
    "severity": 4,
    "label": "RED_FLAG_RECOMMENDED",
    "fused_confidence": 0.996,
    "medical_response": true,
    "location": "T4",
    "track_position_m": 1423,
    "car_id": 4,
    "timestamp": 1732650000.5,
    "contributing_incident_ids": ["b1f2c1a0-..."]
  }
}
```

## `eta_update`

How long until a hazard becomes relevant to a given car/position — drives
any "closing in" countdown UI.

```json
{
  "type": "eta_update",
  "data": {
    "car_id": 17,
    "location": "T4",
    "track_position_m": 1423,
    "eta_s": 6.4,
    "speed_mps": 33.2
  }
}
```

## `monte_carlo_result`

Output of whatever risk simulation /core runs to help decide severity ahead
of (or alongside) a hard rule kicking in.

```json
{
  "type": "monte_carlo_result",
  "data": {
    "scenario": "T4_collision_risk",
    "trials": 10000,
    "p_collision": 0.62,
    "p_severity_gte_3": 0.18,
    "recommended_label": "DOUBLE_YELLOW"
  }
}
```

---

Dashboard implementations should render/log any `type` they don't recognise
rather than crashing on it — new message types will get added as fusion
logic matures, and the stub (`dashboard/index.html`) already does this.
