"""
flagzero/tools/start_demo.py -- one-click demo launcher (Person C)
==================================================================
Starts server.py (port 8000) and a cloudflared quick tunnel, finds the tunnel's
https address, then opens the dashboard and the QR join page. Ctrl+C stops both.

    Windows: double-click start_demo.bat          (or: py -m flagzero.tools.start_demo)
    Mac:     double-click start_demo.command      (or: python3 -m flagzero.tools.start_demo)

Options:
    --no-tunnel   server only (phones on the same Wi-Fi can't use motion sensors without https)
    --no-browser  don't open browser tabs
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from flagzero import config

PORT = 8000
ROOT = config.PACKAGE_DIR.parent
TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def find_cloudflared() -> str | None:
    found = shutil.which("cloudflared")
    if found:
        return found
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", "")) / "cloudflared" / "cloudflared.exe",
        Path(os.environ.get("ProgramFiles", "")) / "cloudflared" / "cloudflared.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "cloudflared.exe",
        Path("/opt/homebrew/bin/cloudflared"),
        Path("/usr/local/bin/cloudflared"),
    ]
    return next((str(p) for p in candidates if p.is_file()), None)


def dns_ready(host: str) -> bool:
    """True once public DNS (Cloudflare AND Google) can resolve the new tunnel name.

    Asked over DNS-over-HTTPS on purpose: querying the PC's or the Wi-Fi's own resolver too
    early makes them cache "does not exist" (DNS_PROBE_FINISHED_NXDOMAIN) for a while."""
    for url in (f"https://1.1.1.1/dns-query?name={host}&type=A", f"https://8.8.8.8/resolve?name={host}&type=A"):
        try:
            req = urllib.request.Request(url, headers={"accept": "application/dns-json"})
            data = json.loads(urllib.request.urlopen(req, timeout=4).read())
            if data.get("Status") != 0 or not any(a.get("type") == 1 for a in data.get("Answer", [])):
                return False
        except Exception:
            return False
    return True


def wait_for_dns(host: str, timeout_s: float = 90) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        if dns_ready(host):
            time.sleep(3)                       # let the Wi-Fi's resolver catch up too
            return True
        time.sleep(2)
    return False


def wait_for_server(timeout_s: float = 30) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/state", timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def banner(lines: list[str]) -> None:
    width = max(len(x) for x in lines) + 4
    print("\n" + "=" * width)
    for x in lines:
        print(f"  {x}")
    print("=" * width + "\n", flush=True)


def main() -> None:
    global PORT
    ap = argparse.ArgumentParser(description="Start the FlagZero demo (server + tunnel).")
    ap.add_argument("--no-tunnel", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()
    PORT = args.port

    if port_in_use(PORT):
        print(f"Port {PORT} is already in use: a FlagZero server (or something else) is already running.\n"
              f"Close that terminal window, or on Windows run:  netstat -ano | findstr :{PORT}   then   taskkill /PID <number> /F")
        sys.exit(1)

    procs: list[subprocess.Popen] = []
    print(f"Starting the FlagZero server on port {PORT} ...", flush=True)
    procs.append(subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "flagzero.server:app", "--host", "0.0.0.0", "--port", str(PORT)],
        cwd=ROOT))
    if not wait_for_server():
        print("The server didn't start. Scroll up for the error.")
        for p in procs:
            p.terminate()
        sys.exit(1)

    local_dash = f"http://localhost:{PORT}/dashboard"
    tunnel_url: str | None = None
    if not args.no_tunnel:
        exe = find_cloudflared()
        if not exe:
            print("cloudflared isn't installed. Windows: winget install --id Cloudflare.cloudflared   "
                  "Mac: brew install cloudflared   (then reopen the terminal). Running without a tunnel.")
        else:
            print("Starting the https tunnel (takes ~5-10 s) ...", flush=True)
            tun = subprocess.Popen([exe, "tunnel", "--url", f"http://localhost:{PORT}"],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            procs.append(tun)
            found = threading.Event()

            def read_tunnel() -> None:
                nonlocal tunnel_url
                for line in tun.stdout:            # keep draining so cloudflared never blocks
                    m = TUNNEL_RE.search(line)
                    if m and not found.is_set():
                        tunnel_url = m.group(0)
                        found.set()

            threading.Thread(target=read_tunnel, daemon=True).start()
            if not found.wait(45):
                print("Couldn't get a tunnel address (no internet?). The server still runs locally.")

    local_join = f"http://localhost:{PORT}/join"
    if tunnel_url:
        (config.RESULTS_DIR).mkdir(parents=True, exist_ok=True)
        (config.RESULTS_DIR / "tunnel_url.txt").write_text(tunnel_url + "\n")
        local_join += f"?base={tunnel_url}"
        print(f"Tunnel created: {tunnel_url}\nWaiting for it to go live on the internet (usually 10-40 s) ...", flush=True)
        live = wait_for_dns(tunnel_url.removeprefix("https://"))
        banner(["FLAGZERO IS RUNNING" + ("" if live else "  (couldn't confirm the tunnel is live)"),
                "",
                f"Dashboard (this laptop):  {local_dash}",
                f"Join page / QR codes:     {local_join}",
                f"Car 17 (iPhone):          {tunnel_url}/car?car=17",
                f"Car 21 (OnePlus):         {tunnel_url}/car?car=21",
                "",
                "READY: scan the QR codes now." if live else "Wait ~30 s, then scan the QR codes.",
                "If a phone says 'site can't be reached', wait 1 minute and reload (DNS catch-up).",
                "Leave this window open. Press Ctrl+C here to stop everything."])
    else:
        banner(["FLAGZERO SERVER IS RUNNING (no tunnel)", "", f"Dashboard: {local_dash}",
                "Press Ctrl+C here to stop."])

    if not args.no_browser:
        # The laptop opens both pages via localhost, so it never has to look up the tunnel name;
        # the join page's QR codes still point the phones at the tunnel (?base=...).
        webbrowser.open(local_dash)
        if tunnel_url:
            webbrowser.open(local_join)

    try:
        while all(p.poll() is None for p in procs):
            time.sleep(1)
        print("A process exited; stopping the demo.")
    except KeyboardInterrupt:
        print("\nStopping ...")
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
