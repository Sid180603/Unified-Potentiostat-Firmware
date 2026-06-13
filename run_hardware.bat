@echo off
REM Unified Potentiostat — Hardware Mode (Windows)
REM Runs Flask against a real COM port (no socat, no mock)

cd /d "%~dp0"
set POTENTIOSTAT_DEV=0
set FLASK_DEBUG=0

echo Starting Flask in production mode...
echo Connect your Seeeduino XIAO via USB.
echo Select the COM port in the browser UI.
echo.

ui\.venv\Scripts\python ui\app.py
