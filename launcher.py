# -*- coding: utf-8 -*-
"""START.bat이 호출하는 실행 스크립트.
한글 안내 문구를 전부 여기(Python)로 옮긴 이유: .bat 파일에 한글을 직접 쓰면
Windows 콘솔 코드페이지에 따라 명령어가 깨져서 해석되는 문제가 있어서,
.bat은 최대한 아무 내용도 없게(순수 영문) 두고 이 스크립트가 안내/설치/실행을 전담한다."""
import hashlib
import os
import platform
import re
import subprocess
import sys
import time
import threading
import webbrowser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_SETUP_MARKER = os.path.join(BASE_DIR, ".setup_done")

# 자동 업데이트(사용자 요청: "업데이트 패치로 내놓으라고 코어싱크는
# 그렇게하고있잖아" - 매번 zip 받아서 수동으로 덮어쓰는 대신, 켤 때마다
# 최신 코드인지 자동으로 확인하고 다르면 스스로 받아서 적용한다). 저장소
# 자체가 공개 저장소라(raw.githubusercontent.com 확인됨) 별도 인증 없이
# 받을 수 있다.
_REPO_OWNER = "S5636"
_REPO_NAME = "A"
_REPO_BRANCH = "claude/online-shop-margin-dashboard-uiyvbc"
_VERSION_URL = (
    f"https://raw.githubusercontent.com/{_REPO_OWNER}/{_REPO_NAME}/{_REPO_BRANCH}/BUILD_VERSION.txt"
)
_ZIP_URL = f"https://github.com/{_REPO_OWNER}/{_REPO_NAME}/archive/refs/heads/{_REPO_BRANCH}.zip"

# 절대 덮어쓰면 안 되는 사용자 데이터/세션 - Margin_Board.zip을 만들 때 쓰는
# 제외 목록과 반드시 똑같이 맞춘다(한쪽만 빼먹으면 실제 데이터가 날아가는
# 사고로 이어질 수 있음).
_UPDATE_PROTECTED_NAMES = {
    "shop_data.db", "fees_config.json", "settings.json",
    "ownerclan_profile", "ownerclan_profile_whale", "ownerclan_profile_chrome",
    ".setup_done", ".git",
}
_UPDATE_PROTECTED_PATTERN = re.compile(r"^\.stock_recalc_v\d+_done$")


def _is_update_protected(top_level_name):
    return top_level_name in _UPDATE_PROTECTED_NAMES or bool(_UPDATE_PROTECTED_PATTERN.match(top_level_name))


def _local_version():
    try:
        with open(os.path.join(BASE_DIR, "BUILD_VERSION.txt"), "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _apply_update_from(extracted_root):
    """extracted_root(압축 푼 최신 소스 폴더) 안의 파일들을 BASE_DIR 위에
    그대로 덮어쓴다 - 단, 최상위가 보호 목록에 있는 항목은 완전히 건너뛴다."""
    import shutil
    for name in os.listdir(extracted_root):
        if _is_update_protected(name):
            continue
        src = os.path.join(extracted_root, name)
        dst = os.path.join(BASE_DIR, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)


def _install_https_opener():
    """일부 윈도우 PC에서 urllib 기본 SSL 인증서 확인이 "unable to get issuer
    certificate"로 실패하는 경우가 실제로 있었다(윈도우 자체 인증서
    저장소에 중간 인증서가 빠져있는 흔한 문제로 보임) - certifi가 갖고
    있는, 더 완전한 공인 인증서 목록을 대신 쓰면 이 문제를 피할 수 있다.
    certifi는 requirements.txt에 있어서 pip install 이후에는 항상 쓸 수
    있다(그래서 이 함수는 pip install이 끝난 뒤에만 호출한다) - 혹시라도
    아직 없으면(첫 설치 과정 자체가 실패한 경우 등) 그냥 조용히 넘어가고
    기본 동작(OS 인증서 저장소)으로 되돌아간다. 인증서 확인 자체를 끄는
    것(끄면 더 쉽게 고쳐지지만 보안상 절대 안 됨)과는 다르다 - 더 완전한
    신뢰 목록으로 "제대로" 검증하는 것이다."""
    try:
        import certifi
        import ssl
        import urllib.request
        ctx = ssl.create_default_context(cafile=certifi.where())
        urllib.request.install_opener(
            urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
        )
        return True
    except Exception:
        return False


def _check_and_apply_update():
    try:
        import urllib.request
        remote_version = urllib.request.urlopen(_VERSION_URL, timeout=10).read().decode("utf-8").strip()
    except Exception as e:
        print(f"[안내] 업데이트 확인에 실패했습니다(무시하고 지금 버전으로 계속 진행합니다): {type(e).__name__}: {e}")
        return
    local_version = _local_version()
    if not remote_version or remote_version == local_version:
        return
    print(f"[안내] 새 버전을 발견했습니다 (현재: {local_version or '(없음)'} → 최신: {remote_version}) - 자동으로 업데이트합니다...")
    try:
        import tempfile
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, "update.zip")
            import urllib.request
            urllib.request.urlretrieve(_ZIP_URL, zip_path)
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(tmp)
            # GitHub 저장소 압축파일은 맨 위가 "<repo>-<branch 이름을 정리한
            # 형태>/" 폴더 하나로 감싸여 있다 - 그 폴더를 찾아서 그 "안의"
            # 내용을 기준으로 복사한다.
            extracted_root = None
            for name in os.listdir(tmp):
                full = os.path.join(tmp, name)
                if os.path.isdir(full):
                    extracted_root = full
                    break
            if extracted_root is None:
                raise RuntimeError("다운로드한 압축파일 안에서 소스 폴더를 찾지 못했습니다.")
            _apply_update_from(extracted_root)
        print("[안내] 업데이트 적용 완료.")
    except Exception as e:
        print(f"[안내] 업데이트 적용에 실패했습니다(무시하고 지금 버전으로 계속 진행합니다): {type(e).__name__}: {e}")


