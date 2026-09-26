"""
flagzero/tools/export_track.py -- build flagzero/track.json from real F1 data (Person C)
=========================================================================================
Uses FastF1 (official F1 timing/position data) to export the real Interlagos
(Autodromo Jose Carlos Pace, Sao Paulo) layout in the format core/track.py reads:
the pole lap's X/Y trace ("polyline", s = lap distance), the official corner
positions with a target speed taken from the real lap and evenly spaced marshal
posts. The raw speed trace is kept too. No corner is special.

Only needed to regenerate track.json; the file is committed, so nobody else
needs FastF1 installed.

    py -m pip install fastf1
    py -m flagzero.tools.export_track            # 2023 Sao Paulo GP qualifying (dry)
    py -m flagzero.tools.export_track --year 2024
"""
import argparse
import json
import pathlib
import tempfile

import fastf1
import numpy as np

OUT = pathlib.Path(__file__).resolve().parent.parent / "track.json"

# Interlagos corner names by official turn number
NAMES = {1: "S do Senna", 2: "S do Senna", 3: "Curva do Sol", 4: "Descida do Lago", 5: "Descida do Lago",
         6: "Ferradura", 7: "Ferradura", 8: "Laranjinha", 9: "Pinheirinho", 10: "Bico de Pato",
         11: "Mergulho", 12: "Junção", 13: "Subida dos Boxes", 14: "Subida dos Boxes", 15: "Subida dos Boxes"}

STEP_M = 10               # resample spacing
RACE_PACE = 0.92          # corner target speeds = qualifying minimum x this
CORNER_WINDOW_M = 80      # look for the slowest point within this of each corner
MARSHAL_POSTS = 8
# Assumed sightlines (m): rough guesses, not measured.
SIGHTLINE_M = {"T1": 250, "T2": 120, "T3": 180, "T4": 180, "T5": 150, "T6": 160, "T7": 120, "T8": 130,
               "T9": 110, "T10": 100, "T11": 140, "T12": 120, "T13": 200, "T14": 220, "T15": 250}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2023)  # 2024 qualifying was wet
    ap.add_argument("--session", default="Q")
    args = ap.parse_args()

    cache = pathlib.Path(tempfile.gettempdir()) / "fastf1_cache"
    cache.mkdir(exist_ok=True)
    fastf1.Cache.enable_cache(str(cache))

    session = fastf1.get_session(args.year, "Sao Paulo", args.session)
    session.load(laps=True, telemetry=True, weather=False, messages=False)
    lap = session.laps.pick_fastest()
    tel = lap.get_telemetry().add_distance()
    info = session.get_circuit_info()

    d = tel["Distance"].to_numpy()
    x = tel["X"].to_numpy() / 10.0  # FastF1 X/Y are in 1/10 m
    y = tel["Y"].to_numpy() / 10.0
    v = tel["Speed"].to_numpy()
    lap_m = float(round(d[-1]))

    grid = np.arange(0, lap_m, STEP_M)
    xs, ys, vs = (np.interp(grid, d, a) for a in (x, y, v))
    xs, ys = xs - xs.min(), ys - ys.min()

    corners = []
    for _, c in info.corners.iterrows():
        n = int(c["Number"])
        name = f"T{n}{c['Letter'] or ''}"
        pos = float(c["Distance"])
        near = (np.abs(grid - pos) <= CORNER_WINDOW_M) | (np.abs(grid - pos) >= lap_m - CORNER_WINDOW_M)
        corners.append({
            "name": name,
            "s": round(pos, 1),
            "target_kmh": round(float(vs[near].min()) * RACE_PACE),
            "sightline_m": SIGHTLINE_M.get(name, 150),
            "note": NAMES.get(n, ""),
        })

    spacing = lap_m / MARSHAL_POSTS
    track = {
        "name": "Autódromo José Carlos Pace (Interlagos), São Paulo",
        "source": f"FastF1 {args.year} São Paulo GP {args.session}, {lap['Driver']} fastest lap {lap['LapTime']}",
        "direction": "counter-clockwise",
        "lap_length_m": lap_m,
        "units": {"x": "m", "y": "m", "s": "m along the lap", "target_kmh": "km/h"},
        "polyline": [{"x": round(float(a), 1), "y": round(float(b), 1), "s": round(float(s), 1)}
                     for a, b, s in zip(xs, ys, grid)],
        "corners": corners,
        "marshal_posts": [{"id": f"M{i + 1}", "s": round(spacing * (i + 0.5)),
                           "covers_from_m": round(spacing * i), "covers_to_m": round(spacing * (i + 1))}
                          for i in range(MARSHAL_POSTS)],
        "speed_profile_kmh": [[round(float(s), 1), round(float(sp))] for s, sp in zip(grid, vs)],
        "notes": "Positions are metres along the lap from the timing line. target_kmh = the real qualifying lap's "
                 "slowest point near each corner x 0.92. Sightlines are rough assumptions.",
    }
    OUT.write_text(json.dumps(track, indent=1), encoding="utf-8")
    print(f"wrote {OUT}: {lap_m:.0f} m, {len(track['polyline'])} points, {len(corners)} corners, "
          f"no special corner")
    for c in corners:
        print(f"  {c['name']:4} {c['s']:7.1f} m  {c['target_kmh']:4} km/h  sightline {c['sightline_m']:3} m  {c['note']}")


if __name__ == "__main__":
    main()
