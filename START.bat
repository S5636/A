@echo off
cd /d "%~dp0"

rem Close any window left over from a previous run first. It may still hold
rem port 5000, which would stop this run from working. This window's title
rem is not set yet - set just below - so only an "old" window matches.
rem Mirrors the user's other proven-working script (Card Benefit Checker's
rem START.bat) exactly, /T included - a no-/T version (kill python.exe and
rem its parent individually, leaving the tree intact) did not actually
rem close the old window/tab in testing, while /T does. /T also kills the
rem OwnerClan browser window if a previous run had opened and logged into
rem one (it is a descendant of the old python.exe) - accepted tradeoff:
rem an occasional extra re-login beats old windows piling up every restart.
rem NOTE: keep this file plain ASCII only, with NO parentheses in any echoed
rem or commented text. Korean text breaks on some Windows codepages, and an
rem unescaped parenthesis inside a for/if block's body closes that block
rem early and breaks the whole script - both were seen to corrupt/garble
rem commands or stop this file from running at all in testing.
echo Checking for a previous window/server...
taskkill /F /T /FI "WINDOWTITLE eq Margin Board"
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5000 ^| findstr LISTENING') do (
    echo Found old server on port 5000, PID %%a - stopping it.
    for /f usebackq %%p in (`powershell -NoProfile -Command "(Get-CimInstance Win32_Process -Filter 'ProcessId=%%a').ParentProcessId" 2^>nul`) do (
        taskkill /F /T /PID %%p >nul 2>nul
    )
    taskkill /F /T /PID %%a >nul 2>nul
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

rem launcher.py checks GitHub every 2 minutes while running and exits with
rem code 42 the moment it finds a newer version, instead of applying the
rem update live inside a running process (so every running thread, pywinauto
rem window handle, etc. just restarts clean from scratch, and we don't need
rem anything fragile like os.execv just to swap the running code - exec does
rem not preserve the process on Windows the way it does on Unix and caused a
rem total silent lockup when tried before). This loop just restarts python
rem whenever that exact exit code comes back, so the server effectively never
rem needs a manual START.bat press again. Any other exit code (0, a crash,
rem ctrl+c) falls through to the final pause below and stops the loop.
:run_server
python launcher.py
if %errorlevel% equ 42 (
    echo.
    echo New version found - restarting automatically...
    echo.
    goto run_server
)

pause
