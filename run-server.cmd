@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "data" mkdir "data"
echo [%date% %time%] start >> "data\server.log"
".venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir apps\api --host 0.0.0.0 --port 8100 >> "data\server.log" 2>&1
echo [%date% %time%] exited code %ERRORLEVEL% >> "data\server.log"
