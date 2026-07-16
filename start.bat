@echo off
cd /d "%~dp0"
if "%VINTED_ALERTS_ADMIN_USERNAME%"=="" set "VINTED_ALERTS_ADMIN_USERNAME=admin"
set "VINTED_ALERTS_PORT=8790"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8790 .*LISTENING"') do (
  taskkill /F /PID %%P >nul 2>&1
)
python app.py
pause
