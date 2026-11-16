@echo off
cd /d "%~dp0"

rem Close any window left over from a previous run first (it may still hold
rem port 5050, which would stop this run from working). /T also kills the
rem python.exe child process, not just the cmd window itself. This window's
rem title is not set yet (set just below), so only an "old" window matches.
rem NOTE: keep this file plain ASCII only - Korean text here breaks on some
rem Windows codepages and was seen to corrupt/garble commands in testing.
rem Printed on purpose (not >nul) so a failure is visible instead of silent.
echo Checking for a previous window/server...
taskkill /F /T /FI "WINDOWTITLE eq Card Benefit Checker"
set FOUND_OLD_PORT=0
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5050 ^| findstr LISTENING') do (
    echo Found old server on port 5050, PID %%a - stopping it.
    set FOUND_OLD_PORT=1
    rem Also stop the window/tab that owns this process (its parent), not
    rem just the python.exe process itself - otherwise, under Windows
    rem Terminal, the old tab/window can be left open with nothing running
    rem in it instead of actually closing.
    for /f usebackq %%p in (`powershell -NoProfile -Command "(Get-CimInstance Win32_Process -Filter 'ProcessId=%%a').ParentProcessId" 2^>nul`) do (
        taskkill /F /T /PID %%p >nul 2>nul
    )
    rem The parent kill above usually already takes this PID down with it
    rem (it was its child), so this is just a safety net for when that
    rem fails - the "could not find process" message when it's already
    rem gone is expected and harmless, so it's hidden here instead of
    rem printed as if it were a real failure.
    taskkill /F /T /PID %%a >nul 2>nul
)
if "%FOUND_OLD_PORT%"=="0" echo No old server was using port 5050.

rem Tell launcher.py whether an old server was actually running, so it can
rem skip auto-opening yet another browser tab on top of the one that is
rem presumably already open from before (this is a restart, not a first
rem launch).
set CBC_WAS_RUNNING=%FOUND_OLD_PORT%

title Card Benefit Checker

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

pause
