@echo off
setlocal
cd /d "%~dp0"

if /I "%~1"=="install" goto install
if /I "%~1"=="start" goto start
if /I "%~1"=="stop" goto stop
if /I "%~1"=="restart" goto restart
if /I "%~1"=="status" goto status
if /I "%~1"=="remove" goto remove
if not "%~1"=="" goto usage

echo GRANDE Alpha scheduled live shadow
echo.
echo No task changes occur until you explicitly choose an action.
echo [I] Install or refresh the 6:20 AM weekday task
echo [A] Start the installed task now
echo [T] Stop the exact owned scheduled runtime
echo [X] Restart the exact owned scheduled runtime
echo [S] Show current status
echo [R] Remove the task
choice /C IATXSR /N /M "Choose I, A, T, X, S, or R: "
if errorlevel 6 goto remove
if errorlevel 5 goto status
if errorlevel 4 goto restart
if errorlevel 3 goto stop
if errorlevel 2 goto start
if errorlevel 1 goto install

:install
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0manage-shadow-schedule.ps1" -Install
goto done

:start
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0manage-shadow-schedule.ps1" -Start
goto done

:stop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0manage-shadow-schedule.ps1" -Stop
goto done

:restart
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0manage-shadow-schedule.ps1" -Restart
goto done

:status
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0manage-shadow-schedule.ps1" -Status
goto done

:remove
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0manage-shadow-schedule.ps1" -Remove
goto done

:usage
echo Usage: "%~nx0" [install^|start^|stop^|restart^|status^|remove]
exit /b 2

:done
set "GRANDE_EXIT=%ERRORLEVEL%"
echo.
if not "%GRANDE_EXIT%"=="0" echo Scheduled-shadow setup found a blocker. Read the message above.
pause
exit /b %GRANDE_EXIT%
