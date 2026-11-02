@echo off
chcp 65001 >nul
cd /d "%~dp0"
"runtime\python\python.exe" -X utf8 scripts\gateway.py stop
if errorlevel 1 pause
