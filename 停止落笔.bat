@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 停止落笔

echo.
echo   正在停止落笔（端口 8848）...

set "FOUND="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8848" ^| findstr "LISTENING"') do (
  taskkill /F /PID %%a >nul 2>nul
  set "FOUND=1"
)

if defined FOUND (
  echo   已停止。
) else (
  echo   没有在运行，不用停。
)
echo.
timeout /t 2 >nul
