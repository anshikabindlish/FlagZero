"""1D car simulation.

20 cars drive round the lap. Each corner has a target speed: cars brake toward
it and accelerate after it. The same Simulation class runs inside the server
(live, 20 Hz) or on its own with no server (headless, for Monte Carlo/tests).

Headless demo:  python3 -m flagzero.sim.sim --seconds 20
"""
from __future__ import annotations

from typing import Optional

import argparse
import math
import random
import time

from flagzero import config
from flagzero.core.state import CAMERA, RUNNING, STOPPED, CarState, WorldState
from flagzero.core.track import Track, default_track, upstream_distance, wrap


def kmh(v: float) -> float:
    return v / 3.6


class Simulation:
    def __init__(self, world: Optional[WorldState] = None, track: Optional[Track] = None,
                 seed: Optional[int] = config.SIM_SEED):
        self.world = world or WorldState()
        self.track = track or default_track()
        self.lap = self.track.lap_length_m
        self.rng = random.Random(seed)
        self.t = 0.0                    # simulated seconds since start
        self.v_top = kmh(config.SIM_TOP_SPEED_KMH)
        self.a_acc = config.SIM_ACCEL_G * config.G
        self.a_brake = config.SIM_BRAKE_G * config.G
        self._v_allow = self._build_speed_profile()
        self.spawn_cars()

    # ------------------------------------------------------------ speed profile
    def _build_speed_profile(self) -> list[float]:
        """Max allowed speed (m/s) at every metre of the lap.

        At distance d before a corner with target speed vc, a car braking at
        a_brake can be doing at most sqrt(vc^2 + 2*a_brake*d).
        """
        n = int(math.ceil(self.lap))
        prof = [self.v_top] * n
        for c in self.track.corners:
            vc = kmh(c.target_kmh)
            for i in range(n):
                d = upstream_distance(i, c.s, self.lap)
                v = math.sqrt(vc * vc + 2 * self.a_brake * d)
                if v < prof[i]:
                    prof[i] = v
        return prof

    def v_allow(self, s: float) -> float:
        return self._v_allow[int(wrap(s, self.lap)) % len(self._v_allow)]

    # ------------------------------------------------------------ setup
    def spawn_cars(self) -> None:
        """Line the cars up behind SIM_START_M with random 1-3 s gaps."""
        self.world.cars.clear()
        numbers = list(config.SIM_CAR_NUMBERS[: config.SIM_NUM_CARS])
        self.rng.shuffle(numbers)
        s = config.SIM_START_M
        for n in numbers:
            v = self.v_allow(s)
            pace = 1.0 + self.rng.uniform(-config.SIM_SPEED_JITTER, config.SIM_SPEED_JITTER)
            self.world.cars[n] = CarState(
                car=n, track_m=wrap(s, self.lap), speed_mps=v,
                has_phone=n in config.SIM_PHONE_CARS, pace=pace,
            )
            gap_s = self.rng.uniform(*config.SIM_SPACING_S)
            s -= max(v, 20.0) * gap_s

    # ------------------------------------------------------------ inputs
    def stop_car(self, car: int) -> None:
        """An incident involving this car: stop it where it is."""
        c = self.world.cars.get(car)
        if c:
            c.state = STOPPED
            c.speed_mps = 0.0

    def release_car(self, car: int) -> None:
        c = self.world.cars.get(car)
        if c and c.state == STOPPED:
            c.state = RUNNING

    def release_all(self) -> None:
        for c in self.world.cars.values():
            if c.state == STOPPED:
                c.state = RUNNING

    def apply_camera(self, car: int, track_m: float, now_s: Optional[float] = None) -> None:
        """Vision sees this car: its camera position replaces the sim position."""
        c = self.world.cars.get(car)
        if not c:
            return
        now_s = time.monotonic() if now_s is None else now_s
        new = wrap(track_m, self.lap)
        if c.state == CAMERA and c.camera_seen_s is not None:
            dt = now_s - c.camera_seen_s
            if dt > 0:
                c.speed_mps = upstream_distance(c.track_m, new, self.lap) / dt
                if c.speed_mps > self.v_top:   # jumped backwards / noise
                    c.speed_mps = 0.0
        else:
            c.speed_mps = 0.0
        c.track_m = new
        c.camera_seen_s = now_s
        if c.state != STOPPED:
            c.state = CAMERA

    # ------------------------------------------------------------ main update
    def tick(self, dt: float, now_s: Optional[float] = None) -> None:
        """Advance every car by dt seconds. Same code live and headless."""
        now_s = time.monotonic() if now_s is None else now_s
        self.t += dt
        for c in self.world.cars.values():
            if c.state == STOPPED:
                c.speed_mps = 0.0
                continue
            if c.state == CAMERA:
                if c.camera_seen_s is not None and now_s - c.camera_seen_s <= config.CAMERA_OVERRIDE_TIMEOUT_S:
                    continue                    # the camera owns this car's position
                c.state = RUNNING               # camera lost it: sim takes over
            # Look one tick ahead so braking never lags the profile. A faster-paced
            # car (pace > 1) follows a scaled profile, which needs pace^2 x the braking.
            ahead = c.track_m + c.speed_mps * dt
            target = self.v_allow(ahead) * c.pace
            cap = config.SPEED_CAP_KMH.get(c.warning)     # cars obey SLOW ZONE / RED
            if cap is not None:
                target = min(target, kmh(cap))
            if c.speed_mps > target:
                c.speed_mps = max(target, c.speed_mps - self.a_brake * c.pace ** 2 * dt)
            else:
                c.speed_mps = min(target, c.speed_mps + self.a_acc * dt)
            c.track_m = wrap(c.track_m + c.speed_mps * dt, self.lap)

    def run_headless(self, seconds: float, dt: Optional[float] = None) -> None:
        """Run as fast as possible with no server and no sleeping."""
        dt = dt or 1.0 / config.SERVER_TICK_HZ
        steps = int(seconds / dt)
        for _ in range(steps):
            self.tick(dt, now_s=self.t + dt)


def _print_table(sim: Simulation) -> None:
    cars = sorted(sim.world.cars.values(), key=lambda c: -c.track_m)
    row = "  ".join(f"#{c.car:>2} {c.track_m:6.0f}m {c.speed_kmh:5.0f}" for c in cars[:5])
    print(f"t={sim.t:5.1f}s  {row}  ...")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the FlagZero sim headless.")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    sim = Simulation(seed=args.seed)
    t0 = time.perf_counter()
    for _ in range(int(args.seconds)):
        sim.run_headless(1.0)
        _print_table(sim)
    print(f"simulated {args.seconds:.0f} s of racing in {time.perf_counter() - t0:.3f} s")
    t4 = sim.track.corner("T4")
    car21 = sim.world.cars[21]
    d = upstream_distance(car21.track_m, t4.s, sim.lap)
    print(f"car 21 is at {car21.track_m:.0f} m, {d:.0f} m before T4, doing {car21.speed_kmh:.0f} km/h")


if __name__ == "__main__":
    main()
