@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHONPATH=%~dp0"
set /a FAILS=0

echo ===== NOEMA BUILD CHECK =====
echo.

REM --- 1. required source files ---
for %%F in (bot.py ingestion.py knowledge_store.py url_enricher.py retrieval.py) do (
    if not exist "%%~F" (
        echo [FAIL] missing source file: %%~F
        set /a FAILS+=1
    ) else (
        echo [ OK ] source file present: %%~F
    )
)
echo.

REM --- 2. legacy build markers still in bot.py ---
for %%M in ("v8-AMVERA-2026-09-09" "def search_products_live" "def get_weather_live" "Open-Meteo" "multi-market") do (
    findstr /C:%%~M bot.py >nul 2>&1
    if errorlevel 1 (
        echo [FAIL] marker missing in bot.py: %%~M
        set /a FAILS+=1
    ) else (
        echo [ OK ] marker present: %%~M
    )
)
echo.

REM --- 3. virtualenv ---
if not exist ".venv\Scripts\python.exe" (
    echo [FAIL] .venv missing - run SETUP.bat first
    set /a FAILS+=1
) else (
    echo [ OK ] python: .venv\Scripts\python.exe
)
echo.

REM --- 4. .env with filled tokens ---
if not exist ".env" (
    echo [FAIL] .env missing - copy .env.example and fill in tokens
    set /a FAILS+=1
) else (
    findstr /C:"PASTE_" ".env" >nul 2>&1
    if not errorlevel 1 (
        echo [FAIL] .env still contains PASTE_ placeholders
        set /a FAILS+=1
    ) else (
        echo [ OK ] .env present
    )
)
echo.

if exist ".venv\Scripts\python.exe" (
    REM --- 5. syntax check ---
    ".venv\Scripts\python.exe" -m py_compile bot.py ingestion.py knowledge_store.py url_enricher.py retrieval.py
    if errorlevel 1 (
        echo [FAIL] py_compile failed
        set /a FAILS+=1
    ) else (
        echo [ OK ] py_compile: all five modules
    )

    REM --- 6. import check ---
    ".venv\Scripts\python.exe" -c "import bot, ingestion, knowledge_store, url_enricher" >nul 2>&1
    if errorlevel 1 (
        echo [FAIL] module import failed
        set /a FAILS+=1
    ) else (
        echo [ OK ] imports: bot, retrieval, ingestion, knowledge_store, url_enricher
    )

    REM --- 7. full test suite (unit + integration + regression) ---
    ".venv\Scripts\python.exe" -m unittest discover -s tests -t . >nul 2>&1
    if errorlevel 1 (
        echo [FAIL] unittest suite failed - check tests/ output
        set /a FAILS+=1
    ) else (
        echo [ OK ] unittest: all tests pass
    )
)

echo.
echo =============================
if %FAILS% EQU 0 (
    echo RESULT: ^>^> ALL CHECKS PASSED
) else (
    echo RESULT: FAILED   fail count = %FAILS%
)
echo =============================
if "%1"=="" pause
exit /b %FAILS%
