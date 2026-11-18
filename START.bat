@echo off
cd /d "%~dp0"

rem Close any window left over from a previous run first. It may still hold
rem port 5000, which would stop this run from working. This window's title
rem is not set yet - set just below - so only an "old" window matches.
rem IMPORTANT: do NOT use /T anywhere in here. /T would also kill the
rem OwnerClan browser window a previous run may have opened and logged in -
rem that browser is a child of the old python.exe, and /T kills the whole
rem child tree too. This was tried before and it killed the logged-in
rem browser along with the old window, forcing a re-login every restart.
rem The WINDOWTITLE filter below only works for a plain cmd.exe window - it
rem does not reliably match when running inside Windows Terminal (its real
rem OS-level window title does not necessarily match an inner tab's title),
rem so a previous window could survive even when this prints a success-
rem looking message. The port-based PID lookup below, and killing that
rem PID's parent process too (so the actual tab/window it lives in closes,
rem not just the python.exe inside it), is what actually closes it either way.
rem NOTE: keep this file plain ASCII only, with NO parentheses in any echoed
rem or commented text. Korean text breaks on some Windows codepages, and an
rem unescaped parenthesis inside a for/if block's body closes that block
rem early and breaks the whole script - both were seen to corrupt/garble
rem commands or stop this file from running at all in testing.
echo Checking for a previous window/server...
taskkill /F /FI "WINDOWTITLE eq Margin Board"
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5000 ^| findstr LISTENING') do (
    echo Found old server on port 5000, PID %%a - stopping it. Browser windows are left alone.
    for /f usebackq %%p in (`powershell -NoProfile -Command "(Get-CimInstance Win32_Process -Filter 'ProcessId=%%a').ParentProcessId" 2^>nul`) do (
        taskkill /F /PID %%p >nul 2>nul
    )
    taskkill /F /PID %%a >nul 2>nul
)

title Margin Board

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo [ERROR] python command not found.
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
