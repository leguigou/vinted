@echo off
cd /d "%~dp0"
if "%VINTED_ALERTS_ADMIN_USERNAME%"=="" set "VINTED_ALERTS_ADMIN_USERNAME=admin"
if "%VINTED_ALERTS_ADMIN_PASSWORD%"=="" set "VINTED_ALERTS_ADMIN_PASSWORD=admin123"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8787 .*LISTENING"') do (
  taskkill /F /PID %%P >nul 2>&1
)
python app.py
pause
