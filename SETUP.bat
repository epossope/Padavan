@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
if not exist ".env" copy /Y ".env.example" ".env" >nul
echo.
echo SETUP DONE
echo Build v8-CLEAN-2026-09-03
echo.
notepad ".env"
pause
exit /b 0
:fail
echo SETUP FAILED
pause
exit /b 1
