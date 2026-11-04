@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "data" mkdir data
echo.>> data\server.log
echo [%DATE% %TIME%] start tiku-studio>> data\server.log
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" goto :bad
"%PY%" -c "import fastapi, uvicorn, fitz, PIL, docx, httpx, rapidocr_onnxruntime" >nul 2>nul
if errorlevel 1 goto :bad
"%PY%" -m uvicorn app.main:app --app-dir apps\api --host 0.0.0.0 --port 8100 >> data\server.log 2>&1
echo [%DATE% %TIME%] exited with %ERRORLEVEL%>> data\server.log
exit /b 0

:bad
echo [%DATE% %TIME%] .venv missing or broken - run start.bat to rebuild>> data\server.log
exit /b 1
