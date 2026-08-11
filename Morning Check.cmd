@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0morning-check.ps1"
set "GRANDE_EXIT=%ERRORLEVEL%"
echo.
if not "%GRANDE_EXIT%"=="0" echo Morning check found a blocker. Read the message above before opening GRANDE Alpha.
pause
exit /b %GRANDE_EXIT%
