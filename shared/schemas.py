"""
Canonical data contracts for FlagZero. Everyone imports from here instead of
re-typing field names/enums -- if a shape changes, it changes in one place.

Two core shapes:
  - Incident: a single detection from one source (IMU or camera)
  - Decision: the fused/decided flag state, published to the dashboard

Plus WebSocket envelope helpers for core <-> dashboard traffic. See
shared/incident.schema.json, shared/decision.schema.json and
shared/ws_messages.md for the formal/documented versions of the same
shapes this module builds and validates.
"""
import time
import uuid

EVENT_TYPES = {"crash", "spin", "rollover", "disabled", "debris"}
SOURCES = {"imu", "camera"}
LABELS = {
    "NORMAL", "CAUTION", "YELLOW", "DOUBLE_YELLOW", "SLOW_ZONE", "RED_FLAG_RECOMMENDED",
}

# Suggested severity <-> label mapping (CONFIRM WITH THE TEAM, see root README).
# There are 6 labels and only 5 severity rungs (0-4) -- SLOW_ZONE is treated
# here as a local/orthogonal state that could co-occur with CAUTION or
# YELLOW, rather than a rung of the same ladder. If you'd rather it *be* a
# rung (e.g. bump DOUBLE_YELLOW and RED_FLAG_RECOMMENDED to make room),
# change this dict -- everything else keys off of it.
SEVERITY_LABELS = {
    0: "NORMAL",
    1: "CAUTION",
    2: "YELLOW",
    3: "DOUBLE_YELLOW",
    4: "RED_FLAG_RECOMMENDED",
}

WS_MESSAGE_TYPES = {"incident", "severity_update", "eta_update", "monte_carlo_result"}


def new_incident(event, source, car_id, location, track_position_m, confidence, extra=None):
    """Build a schema-valid Incident dict. Raises ValueError on bad inputs."""
    if event not in EVENT_TYPES:
        raise ValueError(f"event must be one of {EVENT_TYPES}, got {event!r}")
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}, got {source!r}")
    if not (0.0 <= confidence <= 1.0):
        raise ValueError(f"confidence must be in [0, 1], got {confidence!r}")

    incident = {
        "id": str(uuid.uuid4()),
        "event": event,
        "source": source,
        "car_id": car_id,
        "location": location,
        "track_position_m": track_position_m,
        "confidence": round(float(confidence), 4),
        "timestamp": time.time(),
    }
    if extra:
        incident["extra"] = extra
    return incident


def validate_incident(d: dict) -> None:
    """Raise ValueError with a clear message if d doesn't match the Incident shape."""
    required = ["event", "source", "car_id", "location", "track_position_m", "confidence", "timestamp"]
    missing = [k for k in required if k not in d]
    if missing:
        raise ValueError(f"Incident missing fields: {missing}")
    if d["event"] not in EVENT_TYPES:
        raise ValueError(f"Incident.event {d['event']!r} not in {EVENT_TYPES}")
    if d["source"] not in SOURCES:
        raise ValueError(f"Incident.source {d['source']!r} not in {SOURCES}")
    if not (0.0 <= d["confidence"] <= 1.0):
        raise ValueError(f"Incident.confidence {d['confidence']!r} out of [0,1]")


def new_decision(severity, fused_confidence, location, track_position_m,
                  medical_response=False, label=None, car_id=None,
                  contributing_incident_ids=None):
    """Build a schema-valid Decision dict. Raises ValueError on bad inputs."""
    if not (0 <= severity <= 4):
        raise ValueError(f"severity must be 0-4, got {severity!r}")
    label = label or SEVERITY_LABELS[severity]
    if label not in LABELS:
        raise ValueError(f"label must be one of {LABELS}, got {label!r}")

    decision = {
        "severity": severity,
        "label": label,
        "fused_confidence": round(float(fused_confidence), 4),
        "medical_response": bool(medical_response),
        "location": location,
        "track_position_m": track_position_m,
        "timestamp": time.time(),
    }
    if car_id is not None:
        decision["car_id"] = car_id
    if contributing_incident_ids:
        decision["contributing_incident_ids"] = contributing_incident_ids
    return decision


def validate_decision(d: dict) -> None:
    required = ["severity", "label", "fused_confidence", "medical_response", "location", "track_position_m"]
    missing = [k for k in required if k not in d]
    if missing:
        raise ValueError(f"Decision missing fields: {missing}")
    if not (0 <= d["severity"] <= 4):
        raise ValueError(f"Decision.severity {d['severity']!r} out of 0-4")
    if d["label"] not in LABELS:
        raise ValueError(f"Decision.label {d['label']!r} not in {LABELS}")


def ws_envelope(msg_type: str, data: dict) -> dict:
    """Wrap a payload in the {"type": ..., "data": ...} envelope every
    WebSocket message uses -- see shared/ws_messages.md."""
    if msg_type not in WS_MESSAGE_TYPES:
        raise ValueError(f"Unknown WS message type {msg_type!r}, expected one of {WS_MESSAGE_TYPES}")
    return {"type": msg_type, "data": data}
