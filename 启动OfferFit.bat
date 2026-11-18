@echo off
cd /d "%~dp0"
py -3 start.py 2>nul || python start.py
if errorlevel 1 pause
