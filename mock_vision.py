"""
flagzero/tools/mock_vision.py

Sends the same {"type":"vision", ...} messages vision.py would, but driven
from the keyboard instead of a camera -- for testing the server/fusion/
severity pipeline without any hardware.

Usage
-----
    python -m flagzero.tools.mock_vision
    python -m flagzero.tools.mock_vision --ws-url ws://localhost:8000/ws/vision

Commands (type the letter, then Enter -- this uses input() so it works the
same on macOS, Windows and Linux with no extra keyboard-capture library)
----------------------------------------------------------------------
    s   car 17 stops at 1423 m
    d   debris hazard at 1423 m (car 17 also stopped there)
    m   multi-stop: car 17 and car 8 both stopped near 1423 m
    c   clear: cars moving normally again, no hazards
    q   quit

Reuses the same auto-reconnecting websocket connection logic as vision.py.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import time

from flagzero.vision.vision import (
    DEFAULT_WS_URL,
    ConnectionState,
    maintain_connection,
    maybe_send,
)

INCIDENT_TRACK_M = 1423.0
SEND_HZ = 2.0

HELP_TEXT = (
    "\nCommands:\n"
    "  s = car 17 stops at 1423 m\n"
    "  d = debris at 1423 m (car 17 also stopped)\n"
    "  m = multi-stop: cars 17 and 8 both stopped near 1423 m\n"
    "  c = clear, cars moving normally\n"
    "  q = quit\n"
)


class Scenario:
    def __init__(self):
        self.mode = "clear"
        self.since = time.time()

    def set_mode(self, mode: str) -> None:
        if mode != self.mode:
            self.mode = mode
            self.since = time.time()


def build_scenario_message(camera_id: int, mode: str, since: float, now: float) -> dict:
    if mode == "clear":
        cars = [
            {"car": 17, "track_m": 1200.0, "speed_px_s": 150.0, "stationary_s": 0.0},
            {"car": 8, "track_m": 900.0, "speed_px_s": 145.0, "stationary_s": 0.0},
        ]
        hazards = []

    elif mode == "stop":
        stationary_s = round(now - since, 1)
        cars = [
            {"car": 17, "track_m": INCIDENT_TRACK_M, "speed_px_s": 0.0, "stationary_s": stationary_s},
        ]
        hazards = []

    elif mode == "debris":
        stationary_s = round(now - since, 1)
        cars = [
            {"car": 17, "track_m": INCIDENT_TRACK_M, "speed_px_s": 0.0, "stationary_s": stationary_s},
        ]
        hazards = [{"type": "debris", "track_m": INCIDENT_TRACK_M}]

    elif mode == "multi":
        stationary_s = round(now - since, 1)
        cars = [
            {"car": 17, "track_m": INCIDENT_TRACK_M, "speed_px_s": 0.0,
             "stationary_s": stationary_s},
            # car 8 piles up ~1.5s after car 17, a little further down the track
            {"car": 8, "track_m": INCIDENT_TRACK_M + 6.0, "speed_px_s": 0.0,
             "stationary_s": round(max(0.0, stationary_s - 1.5), 1)},
        ]
        hazards = []

    else:
        cars, hazards = [], []

    return {
        "type": "vision",
        "camera": camera_id,
        "cars": cars,
        "hazards": hazards,
        "occluded": False,
    }


async def sender_loop(state: ConnectionState, scenario: Scenario, camera_id: int) -> None:
    interval = 1.0 / SEND_HZ
    while True:
        now = time.time()
        msg = build_scenario_message(camera_id, scenario.mode, scenario.since, now)
        sent = await maybe_send(state, msg)
        status = "sent" if sent else "not connected"
        print(f"\r[{scenario.mode:7s}] {status}: {json.dumps(msg)}", end="", flush=True)
        await asyncio.sleep(interval)


async def command_loop(scenario: Scenario) -> None:
    print(HELP_TEXT)
    while True:
        line = await asyncio.to_thread(input, "\n(s/d/m/c/q)> ")
        cmd = line.strip().lower()
        if cmd == "s":
            scenario.set_mode("stop")
            print("-> car 17 stopped at 1423 m")
        elif cmd == "d":
            scenario.set_mode("debris")
            print("-> debris at 1423 m (car 17 also stopped)")
        elif cmd == "m":
            scenario.set_mode("multi")
            print("-> multi-stop: cars 17 and 8 both stopped near 1423 m")
        elif cmd == "c":
            scenario.set_mode("clear")
            print("-> clear, cars moving normally")
        elif cmd == "q":
            print("Quitting.")
            return
        else:
            print(f"Unrecognized '{cmd}'.{HELP_TEXT}")


async def async_main(args) -> None:
    state = ConnectionState()
    scenario = Scenario()

    ws_task = asyncio.create_task(maintain_connection(state, args.ws_url))
    send_task = asyncio.create_task(sender_loop(state, scenario, args.camera_id))

    try:
        await command_loop(scenario)
    finally:
        send_task.cancel()
        ws_task.cancel()
        for t in (send_task, ws_task):
            with contextlib.suppress(asyncio.CancelledError):
                await t


def main() -> None:
    parser = argparse.ArgumentParser(description="Keyboard-driven mock vision sender.")
    parser.add_argument("--ws-url", type=str, default=DEFAULT_WS_URL,
                         help="Race-control server websocket URL.")
    parser.add_argument("--camera-id", type=int, default=1,
                         help="Value reported in the 'camera' field of messages.")
    args = parser.parse_args()

    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("\n[mock_vision] stopped by user.")


if __name__ == "__main__":
    main()
