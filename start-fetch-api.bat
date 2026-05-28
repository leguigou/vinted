@echo off
setlocal
cd /d "%~dp0"

if exist "fetch-api.env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in ("fetch-api.env") do (
    if not "%%A"=="" set "%%A=%%B"
  )
) else (
  echo Fichier fetch-api.env introuvable. Tu peux copier fetch-api.env.example puis renseigner le token.
  echo.
)

if "%VINTED_FETCH_API_TOKEN%"=="" (
  echo Definis VINTED_FETCH_API_TOKEN avant de lancer ce service.
  pause
  exit /b 1
)

if "%VINTED_FETCH_API_HOST%"=="" set "VINTED_FETCH_API_HOST=127.0.0.1"
if "%VINTED_FETCH_API_PORT%"=="" set "VINTED_FETCH_API_PORT=8797"
if "%VINTED_FETCH_API_LOG_PATH%"=="" set "VINTED_FETCH_API_LOG_PATH=%~dp0fetch-api.log"
set "PYTHONUNBUFFERED=1"

python -u vinted_fetch_api.py
if errorlevel 1 (
  echo.
  echo Le service API s'est arrete avec une erreur.
  pause
)
