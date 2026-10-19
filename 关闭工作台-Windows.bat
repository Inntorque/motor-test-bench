@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "runtime\python\python.exe" (
  "runtime\python\python.exe" -X utf8 scripts\workbench.py stop
  if errorlevel 1 pause
  exit /b
)
where py >nul 2>nul
if errorlevel 1 (python scripts\workbench.py stop) else (py -3 scripts\workbench.py stop)
if errorlevel 1 pause
