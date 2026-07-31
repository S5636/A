@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 이유마켓 CEO Margin Pro

echo ============================================
echo   이유마켓 CEO Margin Pro
echo ============================================
echo.
echo Python 설치 확인 중...
where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo [오류] python 명령을 찾을 수 없습니다.
    echo https://www.python.org/downloads/ 에서 설치한 뒤 다시 실행해주세요.
    echo 설치 화면에서 반드시 "Add python.exe to PATH"에 체크하세요.
    echo.
    echo 창을 닫지 말고 이 내용을 캡처해서 보내주세요.
    pause
    exit /b 1
)

python --version
echo.
echo (위에 "Python 3.x.x" 형태의 버전이 안 보이고 다른 내용이 나온다면,
echo  Microsoft Store의 python이 아니라 python.org에서 설치한 python이 필요합니다)
echo.

echo 필요한 프로그램을 확인/설치하는 중입니다... (처음 실행할 때만 시간이 좀 걸려요)
python -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo.
    echo [오류] 설치 중 문제가 발생했습니다. 위 내용을 캡처해서 보내주세요.
    echo.
    pause
    exit /b 1
)

echo.
echo 설치 완료. 서버를 시작합니다.
echo 잠시 후 브라우저가 자동으로 열립니다. (안 열리면 http://127.0.0.1:5000 직접 접속)
echo.
echo ※ 이 창을 닫으면 프로그램이 종료됩니다. 계속 이 창을 켜두세요.
echo ============================================
echo.

start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process 'http://127.0.0.1:5000/'"

python app.py

echo.
echo ============================================
echo 서버가 종료되었습니다.
echo 위에 빨간 글씨나 오류(Error/Traceback)가 보이면 그 내용을 캡처해서 보내주세요.
echo ============================================
pause
