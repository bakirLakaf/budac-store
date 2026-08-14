@echo off
REM ============================================================
REM  Get YouCan Access Token (OAuth)
REM  All text here is ASCII only - Arabic breaks .bat files.
REM ============================================================

title YouCan Token Helper

echo ============================================================
echo   YouCan Access Token Helper
echo ============================================================
echo.

REM ---------- 1) Check Python ----------
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    echo         Install Python 3.11+ and enable "Add to PATH".
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo [OK] %%v

REM ---------- 2) Ask for port ----------
echo.
set /p PORT=Enter callback port [default 8123]: 
if "%PORT%"=="" set PORT=8123
echo [INFO] Using port %PORT%
echo.
echo [IMPORTANT] In partners.youcan.shop, the app's
echo             "Allowed redirect URLs" must contain exactly:
echo.
echo             http://localhost:%PORT%/callback
echo.
pause

REM ---------- 3) Install requirements ----------
echo.
echo [INFO] Installing requirements...
python -m pip install --quiet requests python-dotenv
if errorlevel 1 (
    echo [ERROR] Failed to install requirements.
    pause
    exit /b 1
)
echo [OK] Requirements ready.

REM ---------- 4) Run ----------
echo.
echo ============================================================
set YOUCAN_CALLBACK_PORT=%PORT%
python get_youcan_token.py

echo.
echo ============================================================
echo   Done. Check youcan_token.txt for the token.
echo ============================================================
pause
