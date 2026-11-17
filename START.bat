@echo off
cd /d "%~dp0"

rem Close any window left over from a previous run first (it may still hold
rem port 5000, which would stop this run from working). This window's title
rem is not set yet (set just below), so only an "old" window matches.
rem IMPORTANT: do NOT use /T here. /T would also kill the OwnerClan browser
rem window a previous run may have opened and logged in - that browser is a
rem child of the old python.exe, and /T kills the whole child tree too
rem (this was tried before and it killed the logged-in browser along with
rem the old window, forcing a re-login every restart).
rem NOTE: keep this file plain ASCII only - Korean text here breaks on some
rem Windows codepages and can corrupt/garble commands.
echo Checking for a previous window/server...
taskkill /F /FI "WINDOWTITLE eq Margin Board"
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5000 ^| findstr LISTENING') do (
    echo Found old server on port 5000, PID %%a - stopping it (browser windows are left alone).
    taskkill /F /PID %%a >nul 2>nul
)

title Margin Board

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
