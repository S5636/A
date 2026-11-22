# -*- coding: utf-8 -*-
"""START.bat이 호출하는 실행 스크립트.
한글 안내 문구를 전부 여기(Python)로 옮긴 이유: .bat 파일에 한글을 직접 쓰면
Windows 콘솔 코드페이지에 따라 명령어가 깨져서 해석되는 문제가 있어서,
.bat은 최대한 아무 내용도 없게(순수 영문) 두고 이 스크립트가 안내/설치/실행을 전담한다."""
import hashlib
import os
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
    ".admin_browser_profile", ".admin_browser_profile_whale", ".admin_browser_profile_chrome",
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


def _close_previous_instances():
    """이전에 떠있던 마진보드 창(과 그 콘솔)을 전부 찾아서 닫는다.

    START.bat의 taskkill(WINDOWTITLE/포트)만으로는 과거 여러 번의
    실행에서 남은 고아 창들이 계속 쌓이는 문제가 있었다(사용자 확인:
    "왜 아직도 로그 못끄는거야" - 하루 전 세션까지 화면에 그대로
    남아있었음). taskkill의 포트 기준 탐색은 "지금 포트 5000을 쥔
    딱 하나"만 찾는데, 그보다 먼저 떠 있다가 이미 포트를 다른 고아
    프로세스에게 넘겨준 더 오래된 고아들은 전혀 못 찾는다. 여기서는
    포트가 아니라 "python.exe이면서 이 launcher.py 폴더에서 돌고 있는"
    모든 프로세스를 psutil로 직접 찾아서, 그 프로세스 자신과 그
    부모(콘솔을 쥔 cmd 등)를 kill한다 - 자식(오너클랜 브라우저 등)은
    건드리지 않는다(kill()은 그 프로세스 자신만 종료하고 자식까지
    내려가지 않는다 - /T처럼 트리 전체를 죽이는 게 아니다).

    처음 버전은 명령줄의 'launcher.py' 인자를 os.path.abspath()로
    풀어서 비교했는데, 그 함수는 "이 프로세스(마진보드)의" 현재
    폴더를 기준으로 상대경로를 풀어낸다 - 상대경로 'launcher.py'를
    쓰는 다른 프로그램(카드 남은혜택 체크도 똑같이 python launcher.py
    로 실행됨)의 명령줄을 마진보드 자신의 폴더 기준으로 잘못 풀어서
    마진보드 자신의 launcher.py 경로와 우연히 똑같아져버렸다 - 그래서
    전혀 다른 프로그램인 카드 남은혜택 체크까지 같이 꺼버렸다(사용자
    지적: "코어싱크 로그 킬해버리네"). 상대경로는 "그 프로세스 자신의"
    현재 폴더를 기준으로 풀어야 하므로, 대상 프로세스의 실제 cwd()를
    직접 물어봐서(psutil이 OS에서 그 프로세스의 진짜 현재 폴더를
    가져옴) 이 마진보드 폴더(BASE_DIR)와 같은지로 비교한다."""
    try:
        import psutil
    except ImportError:
        return
    my_pid = os.getpid()
    this_dir = os.path.normcase(os.path.normpath(BASE_DIR))
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.pid == my_pid:
                continue
            name = (proc.info.get('name') or '').lower()
            if name != 'python.exe':
                continue
            cmdline = proc.info.get('cmdline') or []
            if not any(os.path.basename(arg) == 'launcher.py' for arg in cmdline if arg):
                continue
            try:
                proc_cwd = os.path.normcase(os.path.normpath(proc.cwd()))
            except Exception:
                continue
            if proc_cwd != this_dir:
                continue
        except Exception:
            continue
        print(f"[안내] 이전에 떠있던 마진보드 프로세스를 정리합니다(PID {proc.pid})...")
        try:
            parent = proc.parent()
        except Exception:
            parent = None
        try:
            proc.kill()
        except Exception:
            pass
        if parent is not None:
            try:
                parent.kill()
            except Exception:
                pass


