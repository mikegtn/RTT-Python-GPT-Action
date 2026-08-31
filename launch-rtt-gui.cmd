@echo off
setlocal
cd /d "%~dp0"
py -3.12 -m rtt_app.gui 2>nul || python -m rtt_app.gui
if errorlevel 1 pause
