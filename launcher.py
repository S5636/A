# -*- coding: utf-8 -*-
"""START.bat이 호출하는 실행 스크립트.
한글 안내 문구를 전부 여기(Python)로 옮긴 이유: .bat 파일에 한글을 직접 쓰면
Windows 콘솔 코드페이지에 따라 명령어가 깨져서 해석되는 문제가 있어서,
.bat은 최대한 아무 내용도 없게(순수 영문) 두고 이 스크립트가 안내/설치/실행을 전담한다."""
import hashlib
import os
import platform
import subprocess
import sys
import time
import threading
import uuid
import webbrowser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_SETUP_MARKER = os.path.join(BASE_DIR, ".setup_done")
_DEELEVATE_FLAG = "--deelevate-attempted"


def _is_admin():
    if platform.system() != "Windows":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _relaunch_without_admin():
    """마진보드가 관리자 권한(Administrator)으로 실행 중이면, 일반 권한
    프로세스로 자기 자신을 다시 띄우고 지금 이(관리자 권한) 프로세스는
    끝낸다. 관리자 권한 프로세스가 띄운 크로미움 계열 브라우저(웨일/크롬)는
    스스로 샌드박스를 끄면서(--no-sandbox) 불안정해지는 문제가 있고,
    실제로 웨일 자동화 창이 이것 때문으로 보이는 TargetClosedError로 계속
    실패했다. 사용자는 관리자 권한으로 실행한 적이 없다고 확인했으므로
    (UAC가 꺼져있거나, 바로가기/실행파일에 저장된 호환성 설정 등 정확한
    원인은 PC마다 다를 수 있음), 원인을 사용자가 직접 찾아 고치게 하는
    대신 코드가 알아서 감지해서 항상 일반 권한으로 강제 전환한다.
    작업 스케줄러(schtasks)로 RunLevel을 LIMITED(일반 권한)로 지정한
    임시 작업을 만들어 실행하는 방식을 쓴다 - 설치 프로그램들이 관리자
    권한 설치 후 일반 권한으로 재시작할 때 흔히 쓰는 표준적인 방법이다.
    --deelevate-attempted 플래그로 한 번 시도했는데도 여전히 관리자
    권한이면(이 PC 계정 자체가 항상 관리자 권한으로 동작하는 경우 등)
    무한 재실행에 빠지지 않도록 더 시도하지 않는다."""
    if _DEELEVATE_FLAG in sys.argv:
        print("[안내] 일반 권한으로 다시 실행을 시도했지만 여전히 관리자 권한입니다 - "
              "이 PC 계정/설정 자체가 항상 관리자 권한으로 동작하는 것 같습니다. 그대로 계속 진행합니다.")
        return False
    print("[안내] 마진보드가 관리자 권한(Administrator)으로 실행되고 있습니다.")
    print("[안내] 크로미움 계열 브라우저 자동화가 불안정해지는 걸 막기 위해, 일반 권한으로 자동으로 다시 실행합니다...")
    print("[안내] (이 창은 자동으로 닫히고, 일반 권한으로 새 창이 뜹니다)")
    script_path = os.path.abspath(__file__)
    cmd_parts = [sys.executable, script_path] + sys.argv[1:] + [_DEELEVATE_FLAG]
    quoted = " ".join(f'"{a}"' for a in cmd_parts)
    task_name = f"MarginBoardDeElevate_{uuid.uuid4().hex[:8]}"
    try:
        subprocess.run(
            ["schtasks", "/Create", "/TN", task_name, "/TR", quoted, "/SC", "ONCE",
             "/ST", "00:00", "/RL", "LIMITED", "/F"],
            check=True, capture_output=True,
        )
        subprocess.run(["schtasks", "/Run", "/TN", task_name], check=True, capture_output=True)
        time.sleep(2)
        subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"], capture_output=True)
        return True
    except Exception as e:
        print(f"[안내] 일반 권한 재실행 시도가 실패했습니다({type(e).__name__}) - 그대로 관리자 권한인 채로 계속 진행합니다.")
        return False


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
    # threaded=True가 꼭 필요하다 - DPJ 자동화 요청 하나가 몇 분씩 걸릴 수
    # 있는데, 기본값(단일 스레드)이면 그동안 서버가 다른 어떤 요청도(진행
    # 상황 조회는 물론 평범한 페이지 새로고침까지) 전혀 처리 못 해서 화면이
    # 완전히 멈춘 것처럼 보였다. pywinauto/COM을 다루는 실제 자동화 작업은
    # 어차피 항상 별도로 새로 만든 전용 스레드에서만 돌아가게 이미 분리해뒀기
    # 때문에(dapalza_auto.collect_and_upload 참고), Flask 쪽 스레드 개수가
    # 늘어나도 그 COM 안전성에는 영향이 없다.
    app.app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
    return True


if __name__ == "__main__":
    if _is_admin() and _relaunch_without_admin():
        sys.exit(0)

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
