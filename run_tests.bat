@echo off
REM ============================================================
REM  BUDAC Store - run the test suite (Windows)
REM  ASCII only.
REM ============================================================
setlocal
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    pause
    exit /b 1
)

if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"

echo [..] Installing test requirements...
python -m pip install -r requirements.txt --quiet

echo.
echo ============================================================
echo   Running the test suite
echo ============================================================
python -m pytest tests/ -v

echo.
echo ============================================================
echo   Dependency vulnerability scan
echo ============================================================
python -m pip install pip-audit --quiet
python -m pip_audit -r requirements.txt 2>nul || pip-audit -r requirements.txt

pause
endlocal
