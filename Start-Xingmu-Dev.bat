@echo off
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
  echo Python 3 was not found. Install Python and FFmpeg to test this development version.
  pause
  exit /b 1
)
python production\server.py
pause
