"""
Track configuration loader -- the single source of truth for lap length and
named zone positions. core and vision should import this instead of
hardcoding numbers like 1423 or 5000.
"""
import json
import os

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "track_config.json")

with open(_CONFIG_PATH, "r") as _f:
    _CONFIG = json.load(_f)

TOTAL_LENGTH_M = _CONFIG["total_length_m"]
ZONES = _CONFIG["zones"]


def zone_position_m(zone_name: str) -> float:
    """Return track_position_m for a named zone, e.g. zone_position_m('T4') -> 1423."""
    try:
        return ZONES[zone_name]["track_position_m"]
    except KeyError:
        raise KeyError(f"Unknown zone {zone_name!r}. Known zones: {list(ZONES)}")


def nearest_zone(track_position_m: float, max_distance_m: float = 100.0):
    """Return the name of the nearest defined zone to a raw track position,
    or None if nothing is within max_distance_m."""
    best_name, best_dist = None, None
    for name, z in ZONES.items():
        d = abs(z["track_position_m"] - track_position_m)
        if best_dist is None or d < best_dist:
            best_name, best_dist = name, d
    if best_dist is not None and best_dist <= max_distance_m:
        return best_name
    return None


def reload():
    """Re-read track_config.json from disk (handy if someone edits it mid-hack)."""
    global _CONFIG, TOTAL_LENGTH_M, ZONES
    with open(_CONFIG_PATH, "r") as f:
        _CONFIG = json.load(f)
    TOTAL_LENGTH_M = _CONFIG["total_length_m"]
    ZONES = _CONFIG["zones"]
