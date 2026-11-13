@echo off
cd /d "%~dp0"

rem 전에 띄워놨던 창이 아직 떠있으면(포트를 그 창이 잡고 있어서 새로
rem 켜도 제대로 안 되거나 창만 계속 쌓임) 먼저 닫는다 - title로 창을
rem 닫고, 혹시 창은 닫혔는데 프로세스만 남아있는 경우를 대비해 포트
rem 5050을 쓰고 있는 프로세스도 같이 끝낸다. 지금 이 창은 아직 title을
rem 안 정했으니(바로 아래 줄에서 정함) 여기선 "예전" 창만 걸린다.
rem /T(트리킬)까지 줘야 창(cmd)만 죽고 그 안에서 돌던 python.exe는
rem 안 죽는 경우를 막을 수 있음 - /T 없으면 자식 프로세스가 창 없이
rem 백그라운드에 그대로 남아서 포트를 계속 잡고 있을 수 있음.
taskkill /F /T /FI "WINDOWTITLE eq Card Benefit Checker" >nul 2>nul
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5050 ^| findstr LISTENING') do (
    taskkill /F /T /PID %%a >nul 2>nul
)

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
