"""Background WebSocket sender for /ws/vision.

Only the newest message matters, so the queue holds one item and old ones are
dropped. Reconnects forever with backoff; never raises into the vision loop.
"""
from __future__ import annotations

import json
import queue
import threading
import time

from flagzero.vision import vision_config as C

try:
    from websockets.sync.client import connect as ws_connect   # websockets >= 11
except Exception:                                               # pragma: no cover
    ws_connect = None


class VisionSender:
    def __init__(self, url: str = C.SERVER_WS_URL, enabled: bool = True):
        self.url = url
        self.enabled = enabled and ws_connect is not None
        self.connected = False
        self.sent = 0
        self.last_error = "" if ws_connect else "pip install websockets>=11"
        self.last_sent_ms = 0
        self.inbox = queue.Queue(maxsize=50)   # anything the server sends back
        self._q: queue.Queue = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._th = None
        if self.enabled:
            self._th = threading.Thread(target=self._run, daemon=True, name="vision-sender")
            self._th.start()

    def send(self, msg: dict):
        if not self.enabled:
            return
        try:
            self._q.get_nowait()             # drop the stale one
        except queue.Empty:
            pass
        try:
            self._q.put_nowait(msg)
        except queue.Full:
            pass

    def close(self):
        self._stop.set()

    def status(self):
        if not self.enabled:
            return "server: off" + (f" ({self.last_error})" if self.last_error else "")
        return (f"server: {'CONNECTED' if self.connected else 'disconnected'} "
                f"sent={self.sent}" + ("" if self.connected else f" {self.last_error[:40]}"))

    # ------------------------------------------------------------------
    def _run(self):
        backoff = C.RECONNECT_MIN_S
        while not self._stop.is_set():
            try:
                with ws_connect(self.url, open_timeout=3, close_timeout=1) as ws:
                    self.connected, self.last_error = True, ""
                    backoff = C.RECONNECT_MIN_S
                    while not self._stop.is_set():
                        try:
                            msg = self._q.get(timeout=0.2)
                        except queue.Empty:
                            msg = None
                        if msg is not None:
                            ws.send(json.dumps(msg, separators=(",", ":")))
                            self.sent += 1
                            self.last_sent_ms = int(time.time() * 1000)
                        self._drain(ws)
            except Exception as e:           # server down, restarting, tunnel hiccup
                self.last_error = type(e).__name__ + ": " + str(e)
            self.connected = False
            if self._stop.wait(backoff):
                break
            backoff = min(C.RECONNECT_MAX_S, backoff * 2)

    def _drain(self, ws):
        """The echo skeleton sends every message back; read without blocking."""
        while True:
            try:
                raw = ws.recv(timeout=0)
            except TimeoutError:
                return
            try:
                self.inbox.put_nowait(raw)
            except queue.Full:
                try:
                    self.inbox.get_nowait()
                except queue.Empty:
                    pass