def _close_previous_instance(port=5000):
    """사용자 요청: "실행 파일 열면 까만 로고 창 뜨는 거, 그 전에 기존 창이
    꺼지게 해달라 - 코어싱크는 그렇게 되어있다" - 새로 실행할 때 이전에
    띄워뒀던 마진보드 서버(와 그 콘솔 창)가 아직 떠있으면 자동으로 먼저
    종료해서, 까만 콘솔 창이 여러 개 쌓이지 않고 항상 최신 창 하나만
    남게 한다. 윈도우에서만 의미가 있다.

    처음 구현은 taskkill /PID /T(자식 프로세스까지 통째로 종료)를 썼는데,
    오너클랜 로그인 세션이 들어있는 브라우저(웨일/크롬)가 이전 서버 프로세스의
    '자식'이라서 같이 죽어버렸다(사용자 지적: "마진보드 창은 안 꺼지고
    오너클랜 로그인 창만 꺼졌어? 매번 로그인을 새로 해줘야 되는 거야?").
    이번엔 psutil로 포트를 쥔 이전 서버 프로세스 자신만 콕 집어 종료하고
    (자식인 브라우저는 그대로 둬서 로그인 상태 유지), 눈에 보이는 검은 창
    자체는 그 부모(cmd.exe)를 따로 찾아서 닫는다 - 자식을 죽여도 부모 cmd
    창은 저절로 안 닫히고 "계속하려면 아무 키나..." 상태로 남기 때문이다.

    주의: 이전 창에서 STOCK/ORDER 등 자동화가 한창 진행 중이었다면 그
    작업(과 그 작업이 새로 띄운 창)은 함께 끊긴다 - 서버 프로세스를
    종료하는 거라 피할 수 없다. 다만 기존에 로그인해둔 오너클랜 브라우저
    창 자체는 이제 건드리지 않는다."""
    if platform.system() != 'Windows':
        return
    try:
        import psutil
    except ImportError:
        return
    my_pid = os.getpid()
    target_pid = None
    try:
        for conn in psutil.net_connections(kind='tcp'):
            if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port \
                    and conn.pid and conn.pid != my_pid:
                target_pid = conn.pid
                break
    except Exception:
        return
    if not target_pid:
        return
    try:
        proc = psutil.Process(target_pid)
    except Exception:
        return
    console_proc = None
    try:
        parent = proc.parent()
        if parent is not None and parent.name().lower() == 'cmd.exe':
            console_proc = parent
    except Exception:
        console_proc = None
    print("[안내] 이전에 실행 중이던 마진보드 창을 자동으로 닫습니다...")
    try:
        proc.kill()
    except Exception:
        pass
    if console_proc is not None:
        try:
            console_proc.kill()
        except Exception:
            pass
    time.sleep(1)


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

    _close_previous_instance()

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

    # 자동 업데이트 확인은 pip install 다음에 한다(순서 중요) - certifi가
    # requirements.txt에 있어서, 여기 도착한 시점엔 항상 설치가 돼있다고
    # 믿을 수 있다. 업데이트 확인을 pip install보다 먼저 하면, certifi
    # 자체가 아직 하나도 안 깔려있는 상태에서 인증서 문제를 certifi로
    # 피해보려는 코드가 돌아서 아무 의미가 없다(실제로 겪은 문제:
    # "[SSL: CERTIFICATE_VERIFY_FAILED] ... unable to get issuer
    # certificate" - 윈도우 자체 인증서 저장소에 중간 인증서가 빠져있는
    # 경우로 보임).
    _install_https_opener()
    print("업데이트를 확인하는 중...")
    _check_and_apply_update()
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
