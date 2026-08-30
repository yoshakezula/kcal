@echo off
REM Starts the Kcal server in the background, then opens it full-screen
REM in Chrome kiosk mode. Put a shortcut to this file in your Startup
REM folder (Win+R, shell:startup) to auto-launch on boot.

cd /d "%~dp0"

start "" /min pythonw app.py

REM Give the server a moment to start before opening the browser.
timeout /t 2 /nobreak >nul

REM Chrome kiosk mode. If Chrome isn't on your PATH, replace "chrome"
REM below with the full path, e.g.:
REM   "C:\Program Files\Google\Chrome\Application\chrome.exe"
start "" chrome --kiosk --app=http://127.0.0.1:5000

REM Edge alternative (comment out the Chrome line above and uncomment this):
REM start "" msedge --kiosk --app=http://127.0.0.1:5000
