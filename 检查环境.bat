@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title tiku-studio 环境检查

echo ============================================
echo   tiku-studio 环境检查
echo ============================================
echo.

set "BAD=0"
set "BASEPY="
call :try_py "py -3.13"
call :try_py "py -3.12"
call :try_py "py -3"
call :try_py "python"

if defined BASEPY (
  for /f "delims=" %%V in ('%BASEPY% -c "import sys;print(sys.version.split()[0])" 2^>nul') do echo [OK] Python %%V
) else (
  echo [错误] 没有 Python 3.11 或更高版本
  echo        请运行「一键安装并启动.bat」自动安装
  set "BAD=1"
)

if not exist ".venv\Scripts\python.exe" (
  echo [警告] 还没有 .venv，双击 start.bat 会自动创建
) else (
  ".venv\Scripts\python.exe" -c "import fastapi, uvicorn, fitz, PIL, docx, httpx, rapidocr_onnxruntime" >nul 2>nul
  if errorlevel 1 (
    echo [错误] .venv 依赖缺失，或与当前 Python 版本不匹配
    echo        双击 start.bat 会自动重建，不需要手动删目录
    ".venv\Scripts\python.exe" -c "import sys;print('        venv Python', sys.version.split()[0])" 2>nul
    set "BAD=1"
  ) else (
    echo [OK] .venv 依赖完整
  )
)

if exist "apps\web\dist\index.html" (
  echo [OK] 前端已构建
) else (
  where npm >nul 2>nul
  if errorlevel 1 (
    echo [警告] 没有前端构建产物，也没有 npm
    echo        请运行「一键安装并启动.bat」
  ) else (
    echo [提示] 首次运行 start.bat 会自动构建前端
  )
)

netstat -ano | findstr ":8100" | findstr /i "LISTENING" >nul 2>nul
if errorlevel 1 (
  echo [提示] 8100 端口空闲，服务当前没有运行
) else (
  echo [OK] 8100 端口有服务在监听
)

echo.
if "%BAD%"=="0" (
  echo 结论：环境正常，可以双击 start.bat。
) else (
  echo 结论：有问题，请看上面的错误项。
)
echo.
pause
exit /b 0

:try_py
if defined BASEPY goto :eof
%~1 -c "import sys;raise SystemExit(0 if sys.version_info>=(3,11) else 1)" >nul 2>nul
if not errorlevel 1 set "BASEPY=%~1"
goto :eof
