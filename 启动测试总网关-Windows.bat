@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "runtime\python\python.exe" (
  echo 请下载并完整解压 Inntorque-Local-Services-Windows-x64.zip。
  pause
  exit /b 1
)
"runtime\python\python.exe" -X utf8 scripts\gateway.py start
if errorlevel 1 pause
