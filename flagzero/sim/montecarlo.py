"""A5: Monte Carlo - human marshal vs FlagZero, vectorised with numpy.

Each run = one random incident with one car following it.
    following gap 1-10 s, speed 150-250 km/h, sightline 50-250 m,
    braking 1.0-1.5 g, driver reaction 0.7-1.5 s
Time until the driver is warned:
    marshal:  see + react + flag            (sim/marshal.py)
    FlagZero: phone IMU 0.3 s + network 0.05 s (the measured live median);
              if the phone misses the crash -> the marshal path
Either way, if the driver reaches the sightline first they see it themselves.
Secondary impact = distance left when the driver knows < d_need.

CLI:  python3 -m flagzero.sim.montecarlo --n 10000 --seed 42
"""
from __future__ import annotations

from typing import Optional

import argparse
import json
import time

import numpy as np

from flagzero import config
from flagzero.sim import marshal

DEFAULTS = {
    "gap_min": config.MC_GAP_S[0], "gap_max": config.MC_GAP_S[1],
    "speed_min": config.MC_SPEED_KMH[0], "speed_max": config.MC_SPEED_KMH[1],
    "sightline_min": config.MC_SIGHTLINE_M[0], "sightline_max": config.MC_SIGHTLINE_M[1],
    "brake_min": config.MC_BRAKE_G[0], "brake_max": config.MC_BRAKE_G[1],
    "driver_react_min": config.MC_DRIVER_REACT_S[0], "driver_react_max": config.MC_DRIVER_REACT_S[1],
    "marshal_react_min": config.MARSHAL_REACT_S[0], "marshal_react_max": config.MARSHAL_REACT_S[1],
    "visibility": config.MARSHAL_VISIBILITY,
    "imu_fn": config.MC_IMU_FALSE_NEG,
    "network_min": config.MC_NETWORK_S[0], "network_max": config.MC_NETWORK_S[1],
}


def _summary(t_warn, t_eff, t_arrive, dist0, v, react, a, d_need):
    d_left = dist0 - v * t_eff
    lead = t_arrive - t_warn                       # seconds of warning before reaching the hazard
    brake_dist = np.maximum(0.0, d_left - v * react)
    v_hit = np.sqrt(np.maximum(0.0, v ** 2 - 2 * a * brake_dist))
    return {
        "warned_before_hazard_pct": float(np.mean(t_warn < t_arrive) * 100),
        "secondary_impact_pct": float(np.mean(d_left < d_need) * 100),
        "avg_warning_time_s": float(np.mean(lead)),
        "p5_warning_time_s": float(np.percentile(lead, 5)),
        "median_time_to_warn_s": float(np.median(t_warn)),
        "avg_speed_at_hazard_kmh": float(np.mean(v_hit) * 3.6),
    }


