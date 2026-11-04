@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title tiku-studio 一键安装并启动

echo ============================================
echo   tiku-studio 一键安装并启动
echo ============================================
echo.

set "BASEPY="
call :try_py "py -3.13"
call :try_py "py -3.12"
call :try_py "py -3"
call :try_py "python"
if defined BASEPY goto :py_ok

echo 没有检测到 Python 3.11+，开始自动安装 Python 3.13.9 ...
set "PYEXE=%TEMP%\python-3.13.9-amd64.exe"
call :download "https://mirrors.tuna.tsinghua.edu.cn/python/3.13.9/python-3.13.9-amd64.exe" "%PYEXE%"
if not exist "%PYEXE%" call :download "https://mirrors.huaweicloud.com/python/3.13.9/python-3.13.9-amd64.exe" "%PYEXE%"
if not exist "%PYEXE%" call :download "https://www.python.org/ftp/python/3.13.9/python-3.13.9-amd64.exe" "%PYEXE%"
if not exist "%PYEXE%" goto :py_fail
"%PYEXE%" /quiet InstallAllUsers=1 PrependPath=1 Include_launcher=1 Include_test=0
set "PATH=C:\Program Files\Python313;C:\Program Files\Python313\Scripts;%PATH%"
call :try_py "py -3.13"
call :try_py "C:\Program Files\Python313\python.exe"
if not defined BASEPY goto :py_fail
echo Python 已安装。

:py_ok
if exist "apps\web\dist\index.html" goto :node_ok
where npm >nul 2>nul
if not errorlevel 1 goto :node_ok
echo 没有检测到 Node.js，开始自动安装 Node 22.20.0 ...
set "NODEMSI=%TEMP%\node-v22.20.0-x64.msi"
call :download "https://cdn.npmmirror.com/binaries/node/v22.20.0/node-v22.20.0-x64.msi" "%NODEMSI%"
if not exist "%NODEMSI%" call :download "https://registry.npmmirror.com/-/binary/node/v22.20.0/node-v22.20.0-x64.msi" "%NODEMSI%"
if not exist "%NODEMSI%" goto :node_fail
msiexec /i "%NODEMSI%" /qn /norestart
set "PATH=C:\Program Files\nodejs;%PATH%"
where npm >nul 2>nul
if errorlevel 1 goto :node_fail
echo Node.js 已安装。

:node_ok
echo 交给 start.bat 完成环境检查、前端构建与启动 ...
echo.
call "%~dp0start.bat"
exit /b 0

:download
if exist "%~2" del /q "%~2" >nul 2>nul
where curl >nul 2>nul
if errorlevel 1 goto :download_ps
curl -L --fail --retry 2 -o "%~2" "%~1"
if exist "%~2" goto :eof
:download_ps
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri '%~1' -OutFile '%~2' -UseBasicParsing } catch { exit 1 }"
goto :eof

:py_fail
echo [错误] Python 自动安装失败，请手动安装 Python 3.13 后重新运行本文件。
pause
exit /b 1

:node_fail
echo [错误] Node.js 自动安装失败，请手动安装 Node.js 22 后重新运行本文件。
pause
exit /b 1

:try_py
if defined BASEPY goto :eof
%~1 -c "import sys;raise SystemExit(0 if sys.version_info>=(3,11) else 1)" >nul 2>nul
if not errorlevel 1 set "BASEPY=%~1"
goto :eof
