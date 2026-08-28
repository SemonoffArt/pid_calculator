@echo off
cd /d "%~dp0"
set "PID_DEBUG=0"
uv sync
uv run python app.py
pause
