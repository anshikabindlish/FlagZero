@echo off
rem FlagZero one-click demo: server + https tunnel, opens the dashboard and QR join page.
cd /d "%~dp0"
py -m flagzero.tools.start_demo %*
pause
