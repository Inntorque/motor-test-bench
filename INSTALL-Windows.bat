@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "runtime\python\python.exe" (
  echo Please download and fully extract the Windows release ZIP first.
  pause
  exit /b 1
)
"runtime\python\python.exe" -X utf8 scripts\install_windows.py
if errorlevel 1 (
  echo Installation did not complete. See the message above.
  pause
  exit /b 1
)
pause
