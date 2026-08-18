@echo off
rem AI Council — one-click startup (Windows)
rem Opens the API server and the UI server in their own windows,
rem then opens the app in your browser.

cd /d "%~dp0"

start "AI Council - API" cmd /k ".venv\Scripts\uvicorn council.api.main:app --port 8000"
start "AI Council - UI" cmd /k "cd frontend && npm run dev"

rem Give the servers a moment, then open the app
timeout /t 8 /nobreak >nul
start http://localhost:3000

echo AI Council is starting - two server windows opened.
echo Close those windows (or Ctrl+C in them) to stop.
timeout /t 5 >nul