# 재시작마다 새 탭이 계속 쌓이던 문제(사용자 지적: "나는 탭 정리해주길
# 바라는데") - 오너클랜 자동화 창과 똑같은 방식(Playwright + CDP 디버그
# 포트)으로 마진보드 전용 창을 하나만 띄우고, 다음 실행부터는 그 창에
# 그대로 재접속해서 새로고침만 한다. 오너클랜 쪽과 마찬가지로, 이 창은
# 재시작(START.bat의 강제 taskkill)에서 일부러 안 닫는다 - 그래야 고아
# 프로세스로 살아남아서 다음 실행이 CDP로 이어받을 수 있다.
_ADMIN_CDP_PORT = 17392
_ADMIN_WHALE_PROFILE_DIR = os.path.join(BASE_DIR, ".admin_browser_profile_whale")
_ADMIN_CHROME_PROFILE_DIR = os.path.join(BASE_DIR, ".admin_browser_profile_chrome")
_ADMIN_BROWSER_PROFILE_DIR = os.path.join(BASE_DIR, ".admin_browser_profile")


def _describe_admin_browser_process():
    """17392 포트를 디버그 포트로 쥐고 있는 프로세스가 있으면 그 실행파일
    경로를 알려준다 - "크롬으로 켜지는데 왜?" 같은 걸 다음부턴 로그만 보고
    바로 원인을 알 수 있게(재접속이 옛날 크롬 고아를 그대로 이어받은
    건지, 아예 새로 웨일/크롬 중 뭘로 띄운 건지 구분하기 위함)."""
    try:
        import psutil
    except ImportError:
        return "(psutil 없음)"
    port_flag = f"--remote-debugging-port={_ADMIN_CDP_PORT}"
    for proc in psutil.process_iter(['pid', 'name', 'exe', 'cmdline']):
        try:
            cmdline = proc.info.get('cmdline') or []
            if any(port_flag in (arg or '') for arg in cmdline):
                return f"{proc.info.get('name')} (PID {proc.pid}, {proc.info.get('exe')})"
        except Exception:
            continue
    return "(해당 포트를 쥔 프로세스를 못 찾았음)"


def _try_reconnect_admin_browser(url):
    pw = None
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{_ADMIN_CDP_PORT}", timeout=5000)
        if not browser.contexts:
            browser.close()
            pw.stop()
            return False
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        try:
            page.bring_to_front()
        except Exception:
            pass
        print(f"[안내] 이전에 열어둔 마진보드 창을 그대로 새로고침했습니다 (새 탭을 또 열지 않습니다). "
              f"재사용한 프로세스: {_describe_admin_browser_process()}")
        return True
    except Exception as e:
        print(f"[안내] 마진보드 전용 창 재접속 실패({type(e).__name__}) - 새로 하나 엽니다.")
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass
        return False