def run(n: int = config.MC_N, seed: Optional[int] = None, **overrides) -> dict:
    p = {**DEFAULTS, **{k: float(v) for k, v in overrides.items() if k in DEFAULTS and v is not None}}
    n = int(max(100, min(n, 500_000)))
    t0 = time.perf_counter()
    rng = np.random.default_rng(seed)
    G = config.G

    v = rng.uniform(p["speed_min"], p["speed_max"], n) / 3.6
    gap = rng.uniform(p["gap_min"], p["gap_max"], n)
    dist0 = v * gap                                # following car's distance to the incident at t=0
    sightline = rng.uniform(p["sightline_min"], p["sightline_max"], n)
    a = rng.uniform(p["brake_min"], p["brake_max"], n) * G
    react = rng.uniform(p["driver_react_min"], p["driver_react_max"], n)
    v_safe = config.ROUTER_V_SAFE_KMH / 3.6
    d_need = v * react + np.maximum(0.0, v ** 2 - v_safe ** 2) / (2 * a)
    t_arrive = dist0 / v
    t_sight = np.maximum(0.0, (dist0 - sightline) / v)   # when the driver would see it themselves

    m = marshal.sample(n, rng, visibility=p["visibility"],
                       react_s=(p["marshal_react_min"], p["marshal_react_max"]))
    t_marshal = m["total"]

    imu_ok = rng.random(n) >= p["imu_fn"]
    t_detect = np.where(imu_ok, config.MC_IMU_DETECT_S, np.inf)
    t_net = rng.uniform(p["network_min"], p["network_max"], n)
    all_missed = np.isinf(t_detect)
    t_fz = np.where(all_missed, t_marshal, t_detect + t_net)

    base = _summary(t_marshal, np.minimum(t_marshal, t_sight), t_arrive, dist0, v, react, a, d_need)
    fz = _summary(t_fz, np.minimum(t_fz, t_sight), t_arrive, dist0, v, react, a, d_need)

    base["false_yellows_per_hour"] = 0.0
    fz["false_yellows_per_hour"] = float(
        config.MC_FIELD_CARS * config.MC_IMU_FALSE_EVENTS_PER_CAR_HOUR
        * config.MC_SINGLE_SOURCE_YELLOW_SHARE)
    fz["all_sources_missed_pct"] = float(np.mean(all_missed) * 100)

    mt = marshal.typical(p["visibility"])
    return {
        "n": n, "seed": seed, "params": p,
        "baseline": base, "flagzero": fz,
        "timeline": {                              # medians, for the dashboard's Gantt chart
            "marshal": {"see_s": mt["see"], "react_s": mt["react"], "flag_s": mt["flag"], "total_s": mt["total"]},
            "flagzero": {"detect_s": float(np.median(t_detect[~all_missed])) if (~all_missed).any() else None,
                         "network_s": float(np.median(t_net)),
                         "total_s": float(np.median(t_fz))},
            "car_reaches_hazard_s": float(np.median(t_arrive)),
        },
        "notes": {"false_yellows": "placeholder rates in config.py until the tuning session measures them"},
        "runtime_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


def incident(cars: list, sightline_m: float, fz_warn_s: Optional[float] = None,
             detect_s: float = config.MC_IMU_DETECT_S, n: int = 5000, seed: Optional[int] = 42,
             visibility: float = config.MARSHAL_VISIBILITY) -> dict:
    """Counterfactual for ONE real incident (Person C).

    cars = [(car, dist_m, speed_kmh), ...] for every car approaching the hazard at the moment
    it was detected. Each car is replayed n times with the same physics as run(): once with the
    human-marshal delay (sim/marshal.py) and once with FlagZero's delay. FlagZero's delay is the
    MEASURED detect->warn time for this incident when known (fz_warn_s), otherwise the model
    (detect_s + network, falling back to the marshal if the sensor misses).
    """
    rng = np.random.default_rng(seed)
    G = config.G
    v_safe = config.ROUTER_V_SAFE_KMH / 3.6
    measured = fz_warn_s is not None
    rows, exp_b, exp_f = [], 0.0, 0.0
    for car, dist, kmh in cars:
        if kmh is None or kmh < 5 or dist is None:
            continue                                   # stopped / unknown cars aren't approaching
        v = kmh / 3.6
        a = rng.uniform(*config.MC_BRAKE_G, n) * G
        react = rng.uniform(*config.MC_DRIVER_REACT_S, n)
        d_need = v * react + max(0.0, v * v - v_safe * v_safe) / (2 * a)
        t_arrive = dist / v
        t_sight = max(0.0, (dist - sightline_m) / v)
        t_marshal = marshal.sample(n, rng, visibility=visibility)["total"]
        if measured:
            t_fz = np.full(n, float(fz_warn_s))
        else:
            hit = rng.random(n) >= config.MC_IMU_FALSE_NEG
            t_fz = np.where(hit, detect_s + rng.uniform(*config.MC_NETWORK_S, n), t_marshal)

        def outcome(t_warn):
            t_eff = np.minimum(t_warn, t_sight)
            d_left = dist - v * t_eff
            brake_dist = np.maximum(0.0, d_left - v * react)
            v_hit = np.sqrt(np.maximum(0.0, v * v - 2 * a * brake_dist))
            return {"warn_s": float(np.median(t_warn)),
                    "lead_s": float(np.median(t_arrive - t_warn)),
                    "warned_in_time_pct": float(np.mean(t_warn < t_arrive) * 100),
                    "impact_pct": float(np.mean(d_left < d_need) * 100),
                    "speed_at_hazard_kmh": float(np.mean(v_hit) * 3.6)}

        b, f = outcome(t_marshal), outcome(t_fz)
        exp_b += b["impact_pct"] / 100
        exp_f += f["impact_pct"] / 100
        rows.append({"car": car, "dist_m": round(float(dist)), "speed_kmh": round(float(kmh)),
                     "arrive_s": round(t_arrive, 2), "marshal": b, "flagzero": f})
    return {"n_per_car": n, "sightline_m": sightline_m,
            "flagzero_warn_s": fz_warn_s, "flagzero_measured": measured,
            "cars": rows,
            "expected_impacts": {"marshal": round(exp_b, 2), "flagzero": round(exp_f, 2)},
            "cars_at_risk": {"marshal": sum(r["marshal"]["impact_pct"] >= 50 for r in rows),
                             "flagzero": sum(r["flagzero"]["impact_pct"] >= 50 for r in rows)}}


def save(result: dict) -> None:
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.RESULTS_DIR / "montecarlo.json").write_text(json.dumps(result, indent=2))


ROWS = [
    ("Warning before hazard (%)", "warned_before_hazard_pct", "{:.1f}"),
    ("Secondary-impact scenarios (%)", "secondary_impact_pct", "{:.1f}"),
    ("Average warning time (s)", "avg_warning_time_s", "{:.2f}"),
    ("Worst-case (5th pct) warning time (s)", "p5_warning_time_s", "{:.2f}"),
    ("Median time to warn driver (s)", "median_time_to_warn_s", "{:.2f}"),
    ("Average speed at hazard (km/h)", "avg_speed_at_hazard_kmh", "{:.0f}"),
    ("False yellows per hour", "false_yellows_per_hour", "{:.1f}"),
]


def table(result: dict) -> str:
    lines = [f"{'':40s}{'Marshal':>12s}{'FlagZero':>12s}"]
    for label, key, fmt in ROWS:
        lines.append(f"{label:40s}{fmt.format(result['baseline'][key]):>12s}{fmt.format(result['flagzero'][key]):>12s}")
    lines.append(f"({result['n']} runs, seed={result['seed']}, {result['runtime_ms']} ms)")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=config.MC_N)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--visibility", type=float, default=None)
    args = ap.parse_args()
    res = run(args.n, args.seed, visibility=args.visibility)
    save(res)
    print(table(res))
    print(f"saved {config.RESULTS_DIR / 'montecarlo.json'}")


if __name__ == "__main__":
    main()
