@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 落笔 · 求职前置

echo.
echo   ============================================
echo     落笔 · 求职前置  v1.4
echo   ============================================
echo.

rem -- 找真 python：必须能真的跑 -c，排掉微软商店那个假 python
set "PY="
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python -c "import sys" >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo   [!] 没找到可用的 Python。
  echo       请装 Python 3.10 以上，安装时勾选 "Add Python to PATH"。
  echo       https://www.python.org/downloads/
  echo.
  echo       装好后还这样，多半是微软商店的假 python 抢在前面：
  echo       设置 - 应用 - 应用执行别名，把 python.exe / python3.exe 关掉。
  echo.
  pause
  exit /b 1
)

rem -- 交给 start.py：等服务真起来再开浏览器，已在跑就不重复启
%PY% start.py 8848

if errorlevel 1 (
  echo.
  echo   [!] 没能启动。上面的报错截图发我。
  echo       临时可换端口：%PY% start.py 8899
  echo.
  pause
)