def _launch_admin_browser(url):
    """마진보드 전용 창을 새로 띄운다. Playwright 내장 크로미움을 그냥 쓰면
    사용자가 평소 쓰던 브라우저(웨일)가 아니라 전혀 다른 크롬 창이 떠버린다
    (사용자 지적: "왜 마진보드가 크롬으로 열리냐 웨일로 열리던 게") - 오너클랜
    자동화 창을 고를 때 쓰는 것과 똑같은 순서(웨일 -> 실제 설치된 크롬 ->
    그래도 없으면 내장 크로미움)로 실행파일을 찾아서 그걸로 띄운다."""
    try:
        from playwright.sync_api import sync_playwright
        exe_path = None
        profile_dir = _ADMIN_BROWSER_PROFILE_DIR
        try:
            from ownerclan_auto import _find_whale_executable, _find_chrome_executable
            whale_path = _find_whale_executable()
            if whale_path:
                exe_path, profile_dir = whale_path, _ADMIN_WHALE_PROFILE_DIR
                print(f"[안내] 웨일을 찾았습니다: {whale_path} - 이걸로 마진보드 창을 엽니다.")
            else:
                chrome_path = _find_chrome_executable()
                if chrome_path:
                    exe_path, profile_dir = chrome_path, _ADMIN_CHROME_PROFILE_DIR
                    print(f"[안내] 웨일을 못 찾아서 크롬으로 엽니다: {chrome_path}")
                else:
                    print("[안내] 웨일/크롬 실행파일을 둘 다 못 찾았습니다 - 내장 크로미움으로 엽니다.")
        except Exception as e:
            exe_path, profile_dir = None, _ADMIN_BROWSER_PROFILE_DIR
            print(f"[안내] 웨일/크롬 탐색 중 오류({type(e).__name__}) - 내장 크로미움으로 엽니다.")
        os.makedirs(profile_dir, exist_ok=True)
        pw = sync_playwright().start()
        launch_kwargs = dict(
            headless=False,
            args=[f"--remote-debugging-port={_ADMIN_CDP_PORT}"],
            no_viewport=True,
        )
        if exe_path:
            launch_kwargs["executable_path"] = exe_path
        context = pw.chromium.launch_persistent_context(profile_dir, **launch_kwargs)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        return True
    except Exception as e:
        print(f"[안내] 마진보드 창을 자동으로 여는 데 실패했습니다({type(e).__name__}) - 브라우저에서 직접 {url} 로 접속해주세요.")
        return False


def open_browser_later():
    time.sleep(3)
    url = "http://127.0.0.1:5000/"
    if _try_reconnect_admin_browser(url):
        return
    if _launch_admin_browser(url):
        return
    # playwright가 아예 없거나 위 둘 다 실패한 경우의 최후 안전망
    try:
        webbrowser.open(url)
    except Exception:
        pass


_AUTO_RESTART_EXIT_CODE = 42
_UPDATE_POLL_INTERVAL_SECONDS = 120


