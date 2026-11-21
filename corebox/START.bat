@echo off
cd /d "%~dp0"

rem Close any window left over from a previous run first (it may still hold
rem port 5051, which would stop this run from working). /T also kills the
rem python.exe child process, not just the cmd window itself. This window's
rem title is not set yet (set just below), so only an "old" window matches.
rem NOTE: keep this file plain ASCII only - Korean text here breaks on some
rem Windows codepages and was seen to corrupt/garble commands in testing.
rem Printed on purpose (not >nul) so a failure is visible instead of silent.
echo Checking for a previous window/server...
taskkill /F /T /FI "WINDOWTITLE eq COREBOX"
set FOUND_OLD_PORT=0
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5051 ^| findstr LISTENING') do (
    echo Found old server on port 5051, PID %%a - stopping it.
    set FOUND_OLD_PORT=1
    for /f usebackq %%p in (`powershell -NoProfile -Command "(Get-CimInstance Win32_Process -Filter 'ProcessId=%%a').ParentProcessId" 2^>nul`) do (
        taskkill /F /T /PID %%p >nul 2>nul
    )
    taskkill /F /T /PID %%a >nul 2>nul
)
if "%FOUND_OLD_PORT%"=="0" echo No old server was using port 5051.

rem An auto-restart (see the bottom of this file) re-runs this whole file
rem from the top, so by the time we get here the old server has already
rem exited on its own and the netstat check above finds nothing - without
rem this, CBX_AUTO_RESTART would get overwritten back to 0 and a browser
rem tab would pop open every single auto-restart, not just the first run.
if "%CBX_AUTO_RESTART%"=="1" (
    set CBX_WAS_RUNNING=1
) else (
    set CBX_WAS_RUNNING=%FOUND_OLD_PORT%
)
set CBX_AUTO_RESTART=0

title COREBOX

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo [ERROR] "python" command not found.
    echo.
    echo Please install Python from https://www.python.org/downloads/
    echo IMPORTANT: check "Add python.exe to PATH" during install.
    echo Then run this file again.
    echo.
    pause
    exit /b 1
)

python launcher.py

rem Exit code 42 means launcher.py found a new version on github and shut
rem itself down on purpose so it can come back up with it (no one needs to
rem touch this window). Re-running this same file from scratch (not just
rem looping back to "python launcher.py") also picks up a new START.bat if
rem that changed too, and is safer than a goto-based loop since this file
rem could get overwritten by the update while it is still running.
if %ERRORLEVEL% EQU 42 (
    echo.
    echo Restarting with the updated version...
    set CBX_AUTO_RESTART=1
    "%~f0"
    exit /b 0
)

pause
