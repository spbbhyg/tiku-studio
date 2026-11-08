@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title tiku-studio

echo ============================================
echo   tiku-studio 题库工作台
echo ============================================
echo.

set "PY=.venv\Scripts\python.exe"
set "BASEPY="

rem ---------- 1. 找一个 Python 3.11+ ----------
call :try_py "py -3.13"
call :try_py "py -3.12"
call :try_py "py -3"
call :try_py "python"
if not defined BASEPY goto :no_python

for /f "delims=" %%V in ('%BASEPY% -c "import sys;print(sys.version.split()[0])" 2^>nul') do set "PYVER=%%V"
if not defined PYVER set "PYVER=3.x"
echo [1/4] Python %PYVER%  OK

rem ---------- 2. 校验 .venv（缺包 / ABI 不匹配都会自愈） ----------
set "DEPS=fastapi, uvicorn, fitz, PIL, docx, httpx, rapidocr_onnxruntime, jinja2"
set "VENV_OK="
set "VENV_PARTIAL="
if exist "%PY%" (
  "%PY%" -c "import %DEPS%" >nul 2>nul
  if not errorlevel 1 set "VENV_OK=1"
  "%PY%" -c "import fastapi" >nul 2>nul
  if not errorlevel 1 set "VENV_PARTIAL=1"
)
if defined VENV_OK goto :venv_ready

if defined VENV_PARTIAL (
  echo [2/4] 运行环境缺少部分依赖，正在补齐 ...
  "%PY%" -m pip install -r apps\api\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
  "%PY%" -c "import %DEPS%" >nul 2>nul
  if not errorlevel 1 goto :venv_ready
  echo [提示] 补齐失败，改为整体重建 ...
)

echo [2/4] 运行环境缺失或与 Python 版本不匹配，正在重建 .venv ...
if exist ".venv" rmdir /s /q ".venv"
%BASEPY% -m venv .venv
if errorlevel 1 goto :venv_fail
if not exist "%PY%" goto :venv_fail
"%PY%" -m pip --version >nul 2>nul
if errorlevel 1 (
  echo [提示] .venv 里没有 pip，正在修复 ...
  "%PY%" -m ensurepip --upgrade
)
"%PY%" -m pip --version >nul 2>nul
if errorlevel 1 goto :venv_fail
echo [2/4] 正在下载依赖，第一次安装需要几分钟，请不要关闭这个窗口 ...
"%PY%" -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
"%PY%" -m pip install -r apps\api\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
  echo [提示] 清华镜像安装失败，改用官方源重试 ...
  "%PY%" -m pip install -r apps\api\requirements.txt
)
"%PY%" -c "import %DEPS%" >nul 2>nul
if errorlevel 1 goto :venv_fail
> ".venv\.deps-ok" echo %PYVER%
echo [2/4] 环境已重建  OK
goto :venv_done

:venv_ready
echo [2/4] 运行环境  OK

:venv_done
rem ---------- 3. 前端构建产物 ----------
if exist "apps\web\dist\index.html" goto :dist_ready
echo [3/4] 首次运行，正在构建前端，需要 Node.js 22 ...
where npm >nul 2>nul
if errorlevel 1 goto :no_node
pushd apps\web
call npm install --no-fund --no-audit
if errorlevel 1 goto :build_fail
call npm run build
if errorlevel 1 goto :build_fail
popd
if not exist "apps\web\dist\index.html" goto :build_fail
echo [3/4] 前端已构建  OK
goto :start

:dist_ready
echo [3/4] 前端产物  OK

:start
rem ---------- 4. 起服务 ----------
netstat -ano | findstr ":8100" | findstr /i "LISTENING" >nul 2>nul
if not errorlevel 1 (
  echo [4/4] 服务已在运行，直接打开浏览器。
  if not defined TIKU_NO_BROWSER start "" "http://127.0.0.1:8100/"
  goto :done
)
if not exist "data" mkdir data
echo [4/4] 启动服务：http://127.0.0.1:8100/
echo 关闭这个窗口即可停止服务。
echo.
if not defined TIKU_NO_BROWSER start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process 'http://127.0.0.1:8100/'"
"%PY%" -m uvicorn app.main:app --app-dir apps\api --host 0.0.0.0 --port 8100
set "CODE=%ERRORLEVEL%"
echo.
echo 服务已退出，代码 %CODE%。
echo 如果打不开，请运行「检查环境.bat」，把窗口内容发给开发者。
goto :done

:no_python
echo [错误] 没有找到 Python 3.11 或更高版本。
echo 请安装 Python 3.13：https://www.python.org/downloads/
echo 安装时务必勾选 Add python.exe to PATH，然后重新双击 start.bat。
goto :fail

:venv_fail
echo [错误] Python 依赖安装失败，请检查网络后重试。
echo 也可以先运行「检查环境.bat」看看缺什么。
goto :fail

:no_node
echo [错误] 没有找到 Node.js / npm，无法构建前端。
echo 请安装 Node.js 22：https://nodejs.org/zh-cn/download
echo 或者先在有网络的电脑上构建好 apps\web\dist 再拷过来。
goto :fail

:build_fail
popd
echo [错误] 前端构建失败，请检查 npm 网络或 Node.js 版本。
goto :fail

:fail
echo.
echo 启动失败。请把上面的完整内容发给开发者。
echo.

:done
echo.
if not defined TIKU_NO_PAUSE pause
endlocal
goto :eof

:try_py
if defined BASEPY goto :eof
%~1 -c "import sys;raise SystemExit(0 if sys.version_info>=(3,11) else 1)" >nul 2>nul
if not errorlevel 1 set "BASEPY=%~1"
goto :eof