def _periodic_update_check_loop():
    """서버가 떠 있는 동안에도 2분마다 최신 버전인지 계속 확인한다(사용자
    요청: "서버 켜져 있는 동안 2분마다 반복해서 확인" - 매번 START.bat을
    직접 눌러줘야 했던 걸 없애기 위함). 여기서는 실제로 받아서 적용하지
    않는다 - 버전이 다르다는 것만 확인되면 바로 종료코드 42로 끝내고,
    START.bat이 그 종료코드를 보고 launcher.py를 다시 실행한다. 다시
    실행된 launcher.py가 시작할 때 원래 하던 _check_and_apply_update()가
    실제 다운로드/적용을 그대로 수행하므로 로직을 두 번 만들 필요가 없다.

    os.execv로 "그 자리에서" 교체하는 방법은 일부러 안 쓴다 - 윈도우에서는
    진짜 유닉스 exec처럼 PID를 유지하며 교체되는 게 아니라 새 프로세스를
    spawn하고 원래 프로세스를 죽이는 방식으로 흉내만 내는 것으로 보이고,
    예전에 이 방식(DPJ 연속 실패 시 자동재시작)을 썼다가 알 수 없는 이유로
    전체가 조용히 멈춰버린 사고가 있어서 그 기능 자체를 꺼둔 전례가 있다
    (app.py의 _self_restart 참고). 완전히 새 프로세스를 .bat이 새로
    띄우게 하면 그런 위험이 없다. 스레드에서 프로세스 전체를 확실히 끝내려면
    sys.exit()가 아니라 os._exit()를 써야 한다(sys.exit은 그 스레드만
    끝내고 프로세스 전체는 안 끝난다)."""
    while True:
        time.sleep(_UPDATE_POLL_INTERVAL_SECONDS)
        try:
            import urllib.request
            remote_version = urllib.request.urlopen(_VERSION_URL, timeout=10).read().decode("utf-8").strip()
        except Exception as e:
            print(f"[자동업데이트] 확인 실패(무시하고 계속 돕니다): {type(e).__name__}: {e}")
            continue
        local_version = _local_version()
        if not remote_version or remote_version == local_version:
            print(f"[자동업데이트] 확인함 - 변경 없음 (현재 버전: {local_version or '(없음)'})")
            continue
        # 새 버전을 찾았어도, 하필 그 순간 DPJ/오너클랜 자동화가 다팔자나
        # 오너클랜 창을 한창 조작하는 중이면 바로 꺼버리면 안 된다 - 창이
        # 중간 상태로 어중간하게 남을 수 있다(사용자 요청: "DPJ 자동화
        # 돌고 있는 중간에는 업데이트가 안 되게"). 확인 주기를 늘리는 건
        # 확률을 낮출 뿐 근본 해결이 아니라서(자동화가 최대 15분까지도
        # 걸릴 수 있어 어떤 간격을 줘도 겹칠 수 있음), 대신 그때그때
        # 실제로 돌고 있는지 물어보고, 돌고 있으면 이번엔 재시작을 건너뛰고
        # 다음 2분 뒤에 다시 확인한다 - 자동화가 끝나는 즉시(늦어도 다음
        # 주기 안에) 재시작된다.
        try:
            import dapalza_auto
            import ownerclan_auto
            busy = dapalza_auto.is_busy() or ownerclan_auto.is_busy()
        except Exception:
            busy = False
        if busy:
            print(f"[자동업데이트] 새 버전 발견 (현재: {local_version or '(없음)'} -> 최신: {remote_version}) - "
                  f"지금 자동화가 진행 중이라 재시작을 미룹니다 (다음 확인 때 다시 시도).")
            continue
        print(f"[자동업데이트] 새 버전 발견 (현재: {local_version or '(없음)'} -> 최신: {remote_version}) - "
              f"서버를 재시작해서 적용합니다...")
        os._exit(_AUTO_RESTART_EXIT_CODE)


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

    # START.bat이 python이 시작되기도 전에 WINDOWTITLE/포트 기준으로 1차
    # 정리를 하지만, 그건 "지금 포트 5000을 쥔 딱 하나"만 찾아서 여러
    # 세대에 걸쳐 쌓인 더 오래된 고아 창까지는 못 잡는다(사용자 확인:
    # "왜 아직도 로그 못끄는거야" - 하루 전 세션까지 남아있었음). 여기서
    # 명령줄 기준으로 전부 쓸어서 2차로 정리한다.
    _close_previous_instances()

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
    # 자동 재시작(app.py의 _self_restart) 직후에는 브라우저 탭을 새로 안
    # 연다 - 사용자는 이미 열어둔 탭이 있을 텐데 재시작마다 또 새 탭이
    # 뜨면 원치 않는 창이 갑자기 나타나는 걸로 보이고(사용자 지적:
    # "이창은왜뜨는거냐 그래서계속오류나잖아"), 그 새 탭이 포커스를
    # 가져가면서 때마침 다음 다팔자 자동화가 엉뚱한 창에 입력을 보내
    # 또 실패하는 연쇄로 이어질 수 있다(실제로 재시작 직후 바로 다음
    # DPJ 자동수집이 "저장된 파일을 못 찾았다"로 또 실패한 로그와
    # 시간대가 겹침).
    is_self_restart = os.environ.pop('MARGIN_BOARD_SELF_RESTART', None) == '1'
    if is_self_restart:
        print("(자동 재시작 - 이미 열려있는 브라우저 탭을 그대로 쓰므로 새 탭을 열지 않습니다.)")
    else:
        print("잠시 후 브라우저가 자동으로 열립니다. (안 열리면 http://127.0.0.1:5000 직접 접속하세요)")
    print()
    print("이 창을 닫으면 프로그램이 종료됩니다. 계속 이 창을 켜두세요.")
    print("============================================")
    print()

    if not is_self_restart:
        threading.Thread(target=open_browser_later, daemon=True).start()

    threading.Thread(target=_periodic_update_check_loop, daemon=True).start()

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
