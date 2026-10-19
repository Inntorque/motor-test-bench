@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (python scripts\workbench.py start) else (py -3 scripts\workbench.py start)
if errorlevel 1 pause
