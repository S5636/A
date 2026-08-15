# -*- coding: utf-8 -*-
"""START.bat이 호출하는 실행 스크립트.

같은 와이파이에 있는 PC와 폰이 같이 접속할 수 있도록 0.0.0.0으로 서버를 열고,
폰에서 접속할 때 쓸 이 PC의 사설 IP 주소를 화면에 안내한다.
"""
import hashlib
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_SETUP_MARKER = os.path.join(BASE_DIR, ".setup_done")
PORT = 5050


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def open_browser_later():
    time.sleep(2)
    try:
        webbrowser.open(f"http://127.0.0.1:{PORT}/")
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
    print("  카드 남은혜택 체크")
    print("============================================")
    print()

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
        print("필요한 프로그램을 확인/설치하는 중입니다...")
        result = subprocess.run([sys.executable, "-m", "pip", "install",
                                  "--disable-pip-version-check", "-r", "requirements.txt"])
        if result.returncode != 0:
            print()
            print("[오류] 설치 중 문제가 발생했습니다. 위 내용을 캡처해서 보내주세요.")
            return False
        try:
            with open(_SETUP_MARKER, "w", encoding="utf-8") as f:
                f.write(current_hash)
        except Exception:
            pass

    ip = local_ip()
    print()
    print("설치 완료. 서버를 시작합니다.")
    print(f"이 PC에서 볼 때:   http://127.0.0.1:{PORT}")
    print(f"폰에서 볼 때(같은 와이파이):   http://{ip}:{PORT}")
    print()
    print("이 창을 닫으면 프로그램이 종료됩니다. 계속 이 창을 켜두세요.")
    print("============================================")
    print()

    threading.Thread(target=open_browser_later, daemon=True).start()

    import app  # requirements 설치가 끝난 뒤에 import
    app.app.run(host="0.0.0.0", port=PORT, debug=False)
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
