@echo off
rem Starts the trading screen: stops a running instance, starts it with --lan
rem (the phone link is sent to Telegram) and opens the browser.
rem This window IS the server: closing it stops the screen.
title graham app
cd /d "%~dp0"

for /f "tokens=5" %%p in ('netstat -ano ^| findstr LISTENING ^| findstr ":5000 "') do (
    echo Stopping previous instance, PID %%p
    taskkill /PID %%p /F >nul 2>&1
)

start "" /min cmd /c "timeout /t 5 /nobreak >nul & start http://127.0.0.1:5000/"

set PYTHONIOENCODING=utf-8
python app.py --lan

echo.
echo The server stopped.
pause
