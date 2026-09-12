@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
echo Run SETUP.bat first.
pause
exit /b 1
)
echo Starting Noema v8 CLEAN from:
cd
echo.
".venv\Scripts\python.exe" bot.py
pause
