"""
flagzero/tools/wheel.py -- the physical steering wheel (Arduino buttons, LEDs, buzzer) (Person C)
================================================================================================
Bridges the Arduino in hardware/wheel/ to the server:
  * a wheel button -> {"type": "wheel_button"} on /ws/dash; the server passes it to every
    screen of that car (the phone and the wheel display /car?car=17&wheel=1), which act
    exactly as if the same button had been tapped (same rules as the phone).
  * the car's flag and I'M OK countdown (from the dashboard feed) -> "S <level> <secs>"
    to the Arduino, which lights the LEDs and beeps.

Keys work too (type a letter + Enter), so it can be tested without the Arduino:
    o = I'M OK   c = CONTINUE UNDER YELLOW   r = RECOMMEND RED FLAG   f = REPORT FALSE ALARM   t = TEST CRASH

Run from the repo root (once: py -m pip install pyserial):
    py -m flagzero.tools.wheel                    # finds the Arduino, server on this laptop, car 17
    py -m flagzero.tools.wheel --no-arduino       # keys only
    py -m flagzero.tools.wheel --port COM5 --url wss://<tunnel>.trycloudflare.com
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from typing import Optional

BAUD = 9600
SEND_EVERY_S = 0.5            # the Arduino shows NO LINK after 3 s without a state line
ARDUINO_HINTS = ("arduino", "ch340", "ch341", "wch", "usbmodem", "usbserial", "usb-serial", "usb serial")
BUTTONS = {"OK": "ok", "CONT": "continue", "RED": "red", "FALSE": "false_alarm", "TEST": "test"}
KEYS = {"o": "ok", "c": "continue", "r": "red", "f": "false_alarm", "t": "test"}


def wheel_state(state: dict, car: int) -> tuple[int, int]:
    """(flag level 0-5, seconds left on the I'M OK check or -1) for one car, from a dashboard "state" message."""
    level = next((int(c.get("warning") or 0) for c in state.get("cars", []) if c.get("car") == car), 0)
    secs = -1
    for inc in state.get("incidents", []):
        if inc.get("car") == car and inc.get("countdown") == "PENDING" and inc.get("countdown_left_s") is not None:
            secs = int(inc["countdown_left_s"])
    if state.get("red_confirmed") and secs < 0:
        level = 5                        # the crashed car gets no warnings, but red is red for everyone
    return level, secs


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
    """The wheel on a serial port. Survives being unplugged: it retries every 2 s."""

    def __init__(self, port: Optional[str]):
        import serial  # noqa: F401  (fails early with a clear message if pyserial is missing)
        self.port = port or find_port()
        self.ser = None
        self.buf = b""
        self.retry_at = 0.0
        if not self.port:
            from serial.tools import list_ports
            ports = ", ".join(p.device for p in list_ports.comports()) or "none"
            raise SystemExit(f"Couldn't find the Arduino (serial ports: {ports}). Plug it in, close the "
                             "Arduino IDE, or pass --port COMx. To test with keys only: --no-arduino")
        self._open()

    def _open(self) -> None:
        import serial
        try:
            self.ser = serial.serial_for_url(self.port, BAUD, timeout=0)
            print(f"wheel: Arduino on {self.port}", flush=True)
        except (OSError, ValueError) as e:
            self.ser = None
            self.retry_at = time.monotonic() + 2
            print(f"wheel: can't open {self.port} ({e}). Is the Arduino IDE's Serial Monitor open? Retrying...", flush=True)

    def _lost(self, e: Exception) -> None:
        print(f"wheel: lost the Arduino ({e}); retrying every 2 s", flush=True)
        try:
            self.ser.close()
        except Exception:
            pass
        self.ser = None
        self.retry_at = time.monotonic() + 2

    def _ready(self) -> bool:
        if self.ser is None and time.monotonic() >= self.retry_at:
            self._open()
        return self.ser is not None

    def send_state(self, level: int, secs: int) -> None:
        if self._ready():
            try:
                self.ser.write(f"S {level} {secs}\n".encode())
            except OSError as e:
                self._lost(e)

    def buttons(self) -> list[str]:
        """Buttons pressed since the last call, as wheel_button names."""
        if not self._ready():
            return []
        try:
            n = self.ser.in_waiting
            if n:
                self.buf += self.ser.read(n)
        except OSError as e:
            self._lost(e)
            return []
        *lines, self.buf = self.buf.split(b"\n")
        out = []
        for raw in lines:
            text = raw.decode(errors="replace").strip()
            if text.startswith("BTN ") and text[4:] in BUTTONS:
                out.append(BUTTONS[text[4:]])
            elif text:
                print(f"wheel: arduino says {text}", flush=True)
        return out


async def run(url: str, car: int, arduino: Optional[Arduino]) -> None:
    import websockets

    keys: asyncio.Queue[str] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def read_keys() -> None:
        while True:
            try:
                k = input().strip().lower()[:1]
            except EOFError:
                return
            if k in KEYS:
                loop.call_soon_threadsafe(keys.put_nowait, KEYS[k])

    loop.run_in_executor(None, read_keys)
    ws_url = dash_url(url)
    while True:
        try:
            async with websockets.connect(ws_url, open_timeout=5) as ws:
                print(f"wheel: car {car}, connected to {ws_url}\n"
                      "keys: o I'M OK, c continue, r red flag, f false alarm, t test crash", flush=True)
                level, secs, last_sent, shown = 0, -1, 0.0, None
                while True:
                    try:
                        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.1))
                        if msg.get("type") == "state":
                            level, secs = wheel_state(msg, car)
                    except asyncio.TimeoutError:
                        pass
                    if arduino and ((level, secs) != shown or time.monotonic() - last_sent >= SEND_EVERY_S):
                        arduino.send_state(level, secs)
                        shown, last_sent = (level, secs), time.monotonic()
                    pressed = arduino.buttons() if arduino else []
                    while not keys.empty():
                        pressed.append(keys.get_nowait())
                    for b in pressed:
                        print(f"wheel: {b} pressed", flush=True)
                        await ws.send(json.dumps({"type": "wheel_button", "car": car, "button": b}))
        except (OSError, websockets.WebSocketException) as e:
            print(f"wheel: server not reachable at {ws_url} ({type(e).__name__}); retrying in 2 s", flush=True)
            await asyncio.sleep(2)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="ws://localhost:8000", help="server base, e.g. wss://xyz.trycloudflare.com")
    ap.add_argument("--car", type=int, default=17)
    ap.add_argument("--port", help="serial port, e.g. COM5 (default: find the Arduino)")
    ap.add_argument("--no-arduino", action="store_true", help="keys only, no Arduino")
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
