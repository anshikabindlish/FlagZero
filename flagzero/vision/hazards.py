"""Hazard engine: turns car tracks + scene state into the `hazards` list.

Observed hazards (the car/debris is already there):
  STOPPED_VEHICLE, DEBRIS, MULTI_STOP, OFF_TRACK
Predicted hazards (raised BEFORE a car comes to rest - "predicted": true):
  SPIN_RISK   yaw rate or nose-vs-track angle says the car is rotating/sliding
  OFF_TRACK   extrapolated position leaves the corridor within LOOKAHEAD_S
  SLOWING     sharp deceleration well above normal braking
  CLOSING     a following car will reach the car ahead within CLOSING_TTC_S

Each hazard: {"kind", "car", "track_m", "conf", "predicted", "detail"}.
Timers for STOPPED/DEBRIS pause while a hand is in frame (occluded).
"""
from __future__ import annotations

import itertools

from flagzero.vision import vision_config as C
from flagzero.vision.tracker import CarTrack


def _clip(v, lo=0.0, hi=C.CONF_MAX):
    return max(lo, min(hi, v))


class _Latch:
    """Condition must hold CONFIRM s to turn on; stays on HOLD s after it stops."""

    def __init__(self, confirm_s, hold_s):
        self.confirm_s, self.hold_s = confirm_s, hold_s
        self.since = None
        self.on_until = -1e9
        self.payload = None
        self.first_on = None

    def step(self, cond, t, payload=None):
        if cond:
            if self.since is None:
                self.since = t
            if t - self.since >= self.confirm_s:
                if self.first_on is None:
                    self.first_on = t
                self.on_until = t + self.hold_s
                self.payload = payload
        else:
            self.since = None
        on = t <= self.on_until
        if not on:
            self.first_on = None
        return on

    def reset(self):
        self.__init__(self.confirm_s, self.hold_s)


