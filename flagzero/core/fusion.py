"""A3: noisy-OR fusion.

fused = 1 - prod(1 - conf_i) over independent sources. Each source type (IMU,
CAMERA) counts once, using its best confidence. A missing I'm-OK response is a
corroborating signal for escalation but adds nothing to the number.
"""
from __future__ import annotations

from flagzero.core.state import Incident


def noisy_or(confs: list[float]) -> float:
    p_all_wrong = 1.0
    for c in confs:
        p_all_wrong *= 1.0 - max(0.0, min(1.0, c))
    return 1.0 - p_all_wrong


def best_by_source(inc: Incident) -> dict[str, float]:
    best: dict[str, float] = {}
    for s in inc.sources:
        if s.get("conf") is None:            # NO_RESPONSE has no confidence
            continue
        best[s["src"]] = max(best.get(s["src"], 0.0), float(s["conf"]))
    return best


def fused_confidence(inc: Incident) -> float:
    return noisy_or(list(best_by_source(inc).values()))
