@echo off
cd /d "%~dp0"

if "%VINTED_FETCH_API_TOKEN%"=="" (
  echo Definis VINTED_FETCH_API_TOKEN avant de lancer ce service.
  pause
  exit /b 1
)

if "%VINTED_FETCH_API_HOST%"=="" set "VINTED_FETCH_API_HOST=127.0.0.1"
if "%VINTED_FETCH_API_PORT%"=="" set "VINTED_FETCH_API_PORT=8797"

python vinted_fetch_api.py
if errorlevel 1 (
  echo.
  echo Le service API s'est arrete avec une erreur.
  pause
)
