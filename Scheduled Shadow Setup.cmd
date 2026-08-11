@echo off
setlocal
cd /d "%~dp0"

if /I "%~1"=="install" goto install
if /I "%~1"=="status" goto status
if /I "%~1"=="remove" goto remove
if not "%~1"=="" goto usage

echo GRANDE Alpha scheduled live shadow
echo.
echo No task changes occur until you explicitly choose Install or Remove.
echo [I] Install or refresh the 6:20 AM weekday task
echo [S] Show current status
echo [R] Remove the task
choice /C ISR /N /M "Choose I, S, or R: "
if errorlevel 3 goto remove
if errorlevel 2 goto status
if errorlevel 1 goto install

:install
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0manage-shadow-schedule.ps1" -Install
goto done

:status
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0manage-shadow-schedule.ps1" -Status
goto done

:remove
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0manage-shadow-schedule.ps1" -Remove
goto done

:usage
echo Usage: "%~nx0" [install^|status^|remove]
exit /b 2

:done
set "GRANDE_EXIT=%ERRORLEVEL%"
echo.
if not "%GRANDE_EXIT%"=="0" echo Scheduled-shadow setup found a blocker. Read the message above.
pause
exit /b %GRANDE_EXIT%
