@echo off
cd /d "%~dp0"
set PIDFILE=app\data\server.pid
if exist "%PIDFILE%" (
  set /p SPID=<"%PIDFILE%"
  echo 正在停止 PID %SPID% …
  taskkill /PID %SPID% /F >nul 2>&1
  del "%PIDFILE%" >nul 2>&1
  echo 完成。
  timeout /t 2 >nul
  exit /b 0
)
echo 没找到 pidfile，尝试按端口 8848 停止…
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8848 ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1
echo 完成。
timeout /t 2 >nul
