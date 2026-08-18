@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher not found. Install Python 3.11+ from python.org.
  pause
  exit /b 1
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
  where winget >nul 2>nul
  if errorlevel 1 (
    echo FFmpeg not found. Install FFmpeg and add it to PATH.
    pause
    exit /b 1
  )
  winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements
  where ffmpeg >nul 2>nul
  if errorlevel 1 (
    echo FFmpeg was installed, but the current terminal has not refreshed PATH yet.
    echo Close this window and run setup_windows.bat again.
    pause
    exit /b 0
  )
)
if not exist .venv py -3 -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if not exist .env copy .env.example .env >nul
python main.py --self-test
if errorlevel 1 (
  echo Self-test failed.
  pause
  exit /b 1
)
echo.
echo Setup complete. Edit .env and then run run_windows.bat
pause
