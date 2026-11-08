@echo off
chcp 65001 >nul
title 更新并打包 tiku-studio 独立版
cd /d "%~dp0"
echo ============================================================
echo   tiku-studio 一键打包（前端 + exe + zip 到桌面）
echo   过程中不要关闭窗口，约 3-6 分钟
echo ============================================================
echo.
".venv\Scripts\python.exe" pack.py %*
if errorlevel 1 goto fail
echo.
echo 打包完成，正在打开输出目录...
start "" "dist-app\tiku-studio"
echo.
pause
exit /b 0

:fail
echo.
echo [失败] 打包过程中出错，请把上面的红色信息发给我
pause
exit /b 1
