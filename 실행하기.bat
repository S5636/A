@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 이유마켓 CEO Margin Pro - 준비 중

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [오류] Python이 설치되어 있지 않습니다.
    echo.
    echo https://www.python.org/downloads/ 에서 Python을 설치한 뒤 다시 실행해주세요.
    echo 설치 화면에서 반드시 "Add python.exe to PATH"에 체크하세요.
    echo.
    pause
    exit /b 1
)

echo 필요한 프로그램을 확인/설치하는 중입니다... (처음 실행할 때만 시간이 좀 걸려요)
python -m pip install --quiet --disable-pip-version-check -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo [오류] 설치 중 문제가 발생했습니다. 인터넷 연결을 확인한 뒤 다시 실행해주세요.
    pause
    exit /b 1
)

echo 서버를 시작합니다...
start "이유마켓 CEO Margin Pro (이 창을 닫으면 프로그램이 종료됩니다)" cmd /k python app.py

timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:5000/

exit
