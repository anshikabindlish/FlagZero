"""
flagzero/tools/warning_lights.py -- in-car warning lights on an Arduino (LEDs + buzzer) (Person C)
================================================================================================
Follows one car's warning on the server's dashboard feed (/ws/dash) and sends it to the
Arduino in hardware/warning_lights/ as "S <level>" a few times a second. The Arduino lights
the yellow/red LEDs and beeps when the flag gets worse. By default it follows car 21, the car
behind the crash, so the lights come on before it reaches the incident.

    0 clear, 1 caution, 2 yellow, 3 double yellow (flashing), 4 slow zone (flashing), 5 red

Run from the repo root (once: py -m pip install pyserial):
    py -m flagzero.tools.warning_lights                  # finds the Arduino, server on this laptop, car 21
    py -m flagzero.tools.warning_lights --no-arduino     # only prints the warnings
    py -m flagzero.tools.warning_lights --port COM5 --url wss://<tunnel>.trycloudflare.com --car 21
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from typing import Optional

BAUD = 9600
SEND_EVERY_S = 0.5            # the Arduino shows "no link" after 3 s without a line
ARDUINO_HINTS = ("arduino", "ch340", "ch341", "wch", "usbmodem", "usbserial", "usb-serial", "usb serial")
LABELS = ["CLEAR", "CAUTION", "YELLOW", "DOUBLE YELLOW", "SLOW ZONE", "RED FLAG"]


def car_level(state: dict, car: int) -> int:
    """The warning level (0-5) one car is getting, from a dashboard "state" message."""
    level = next((int(c.get("warning") or 0) for c in state.get("cars", []) if c.get("car") == car), 0)
    if state.get("red_confirmed"):
        level = 5                        # red is red for everyone, the crashed car included
    return max(0, min(5, level))


def dash_url(base: str) -> str:
    base = base.strip().rstrip("/")
    if base.startswith("http"):
        base = "ws" + base[4:]           # http -> ws, https -> wss
    elif not base.startswith("ws"):
        base = "ws://" + base
    return base + "/ws/dash"


def find_port() -> Optional[str]:
    from serial.tools import list_ports
    for p in list_ports.comports():
        if any(h in f"{p.device} {p.description} {p.manufacturer or ''}".lower() for h in ARDUINO_HINTS):
            return p.device
    return None


class Arduino:
    """The warning lights on a serial port. Survives being unplugged: it retries every 2 s."""

    def __init__(self, port: Optional[str]):
        import serial  # noqa: F401  (fails early with a clear message if pyserial is missing)
        self.port = port or find_port()
        self.ser = None
        self.retry_at = 0.0
        if not self.port:
            from serial.tools import list_ports
            ports = ", ".join(p.device for p in list_ports.comports()) or "none"
            raise SystemExit(f"Couldn't find the Arduino (serial ports: {ports}). Plug it in, close the "
                             "Arduino IDE, or pass --port COMx. To run without it: --no-arduino")
        self._open()

    def _open(self) -> None:
        import serial
        try:
            self.ser = serial.serial_for_url(self.port, BAUD, timeout=0)
            print(f"lights: Arduino on {self.port}", flush=True)
        except (OSError, ValueError) as e:
            self.ser = None
            self.retry_at = time.monotonic() + 2
            print(f"lights: can't open {self.port} ({e}). Is the Arduino IDE's Serial Monitor open? Retrying...",
                  flush=True)

    def send(self, level: int) -> None:
        if self.ser is None and time.monotonic() >= self.retry_at:
            self._open()
        if self.ser is None:
            return
        try:
            self.ser.write(f"S {level}\n".encode())
            if self.ser.in_waiting:
                self.ser.read(self.ser.in_waiting)      # discard "READY"
        except OSError as e:
            print(f"lights: lost the Arduino ({e}); retrying every 2 s", flush=True)
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
            self.retry_at = time.monotonic() + 2


async def run(url: str, car: int, arduino: Optional[Arduino]) -> None:
    import websockets

    ws_url = dash_url(url)
    while True:
        try:
            async with websockets.connect(ws_url, open_timeout=5) as ws:
                print(f"lights: following car {car}, connected to {ws_url}", flush=True)
                level, shown, last_sent = 0, None, 0.0
                while True:
                    try:
                        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.2))
                        if msg.get("type") == "state":
                            level = car_level(msg, car)
                    except asyncio.TimeoutError:
                        pass
                    if level != shown:
                        print(f"{time.strftime('%H:%M:%S')}  car {car}: {LABELS[level]}", flush=True)
                    if arduino and (level != shown or time.monotonic() - last_sent >= SEND_EVERY_S):
                        arduino.send(level)
                        last_sent = time.monotonic()
                    shown = level
        except (OSError, websockets.WebSocketException) as e:
            print(f"lights: server not reachable at {ws_url} ({type(e).__name__}); retrying in 2 s", flush=True)
            await asyncio.sleep(2)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="ws://localhost:8000", help="server base, e.g. wss://xyz.trycloudflare.com")
    ap.add_argument("--car", type=int, default=21, help="the car whose warnings the lights show (default 21)")
    ap.add_argument("--port", help="serial port, e.g. COM5 (default: find the Arduino)")
    ap.add_argument("--no-arduino", action="store_true", help="only print the warnings")
    args = ap.parse_args()
    try:
        arduino = None if args.no_arduino else Arduino(args.port)
    except ImportError:
        raise SystemExit("pyserial isn't installed. Run once:  py -m pip install pyserial")
    try:
        asyncio.run(run(args.url, args.car, arduino))
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()
