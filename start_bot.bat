@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Budac Social Publisher

set PORT=5000
set /p PORT=Enter port [default 5000]: 
if "%PORT%"=="" set PORT=5000

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python was not found. Install Python 3.10+ and tick "Add to PATH".
  pause
  exit /b 1
)

if not exist .env (
  copy .env.example .env >nul
  echo [INFO] .env was created from .env.example. Fill it in, then run this file again.
  notepad .env
  pause
  exit /b 0
)

echo [INFO] Installing requirements...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Failed to install requirements.
  pause
  exit /b 1
)

echo [INFO] Starting on port %PORT% ...
python run_standalone.py
pause
