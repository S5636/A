@echo off
cd /d "%~dp0"

rem Close any window left over from a previous run first. It may still hold
rem port 5000, which would stop this run from working. This window's title
rem is not set yet - set just below - so only an "old" window matches.
rem IMPORTANT: do NOT use /T here. /T would also kill the OwnerClan browser
rem window a previous run may have opened and logged in - that browser is a
rem child of the old python.exe, and /T kills the whole child tree too.
rem This was tried before and it killed the logged-in browser along with
rem the old window, forcing a re-login every restart.
rem NOTE: keep this file plain ASCII only, with NO parentheses in any echoed
rem or commented text. Korean text breaks on some Windows codepages, and an
rem unescaped parenthesis inside a for/if block's body closes that block
rem early and breaks the whole script - both were seen to corrupt/garble
rem commands or stop this file from running at all in testing.
echo Checking for a previous window/server...
taskkill /F /FI "WINDOWTITLE eq Margin Board"
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5000 ^| findstr LISTENING') do (
    echo Found old server on port 5000, PID %%a - stopping it. Browser windows are left alone.
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
