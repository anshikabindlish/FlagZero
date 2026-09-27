"""A6: real end-to-end latency, all on server time.

For each incident: detect_ms (first imu_event received)
 -> warn_sent_ms (first warning with level > 0 sent to another phone car)
 -> ack_ms (that phone's ack for the warning; closest we get to "shown").
Every completed trial is appended to results/latency.csv.

Report:  python3 -m flagzero.core.latency
"""
from __future__ import annotations

from typing import Optional

import csv
import logging
import statistics

from flagzero import config

log = logging.getLogger("flagzero.latency")
CSV = config.RESULTS_DIR / "latency.csv"
FIELDS = ["incident", "kind", "to_car", "detect_ms", "warn_sent_ms", "ack_ms",
          "detect_to_sent_ms", "detect_to_ack_ms", "tunnel_rtt_ms"]


class LatencyTracker:
    def __init__(self) -> None:
        self.pending: dict[int, dict] = {}     # warning msg id -> trial row
        self.done: set[tuple[int, int]] = set()   # (incident id, phone car) already measured
        self.last: Optional[dict] = None

    def warning_sent(self, msg_id: int, inc, to_car: int, level: int, t_ms: int) -> None:
        if level <= 0 or inc is None or inc.detect_ms is None or inc.car == to_car:
            return
        if (inc.id, to_car) in self.done:
            return
        self.done.add((inc.id, to_car))
        self.pending[msg_id] = {"incident": inc.id, "kind": inc.kind, "to_car": to_car,
                                "detect_ms": inc.detect_ms, "warn_sent_ms": t_ms}

    def ack(self, msg_id: int, t_ms: int, rtt_ms: Optional[int]) -> Optional[dict]:
        row = self.pending.pop(msg_id, None)
        if row is None:
            return None
        row["ack_ms"] = t_ms
        row["detect_to_sent_ms"] = row["warn_sent_ms"] - row["detect_ms"]
        row["detect_to_ack_ms"] = t_ms - row["detect_ms"]
        row["tunnel_rtt_ms"] = rtt_ms
        self._append(row)
        self.last = row
        s = stats()
        log.info("LATENCY trial: detect->sent %d ms, detect->ack %d ms | n=%d median=%s p90=%s max=%s",
                 row["detect_to_sent_ms"], row["detect_to_ack_ms"], s["n"], s["median"], s["p90"], s["max"])
        return row

    def reset(self) -> None:
        self.pending.clear()
        self.done.clear()

    @staticmethod
    def _append(row: dict) -> None:
        config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        new = not CSV.exists()
        with CSV.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if new:
                w.writeheader()
            w.writerow({k: row.get(k) for k in FIELDS})


def stats(column: str = "detect_to_ack_ms") -> dict:
    if not CSV.exists():
        return {"n": 0, "median": None, "p90": None, "max": None}
    with CSV.open() as f:
        vals = sorted(float(r[column]) for r in csv.DictReader(f) if r.get(column))
    if not vals:
        return {"n": 0, "median": None, "p90": None, "max": None}
    p90 = vals[min(len(vals) - 1, int(round(0.9 * (len(vals) - 1))))]
    return {"n": len(vals), "median": round(statistics.median(vals)), "p90": round(p90), "max": round(vals[-1])}


def main() -> None:
    for col in ("detect_to_sent_ms", "detect_to_ack_ms"):
        print(col, stats(col))


if __name__ == "__main__":
    main()
