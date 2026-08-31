@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m rtt_app.action_api
pause
