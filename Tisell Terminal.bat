@echo off
REM Double-clickable launcher for Windows. Explorer can run this from any
REM working directory, so everything is resolved relative to the script.
cd /d "%~dp0"
title Tisell Terminal

set "UI=http://127.0.0.1:8501"
set "API=http://127.0.0.1:8000"

echo Tisell Terminal
echo ===============

REM Only skip startup when BOTH halves answer. A half-up state (a dying
REM process still holding a port) must start fresh, or the browser opens
REM onto nothing.
curl -s -m 2 "%UI%/_stcore/health" >nul 2>&1
set "UIUP=%errorlevel%"
curl -s -m 2 "%API%/health" >nul 2>&1
set "APIUP=%errorlevel%"

if "%UIUP%"=="0" if "%APIUP%"=="0" (
    echo Already running. Opening %UI%
    start "" "%UI%"
    echo.
    echo You can close this window.
    timeout /t 3 >nul
    exit /b 0
)

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo ERROR: the virtual environment is missing.
    echo Set it up by running these two lines in this folder:
    echo     python -m venv .venv
    echo     .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    echo.
    echo ERROR: .env is missing - the API keys live there.
    echo Copy .env.example to .env and fill in your keys.
    echo.
    pause
    exit /b 1
)

echo Starting... your browser opens on its own in a few seconds.
echo KEEP THIS WINDOW OPEN - closing it stops the terminal.
echo.
".venv\Scripts\python.exe" run.py

echo.
echo Stopped.
pause
