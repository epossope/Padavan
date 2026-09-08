@echo off
setlocal
cd /d "%~dp0"
echo ===== NOEMA BUILD CHECK =====
findstr /C:"BUILD_ID = \"v8-CLEAN-2026-09-03\"" bot.py
findstr /C:"def search_products_live" bot.py
findstr /C:"def get_weather_live" bot.py
findstr /C:"Open-Meteo" bot.py
findstr /C:"multi-market" bot.py
echo =============================
pause