class HazardEngine:
    def __init__(self, cal):
        self.cal = cal
        self._latches: dict[tuple, _Latch] = {}
        self.stop_since: dict[int, float] = {}       # car -> time STOPPED raised

    def _latch(self, key, confirm, hold=C.HAZARD_HOLD_S):
        if key not in self._latches:
            self._latches[key] = _Latch(confirm, hold)
        return self._latches[key]

    def reset(self):
        self._latches.clear()
        self.stop_since.clear()

    # ------------------------------------------------------------------
    def step(self, tracks: list[CarTrack], t: float, debris: list[dict] | None = None):
        hz = []
        stopped = []
        for tr in tracks:
            hz += self._car_hazards(tr, t, stopped)
        hz += self._closing(tracks, t)
        if C.MULTI_STOP_ENABLED:
            hz += self._multi_stop(stopped, t)
        hz += list(debris or [])
        # most severe first; one per (kind, car)
        hz.sort(key=lambda h: (-h["conf"]))
        return hz

    def risk_by_car(self, hazards):
        out = {}
        for h in hazards:
            for c in [h.get("car")] + h.get("detail", {}).get("cars", []):
                if c is not None:
                    out[c] = max(out.get(c, 0.0), h["conf"])
        return out

    # ------------------------------------------------------------------
    def _car_hazards(self, tr: CarTrack, t, stopped):
        out = []
        car, m = tr.car, tr.track_m
        moving = tr.speed_kmh > C.MOVING_KMH
        q = tr.quality

        # STOPPED_VEHICLE (observed). Timer already paused by the tracker.
        is_stopped = tr.armed and tr.stationary_s >= C.STOPPED_RAISE_S
        if is_stopped:
            self.stop_since.setdefault(car, t)
            conf = _clip(0.65 + 0.10 * (tr.stationary_s - C.STOPPED_RAISE_S) + 0.20 * q, hi=0.97)
            backwards = tr.heading_err is not None and abs(tr.heading_err) > 120
            d = {"stationary_s": round(tr.stationary_s, 1), "backwards": backwards,
                 "off_track": tr.lateral > C.OFF_TRACK_LATERAL}
            out.append(self._hz("STOPPED_VEHICLE", car, m, conf, False, d))
            stopped.append(tr)
        else:
            self.stop_since.pop(car, None)

        # SPIN_RISK (predicted)
        yaw = abs(tr.yaw_rate)
        herr = abs(tr.heading_err) if tr.heading_err is not None else 0.0
        spin_cond = (tr.visible(t) and not is_stopped and
                     (yaw > C.SPIN_YAW_DPS or (moving and herr > C.SPIN_HEADING_ERR_DEG)))
        conf = _clip((0.55 + 0.25 * min(1.0, yaw / (2 * C.SPIN_YAW_DPS))
                      + 0.10 * min(1.0, herr / 90.0)) * (0.7 + 0.3 * q))
        if self._latch(("SPIN", car), C.SPIN_CONFIRM_S).step(spin_cond, t, conf):
            p = self._latches[("SPIN", car)].payload
            out.append(self._hz("SPIN_RISK", car, m, p, True,
                                {"yaw_rate_dps": round(tr.yaw_rate), "heading_err_deg":
                                 None if tr.heading_err is None else round(tr.heading_err)}))

        # OFF_TRACK - observed, or predicted from the trajectory
        off_now = tr.lateral > C.OFF_TRACK_LATERAL and tr.visible(t)
        pred = False
        if not off_now and moving and tr.visible(t):
            pred = tr.predicted_lateral(C.LOOKAHEAD_S, self.cal) > C.PREDICT_OFF_LATERAL
        cond = off_now or pred
        conf = _clip(0.75 + 0.1 * min(1.0, tr.lateral - 1.0) if off_now else 0.58)
        if self._latch(("OFF", car), C.OFF_TRACK_CONFIRM_S).step(cond, t, (conf, not off_now)):
            c, p = self._latches[("OFF", car)].payload
            out.append(self._hz("OFF_TRACK", car, m, c, p, {"lateral": round(tr.lateral, 2)}))

        # SLOWING (predicted, low confidence -> severity 1 on the server)
        slow_cond = (tr.visible(t) and not is_stopped and
                     tr.decel_kmh_s > C.SLOWING_DECEL_KMH_S and
                     tr._prev_kmh + tr.decel_kmh_s * 0.2 > C.SLOWING_MIN_FROM_KMH)
        if self._latch(("SLOW", car), 0.1).step(slow_cond, t, 0.5):
            out.append(self._hz("SLOWING", car, m, 0.5, True,
                                {"decel_kmh_s": round(tr.decel_kmh_s)}))
        return out

    def _closing(self, tracks, t):
        out = []
        cands = [tr for tr in tracks if tr.visible(t) or tr.still]
        for a, b in itertools.permutations(cands, 2):          # a follows b
            gap = b.track_m - a.track_m
            if not (0 < gap <= C.CLOSING_MAX_GAP_M):
                continue
            closing = a.v_along - b.v_along                    # track m/s
            cond = closing > 0.5 and gap / closing < C.CLOSING_TTC_S
            ttc = gap / closing if closing > 0 else 99.0
            conf = _clip(0.6 + 0.3 * (1 - min(1.0, ttc / C.CLOSING_TTC_S)))
            if self._latch(("CLOSE", a.car, b.car), 0.1, 1.0).step(cond, t, (conf, ttc, gap)):
                c, ttc_l, gap_l = self._latches[("CLOSE", a.car, b.car)].payload
                out.append(self._hz("CLOSING", a.car, b.track_m, c, True,
                                    {"ahead": b.car, "ttc_s": round(float(ttc_l), 2),
                                     "gap_m": round(float(gap_l), 1)}))
        return out

    def _multi_stop(self, stopped, t):
        out = []
        if len(stopped) < 2:
            return out
        stopped = sorted(stopped, key=lambda tr: tr.track_m)
        clusters = [[stopped[0]]]
        for tr in stopped[1:]:
            if tr.track_m - clusters[-1][-1].track_m <= C.MULTI_STOP_GAP_M:
                clusters[-1].append(tr)
            else:
                clusters.append([tr])
        group = max(clusters, key=len)
        times = [self.stop_since.get(tr.car, t) for tr in group]
        if len(group) >= 2 and max(times) - min(times) <= C.MULTI_STOP_WINDOW_S:
            m = sum(tr.track_m for tr in group) / len(group)
            conf = _clip(0.8 + 0.05 * len(group))
            out.append(self._hz("MULTI_STOP", group[0].car, m, conf, False,
                                {"cars": [tr.car for tr in group]}))
        return out

    @staticmethod
    def _hz(kind, car, m, conf, predicted, detail=None):
        h = {"kind": kind, "track_m": round(float(m), 1), "conf": round(float(conf), 2),
             "predicted": bool(predicted), "detail": detail or {}}
        if car is not None:
            h["car"] = car
        return h
