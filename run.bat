@echo off
cd /d "%~dp0"
echo Lancement de Vinted Alerts...
echo Interface: http://127.0.0.1:8787
echo.
if "%VINTED_ALERTS_ADMIN_USERNAME%"=="" set "VINTED_ALERTS_ADMIN_USERNAME=admin"
if "%VINTED_ALERTS_ADMIN_PASSWORD%"=="" set "VINTED_ALERTS_ADMIN_PASSWORD=admin123"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8787 .*LISTENING"') do (
  taskkill /F /PID %%P >nul 2>&1
)
python app.py
echo.
echo Application arretee.
pause
