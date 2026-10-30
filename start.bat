@echo off
setlocal
cd /d "%~dp0"
title tiku-studio

rem ---------- 1. 找 Python ----------
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [1/3] 首次运行：创建虚拟环境...
  where py >nul 2>nul && (py -3.13 -m venv .venv 2>nul || py -3 -m venv .venv) || (python -m venv .venv)
)
if not exist "%PY%" (
  echo [错误] 未找到 Python 3。请到 https://www.python.org/downloads/ 安装，
  echo        安装时勾选 "Add python.exe to PATH"，然后重新运行本脚本。
  pause & exit /b 1
)

rem ---------- 2. 装后端依赖 ----------
if not exist ".venv\.deps-ok" (
  echo [2/3] 安装后端依赖（首次约 1~2 分钟）...
  "%PY%" -m pip install -q -r apps\api\requirements.txt && type nul > ".venv\.deps-ok"
)

rem ---------- 3. 构建前端（有 Node 就构建，没有也能用旧产物） ----------
if not exist "apps\web\dist\index.html" (
  where npm >nul 2>nul && (
    echo [3/3] 构建前端...
    pushd apps\web && call npm install --no-fund --no-audit && call npm run build && popd
  ) || (
    echo [提示] 未安装 Node.js，跳过前端构建。请到 https://nodejs.org 安装后重跑。
  )
)

echo.
echo   ============================================
echo     tiku-studio 启动中：http://localhost:8100
echo     （平板/手机：浏览器打开 电脑IP:8100）
echo     关闭本窗口即退出服务
echo   ============================================
echo.
"%PY%" -m uvicorn app.main:app --app-dir apps\api --host 0.0.0.0 --port 8100
pause
