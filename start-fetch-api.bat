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
if "%VINTED_FETCH_API_RESTART_EXISTING%"=="" set "VINTED_FETCH_API_RESTART_EXISTING=true"
set "PYTHONUNBUFFERED=1"

set "SHOULD_RESTART_EXISTING="
if /I "%VINTED_FETCH_API_RESTART_EXISTING%"=="true" set "SHOULD_RESTART_EXISTING=1"
if /I "%VINTED_FETCH_API_RESTART_EXISTING%"=="yes" set "SHOULD_RESTART_EXISTING=1"
if /I "%VINTED_FETCH_API_RESTART_EXISTING%"=="on" set "SHOULD_RESTART_EXISTING=1"
if "%VINTED_FETCH_API_RESTART_EXISTING%"=="1" set "SHOULD_RESTART_EXISTING=1"

if defined SHOULD_RESTART_EXISTING (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$port=[int]$env:VINTED_FETCH_API_PORT; $listeners=Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; foreach ($id in $listeners) { $proc=Get-Process -Id $id -ErrorAction SilentlyContinue; if ($proc) { Write-Host ('Port ' + $port + ' deja utilise par PID ' + $id + ' (' + $proc.ProcessName + '), arret...'); try { Stop-Process -Id $id -Force -ErrorAction Stop; Write-Host ('PID ' + $id + ' arrete.') } catch { Write-Host ('Impossible d''arreter PID ' + $id + ': ' + $_.Exception.Message) } } }"
)

python -u vinted_fetch_api.py
if errorlevel 1 (
  echo.
  echo Le service API s'est arrete avec une erreur.
  pause
)
