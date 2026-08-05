# -*- coding: utf-8 -*-
"""START.bat이 호출하는 실행 스크립트.
한글 안내 문구를 전부 여기(Python)로 옮긴 이유: .bat 파일에 한글을 직접 쓰면
Windows 콘솔 코드페이지에 따라 명령어가 깨져서 해석되는 문제가 있어서,
.bat은 최대한 아무 내용도 없게(순수 영문) 두고 이 스크립트가 안내/설치/실행을 전담한다."""
import hashlib
import os
import subprocess
import sys
import time
import threading
import webbrowser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_SETUP_MARKER = os.path.join(BASE_DIR, ".setup_done")


def open_browser_later():
    time.sleep(3)
    try:
        webbrowser.open("http://127.0.0.1:5000/")
    except Exception:
        pass


def _requirements_hash():
    try:
        with open(os.path.join(BASE_DIR, "requirements.txt"), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return ""


def main():
    print("============================================")
    print("  이유상점 Margin Board")
    print("============================================")
    print()

    # pip install/playwright install을 매번 무조건 실행하면, pip이 로컬에 이미
    # 다 깔려있어도 매번 네트워크로 버전을 다시 확인하느라 시간이 걸린다
    # (특히 학교/회사 네트워크처럼 느린 환경에서 부팅이 오래 걸린다는 지적을
    # 받음). requirements.txt 내용이 지난번 설치 성공 때와 똑같으면(해시 비교)
    # 이번엔 설치 단계를 통째로 건너뛴다 - requirements.txt가 바뀐 업데이트를
    # 받았을 때만 다시 설치가 돈다.
    current_hash = _requirements_hash()
    already_done = False
    try:
        with open(_SETUP_MARKER, "r", encoding="utf-8") as f:
            already_done = f.read().strip() == current_hash and bool(current_hash)
    except Exception:
        already_done = False

    if already_done:
        print("필요한 프로그램이 이미 설치되어 있어서 설치 단계를 건너뜁니다.")
    else:
        print("필요한 프로그램을 확인/설치하는 중입니다... (requirements.txt가 바뀐 뒤 처음 실행할 때만 시간이 걸려요)")
        result = subprocess.run([sys.executable, "-m", "pip", "install",
                                  "--disable-pip-version-check", "-r", "requirements.txt"])
        if result.returncode != 0:
            print()
            print("[오류] 설치 중 문제가 발생했습니다. 위 내용을 캡처해서 보내주세요.")
            return False

        print("오너클랜 자동 수집에 쓰는 브라우저를 확인/설치하는 중입니다... (처음 한 번만 시간이 걸려요)")
        pw_result = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"])
        if pw_result.returncode != 0:
            print("[안내] 브라우저 설치에 실패했습니다 - 오너클랜 자동 수집만 안 될 뿐, 나머지 기능은 정상 동작합니다.")

        try:
            with open(_SETUP_MARKER, "w", encoding="utf-8") as f:
                f.write(current_hash)
        except Exception:
            pass

    print()
    print("설치 완료. 서버를 시작합니다.")
    print("잠시 후 브라우저가 자동으로 열립니다. (안 열리면 http://127.0.0.1:5000 직접 접속하세요)")
    print()
    print("이 창을 닫으면 프로그램이 종료됩니다. 계속 이 창을 켜두세요.")
    print("============================================")
    print()

    threading.Thread(target=open_browser_later, daemon=True).start()

    import app  # requirements 설치가 끝난 뒤에 import (미리 하면 설치 전에 실패함)
    app.app.run(host="127.0.0.1", port=5000, debug=False)
    return True


if __name__ == "__main__":
    ok = True
    try:
        ok = main()
    except Exception:
        ok = False
        print()
        print("============================================")
        print("[오류] 실행 중 문제가 발생했습니다:")
        print("============================================")
        import traceback
        traceback.print_exc()

    print()
    print("============================================")
    if ok:
        print("서버가 종료되었습니다.")
    else:
        print("서버 실행에 실패했습니다. 위 내용을 캡처해서 보내주세요.")
    print("============================================")
    input("아무 키나 누르면 창이 닫힙니다...")
