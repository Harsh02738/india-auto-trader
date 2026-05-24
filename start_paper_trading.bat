@echo off
title India Auto-Trader — Paper Trading
cd /d "%~dp0"

echo ============================================================
echo   INDIA AUTO-TRADER — PAPER TRADING MODE
echo ============================================================
echo.
echo Starting all services...
echo.

REM 1. FastAPI Backend
start "FastAPI Backend" cmd /k "cd /d "%~dp0" && echo [1/3] FastAPI Backend starting... && python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000"

timeout /t 3 /nobreak >nul

REM 2. Trade Engine
start "Trade Engine" cmd /k "cd /d "%~dp0" && echo [2/3] Trade Engine starting... && python -m backend.trade_engine"

timeout /t 2 /nobreak >nul

REM 3. Frontend
start "Frontend (Next.js)" cmd /k "cd /d "%~dp0\frontend" && echo [3/3] Frontend starting... && npm run dev"

timeout /t 5 /nobreak >nul

REM Open browser
start "" "http://localhost:3000/intraday"

echo.
echo ============================================================
echo   All services launched in separate windows.
echo   Browser opening: http://localhost:3000/intraday
echo ============================================================
echo.
echo ============================================================
echo   MORNING SCAN — Running via Claude Code...
echo ============================================================
echo.

claude -p "/morning-scan"

echo.
echo ============================================================
echo   Morning scan complete. Press any key to close.
echo ============================================================
pause >nul
