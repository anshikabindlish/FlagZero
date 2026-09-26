#!/bin/bash
# FlagZero one-click demo (Mac): server + https tunnel, opens the dashboard and QR join page.
cd "$(dirname "$0")"
python3 -m flagzero.tools.start_demo "$@"
