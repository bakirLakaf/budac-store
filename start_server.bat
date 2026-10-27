@echo off
REM ============================================================
REM  BUDAC Store - local development launcher (Windows)
REM  ASCII only. Arabic messages live in the Python code.
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo   BUDAC Store - local launcher
echo ============================================================
echo.

REM ---------- 1. Check Python ----------
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found in PATH.
    echo         Install Python 3.11+ from https://www.python.org/downloads/
    echo         and tick "Add Python to PATH" during setup.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python !PYVER! found.

REM ---------- 2. Virtual environment ----------
if not exist ".venv\Scripts\activate.bat" (
    echo [..] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Could not create the virtual environment.
        pause
        exit /b 1
    )
)
call ".venv\Scripts\activate.bat"
echo [OK] Virtual environment active.

REM ---------- 3. Dependencies ----------
echo [..] Installing/updating requirements ^(quiet^)...
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] Dependency installation failed. See the messages above.
    pause
    exit /b 1
)
echo [OK] Dependencies ready.

REM ---------- 4. .env file ----------
if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" >nul
        echo [WARN] Created .env from .env.example - open it and fill the values.
    )
)

REM ---------- 5. SECRET_KEY ----------
REM The app refuses to boot in production without SECRET_KEY.
REM For local runs we set FLASK_ENV=development, which allows a dev key.
set FLASK_ENV=development
echo [OK] FLASK_ENV=development ^(local mode^)

REM ---------- 6. Port ----------
set "PORT="
set /p PORT="Port to run on [default 5000]: "
if "!PORT!"=="" set PORT=5000
echo [OK] Using port !PORT!

REM ---------- 7. Database setup / migrations ----------
echo [..] Running startup checks and migrations...
python startup.py
if errorlevel 1 (
    echo [ERROR] Startup failed. Read the message above.
    pause
    exit /b 1
)

REM ---------- 8. Run ----------
echo.
echo ============================================================
echo   Server starting: http://127.0.0.1:!PORT!
echo   Admin panel:     http://127.0.0.1:!PORT!/admin/login
echo   Press CTRL+C to stop.
echo ============================================================
echo.
python -c "import os; from app import create_app; create_app().run(host='127.0.0.1', port=int(os.environ.get('PORT', 5000)), debug=True)"

pause
endlocal
