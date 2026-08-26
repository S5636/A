# -*- coding: utf-8 -*-
"""'오너클랜 매입 수집' - 오너클랜(웹사이트)의 주문/배송조회 페이지에서
발주내역 엑셀을 자동으로 다운로드해서 마진보드에 업로드한다.

다팔자는 설치형 윈도우 프로그램이라 화면 좌표를 더듬는 방식(pywinauto)이
필요했지만, 오너클랜은 그냥 웹사이트라서 Playwright로 HTML 요소를 이름/텍스트로
직접 찾아 조작한다.

로그인 세션 유지 방식(중요, 2단계로 문제를 겪고 고침):
1) 처음엔 "로그인 → 브라우저 닫기 → 나중에 그 프로필 폴더로 새 브라우저 열기"로
   세션을 재사용하려 했는데, 실제로 테스트해보니 로그인 직후에 브라우저를 완전히
   닫으면(프로세스 종료) 오너클랜 로그인 세션 자체가 사라지는 걸로 확인됐다 -
   순수 세션 쿠키를 쓰는 것으로 보이고, 로그인 화면에 '로그인 상태 유지' 체크박스도
   없다. 그래서 로그인 후 창을 닫는 대신 최소화만 해서 마진보드 프로그램이
   켜져있는 동안 그 브라우저 프로세스를 계속 살려두는 방식으로 바꿨다.
2) 그런데 그렇게 해도 '로그인했는데 인식을 못 한다'는 문제가 남아있었다 - 원인은
   Playwright의 동기(sync) API가 "만든 스레드에서만 써야 한다"는 제약이 있는데,
   Flask가 요청마다 다른 스레드에서 핸들러를 실행할 수 있어서, 로그인 설정
   요청과 나중의 수집 요청이 서로 다른 스레드에서 처리되면 그 사이 스레드가
   달라져서 저장해둔 page 객체를 못 쓰는(조용히 예외가 나서 "닫혀있다"고 오판하는)
   문제가 있었다. 그래서 Playwright를 다루는 코드 전부를 전용 백그라운드 스레드
   하나에서만 돌아가게 큐 방식으로 바꿨다 - 어느 요청이 어느 스레드에서 들어오든
   실제 브라우저 조작은 항상 같은 스레드에서 실행된다.

주의: ownerclan_profile 폴더는 로그인 세션이 들어있는 사용자의 로컬 상태라,
shop_data.db/fees_config.json/settings.json과 마찬가지로 업데이트 zip을 만들
때 절대 포함하면 안 된다."""
import atexit
import os
import platform
import queue
import threading
import time
import re
from urllib.parse import urlsplit, quote

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(BASE_DIR, 'ownerclan_profile')

_state = {'pw': None, 'context': None, 'page': None}

# Playwright sync API는 만든 스레드에서만 안전하게 쓸 수 있어서, 브라우저를
# 다루는 실제 작업은 전부 이 스레드 하나에서만 실행하고, 다른 스레드(Flask 요청
# 스레드)는 큐에 작업을 넣고 결과를 기다리기만 한다.
_task_queue = queue.Queue()
_worker_started = False
_worker_start_lock = threading.Lock()

# STOCK(재고확인)이 신규주문 건수가 많으면 몇 분씩 걸릴 수 있는데, DPJ와
# 똑같이 요청이 다 끝나야만 로그를 통째로 돌려주는 구조라 그동안 화면이
# 안 바뀌어서 "멈췄다"는 오해로 이어졌다(DPJ에서 실제로 겪은 문제와 동일) -
# 진행 중 로그를 실시간으로 조회할 수 있게 같은 방식으로 버퍼를 둔다.
_oc_progress_lock = threading.Lock()
_oc_progress_log = []


def get_progress():
    with _oc_progress_lock:
        return list(_oc_progress_log)


def _reset_oc_progress():
    with _oc_progress_lock:
        _oc_progress_log.clear()


def _push_oc_progress(msg):
    with _oc_progress_lock:
        _oc_progress_log.append(msg)


def _worker_loop():
    while True:
        func, args, kwargs, result_queue = _task_queue.get()
        try:
            result = func(*args, **kwargs)
            result_queue.put(('ok', result))
        except Exception as e:
            result_queue.put(('err', e))


def _run_on_worker(func, *args, **kwargs):
    global _worker_started
    with _worker_start_lock:
        if not _worker_started:
            t = threading.Thread(target=_worker_loop, daemon=True)
            t.start()
            _worker_started = True
    result_q = queue.Queue()
    _task_queue.put((func, args, kwargs, result_q))
    status, result = result_q.get()
    if status == 'err':
        raise result
    return result


def _friendly_error(e):
    return f'{type(e).__name__}: {e}'


def _cleanup():
    """반드시 워커 스레드 안에서만 호출한다."""
    try:
        if _state['context'] is not None:
            _state['context'].close()
    except Exception:
        pass
    try:
        if _state['pw'] is not None:
            _state['pw'].stop()
    except Exception:
        pass
    _state['pw'] = None
    _state['context'] = None
    _state['page'] = None


@atexit.register
def _cleanup_on_exit():
    try:
        _run_on_worker(_cleanup)
    except Exception:
        pass


def _mark_profile_clean_exit():
    """이 프로필 폴더가 이전에 비정상 종료(예: 마진보드 프로그램을 새 버전으로
    다시 켜느라 이전 파이썬 프로세스가 정리 코드 없이 죽은 경우)된 걸로 남아있으면,
    다음에 크롬을 켤 때 '페이지를 복원하시겠습니까?' 알림이 뜨면서 창이 화면
    앞으로 강제로 나오는 걸 실제로 확인했다 - 그러면 백그라운드로 숨겨둔 의미가
    없어진다. 프로필의 종료 상태를 미리 '정상 종료'로 표시해두면 이 알림 자체가
    안 뜬다."""
    pref_path = os.path.join(PROFILE_DIR, 'Default', 'Preferences')
    try:
        import json
        if not os.path.exists(pref_path):
            return
        with open(pref_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # exit_type만 'Normal'로 바꿔도 대부분은 막히는데, 그래도 '페이지를
        # 복원하시겠습니까?' 팝업이 다시 뜨는 사례가 실제로 있었다(사용자
        # 스크린샷으로 확인) - 크롬 버전에 따라 exited_cleanly 불리언도 같이
        # 봐서 판단하는 걸로 보여 둘 다 같이 정상 종료로 표시해둔다. 이
        # 팝업은 Playwright가 클릭할 수 없는 브라우저 네이티브 UI라서(JS
        # dialog가 아님), 한 번 뜨면 자동화가 그 뒤 페이지를 제대로 못 읽고
        # STOCK 판정/ORDER 클릭이 전부 엉뚱하게 실패하는 원인이 될 수 있다.
        data.setdefault('profile', {})['exit_type'] = 'Normal'
        data['profile']['exited_cleanly'] = True
        with open(pref_path, 'w', encoding='utf-8') as f:
            json.dump(data, f)
    except Exception:
        pass


def _kill_stray_browser_processes(L=None):
    """이 프로그램 전용 프로필(PROFILE_DIR)로 떠 있는 크롬 프로세스가 있으면
    전부 종료한다. 마진보드를 새 버전으로 재시작할 때 이전 파이썬 프로세스가
    브라우저를 정상적으로 안 닫고 죽으면, 그 크롬이 고아 프로세스로 남아서
    다음 실행이 새로 띄우려 해도 그 낡은 프로세스에 그대로 붙어버리는 사고가
    반복됐다(비정상종료 복원 알림이 계속 뜨고, 방금 고친 코드가 전혀 반영 안
    된 낡은 창을 계속 쓰게 됨). --user-data-dir에 이 프로그램 전용 폴더
    경로가 들어있는 chrome 프로세스만 정확히 골라서 종료하므로, 사용자가
    평소 쓰는 크롬은 절대 안 건드린다."""
    try:
        import psutil
    except ImportError:
        return
    target = os.path.normcase(os.path.abspath(PROFILE_DIR))
    killed = 0
    for proc in psutil.process_iter(['name', 'cmdline']):
        try:
            name = (proc.info.get('name') or '').lower()
            if 'chrome' not in name:
                continue
            cmdline = proc.info.get('cmdline') or []
            if any(target in os.path.normcase(arg) for arg in cmdline):
                proc.kill()
                killed += 1
        except Exception:
            continue
    if killed and L is not None:
        L(f'이전에 남아있던 오너클랜 전용 크롬 프로세스 {killed}개를 정리하고 새로 띄웁니다.')


def _get_or_launch_page(L, launch_if_missing=True):
    """반드시 워커 스레드 안에서만 호출한다. 이미 살아있는 브라우저 페이지가
    있으면 그대로 재사용하고, 없으면(그리고 launch_if_missing이면) 새로 띄운다."""
    if _state['page'] is not None:
        try:
            _state['page'].evaluate('1')
            return _state['page']
        except Exception:
            if launch_if_missing:
                L('이전에 열려있던 브라우저 창이 닫혀있어서 다시 엽니다...')
            _cleanup()

    if not launch_if_missing:
        return None

    from playwright.sync_api import sync_playwright
    os.makedirs(PROFILE_DIR, exist_ok=True)
    # 지금 이 파이썬 프로세스 안에서는 브라우저를 한 번도 띄운 적이 없는데도
    # 여기 도달했다는 건, 이전 실행(이전 버전 등)이 남긴 고아 크롬 프로세스가
    # 있을 수 있다는 뜻이다 - 새로 띄우기 전에 먼저 정리한다.
    _kill_stray_browser_processes(L)
    _mark_profile_clean_exit()
    pw = sync_playwright().start()
    # 화면 밖 좌표(--window-position=-32000,-32000)에 띄우는 방법도 써봤는데,
    # 그러면 로그인하려고 나중에 'normal'로 복원해도 windowState만 바뀌고
    # 좌표는 화면 밖 그대로라 로그인창 자체가 안 보이는 사고가 났다(직접
    # 재현 확인함). 그래서 화면 밖 배치는 포기하고, 아래에서 최소화만으로
    # 백그라운드 처리한다 - 로그인 때는 _set_window_state가 명시적으로
    # 화면 안쪽 좌표까지 같이 지정해서 확실히 보이게 만든다.
    context = pw.chromium.launch_persistent_context(
        PROFILE_DIR, headless=False,
        args=[
            '--disable-blink-features=AutomationControlled',
            '--disable-session-crashed-bubble',
        ],
        no_viewport=True,
    )
    page = context.pages[0] if context.pages else context.new_page()
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    page.on('dialog', lambda d: d.accept())
    _state['pw'] = pw
    _state['context'] = context
    _state['page'] = page
    # 여기서 곧바로 최소화하지 않는다 - 로그인 흐름(setup_login)이 이 직후에
    # 직접 화면에 보이게 만드는데, 막 최소화한 창을 바로 다시 복원하는 게
    # 꼬여서 창이 계속 안 보이는 사고가 있었다. 최소화는 각 흐름(로그인 완료
    # 후, 수집 흐름 등)이 필요할 때 알아서 한다.
    return page


def _set_window_state(page, state, L=None):
    """창을 닫지 않고 최소화(minimized)/복원(normal)만 한다 - 로그인 세션은
    브라우저 프로세스가 살아있는 한 유지되므로, 닫는 대신 이 방식으로 화면에
    보였다 안 보였다만 시킨다. 'normal'로 복원할 때는 화면 안쪽 좌표까지
    같이 지정해서, 혹시 창이 어떤 이유로든 화면 밖에 있었더라도 확실히
    사용자 눈에 보이는 위치로 오게 한다."""
    try:
        cdp = page.context.new_cdp_session(page)
        info = cdp.send('Browser.getWindowForTarget')
        bounds = {'windowState': state}
        if state == 'normal':
            bounds.update({'left': 80, 'top': 80, 'width': 1280, 'height': 860})
        cdp.send('Browser.setWindowBounds', {
            'windowId': info['windowId'],
            'bounds': bounds,
        })
    except Exception as e:
        if L is not None:
            L(f'창 상태 변경에는 실패했지만 로그인/수집 자체엔 영향 없습니다: {_friendly_error(e)}')


def _is_logged_in(page):
    # 로그인 후 마이페이지 영역 제목이 한글 '마이페이지'가 아니라 영문
    # 'MY PAGE'로 표시된다는 걸 사용자 스크린샷으로 확인했다 - 한글로만
    # 찾다가 로그인해도 계속 '안 됨'으로 잘못 판정하는 사고가 있었다.
    return page.get_by_text('MY PAGE', exact=False).count() > 0 or page.get_by_text('안녕하세요', exact=False).count() > 0


def _setup_login_impl(start_url, wait_seconds, L):
    L('로그인용 브라우저 창을 여는 중... (이미 백그라운드에 떠있었다면 그 창을 다시 씁니다)')
    page = _get_or_launch_page(L)
    page.goto(start_url, wait_until='domcontentloaded', timeout=30000)
    # 이전에 최소화해서 백그라운드로 보내둔 창일 수 있으니, 로그인하려면
    # 다시 화면에 보이게 복원한다. CDP로 windowState/좌표를 바꾸는 것과
    # 별개로, Playwright 자체 기능인 bring_to_front()도 같이 써서 탭이
    # 확실히 맨 앞에 오게 한다(둘 중 하나가 막혀도 다른 하나로 보이도록).
    _set_window_state(page, 'normal', L)
    try:
        page.bring_to_front()
    except Exception:
        pass
    L('★ 지금 새로 뜨거나 앞으로 나온 이 창(평소 쓰시는 크롬 창이 아닙니다)에서 직접 로그인해주세요. 안 보이면 작업표시줄에서 크롬 아이콘을 클릭해보세요. 로그인하시면 자동으로 감지해서 창을 최소화하고 백그라운드로 보냅니다 (최대 5분 대기).')

    logged_in = False
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        try:
            if _is_logged_in(page):
                logged_in = True
                break
        except Exception:
            pass
        time.sleep(2)

    if logged_in:
        L('로그인이 확인됐습니다. 창은 닫지 않고 최소화해서 백그라운드로 보냅니다 (마진보드 프로그램이 켜져있는 동안 로그인이 유지됩니다).')
    else:
        L(f'{wait_seconds}초 동안 로그인 완료를 자동으로 확인하지 못했습니다 - 이미 로그인하셨다면 문제 없으니 그냥 최소화합니다.')
    _set_window_state(page, 'minimized', L)
    return logged_in


def setup_login(start_url, wait_seconds=300):
    """브라우저 창을 열어서(이미 열려있으면 그 창을 그대로 씀) 사용자가 직접
    로그인하게 하고, 로그인 완료를 감지하면 그 창을 닫지 않고 최소화만 해서
    백그라운드에 계속 살려둔다."""
    log = []

    def L(msg):
        log.append(msg)

    if platform.system() != 'Windows':
        L('이 기능은 윈도우 PC에서만 동작합니다.')
        return {'ok': False, 'log': log}

    if not start_url:
        L("오너클랜 주소가 설정되어 있지 않습니다. '데이터 업로드' 탭에서 '바로가기 주소 설정'으로 오너클랜 발주내역 페이지 주소를 먼저 저장해주세요.")
        return {'ok': False, 'log': log}

    try:
        import playwright  # noqa: F401
    except ImportError:
        L("playwright가 설치되어 있지 않습니다. START.bat을 다시 실행하면 자동으로 설치됩니다.")
        return {'ok': False, 'log': log}

    try:
        logged_in = _run_on_worker(_setup_login_impl, start_url, wait_seconds, L)
        return {'ok': logged_in, 'log': log}
    except Exception as e:
        L(f'로그인 설정 중 오류 발생 - {_friendly_error(e)}')
        return {'ok': False, 'log': log}


def _collect_impl(order_url, target_path, L):
    page = _get_or_launch_page(L, launch_if_missing=False)
    if page is None:
        L("아직 로그인된 브라우저가 없습니다 - 먼저 '오너클랜 로그인 설정'을 눌러 로그인해주세요. (마진보드 프로그램을 새로 켰다면 로그인을 다시 한 번 해주셔야 합니다.)")
        return False

    L('오너클랜 페이지를 여는 중 (백그라운드, 로그인 설정 때 열어둔 창을 그대로 씁니다)...')
    page.goto(order_url, wait_until='domcontentloaded', timeout=30000)
    page.wait_for_timeout(1500)

    if not _is_logged_in(page):
        try:
            page_title = page.title()
        except Exception:
            page_title = '(제목 조회 실패)'
        try:
            current_url = page.url
        except Exception:
            current_url = '(주소 조회 실패)'
        try:
            body_text = page.locator('body').inner_text()[:300]
        except Exception:
            body_text = '(본문 조회 실패)'
        L("로그인 상태가 아닌 것 같습니다 - 페이지가 로그인 폼으로 넘어갔습니다. '오너클랜 로그인 설정'을 다시 눌러 재로그인해주세요.")
        L(f"진단정보 - 페이지 제목: '{page_title}' / 주소: {current_url}")
        L(f"진단정보 - 화면에 보이는 글자(앞부분 300자): {body_text}")
        return False

    L("조회기간을 '1개월'로 설정...")
    try:
        page.get_by_text('1개월', exact=True).first.click(timeout=10000)
    except Exception as e:
        L(f"'1개월' 버튼을 못 찾았습니다 (기본 기간으로 진행합니다): {_friendly_error(e)}")
    page.wait_for_timeout(500)

    # 사용자가 실제 페이지 소스에서 직접 확인해준 정보: 엑셀 다운로드 링크는
    # 텍스트로 찾을 버튼이 아니라 href="javascript:getOrderListExcel();" 형태의
    # 링크였다 - '엑셀'이 들어간 텍스트로 찾으면 전혀 다른 기능인 '엑셀 주문하기'
    # 버튼이 걸려서 안 보이는 상태로 클릭 재시도만 반복하다 실패했다. 그래서
    # 그 버튼을 화면에서 찾아 클릭하는 대신, 페이지에 있는 그 자바스크립트
    # 함수를 직접 호출한다 - 링크를 클릭했을 때와 결과가 동일하면서, 화면에
    # 보이는지/눌리는지 같은 문제 자체가 생기지 않는다.
    L("엑셀 다운로드 함수(getOrderListExcel) 직접 호출 및 다운로드 대기 (파일 생성에 시간이 걸릴 수 있어 최대 2분 기다림)...")
    try:
        with page.expect_download(timeout=120000) as download_info:
            page.evaluate('getOrderListExcel()')
        download = download_info.value
        download.save_as(target_path)
    except Exception as e:
        L(f"엑셀 다운로드에 실패했습니다: {_friendly_error(e)}")
        try:
            has_func = page.evaluate("typeof getOrderListExcel")
            L(f"진단정보 - 페이지에 getOrderListExcel 함수가 있는지: {has_func}")
        except Exception as e2:
            L(f'진단정보 수집도 실패: {_friendly_error(e2)}')
        try:
            texts = page.locator('button, a, [role="button"]').all_inner_texts()
            texts = [t.strip() for t in texts if t.strip()]
            L(f"진단정보 - 지금 화면의 버튼/링크 글자들: {texts[:80]}")
        except Exception as e2:
            L(f'진단정보 수집도 실패: {_friendly_error(e2)}')
        return False

    return True


def _ensure_background_impl():
    page = _get_or_launch_page(None, launch_if_missing=False)
    if page is not None:
        _set_window_state(page, 'minimized', None)


def ensure_background():
    """다팔자 자동화처럼 화면 좌표를 더듬는 다른 자동화가 시작되기 직전에
    호출한다. 오너클랜 창이 화면에 로그인 등으로 떠 있는 상태(예: 사용자가
    로그인 완료를 기다리다가 마무리 안 하고 다른 작업으로 넘어간 경우)라면,
    그 창이 다른 프로그램의 창을 가리고 있을 수 있고 - 가려진(occluded)
    Electron/Chromium 창은 접근성 트리 생성이 멈추는 경우가 실제로 있었다.
    오너클랜 브라우저가 살아있으면 무조건 먼저 최소화해서 이 위험을 없앤다.
    브라우저가 아예 없으면(로그인 안 한 상태) 그냥 조용히 넘어간다."""
    try:
        _run_on_worker(_ensure_background_impl)
    except Exception:
        pass


def collect_and_upload(order_url, save_folder=None, save_filename='oc.xlsx'):
    """로그인 설정 때 띄워놓고 백그라운드로 보낸 그 브라우저 창을 그대로 재사용해서
    조회기간을 1개월로 맞추고 엑셀다운로드를 눌러 발주내역 파일을 받는다."""
    log = []

    def L(msg):
        log.append(msg)

    if platform.system() != 'Windows':
        L('이 기능은 윈도우 PC에서만 동작합니다.')
        return {'ok': False, 'log': log}

    if not order_url:
        L("오너클랜 주소가 설정되어 있지 않습니다. '데이터 업로드' 탭에서 '바로가기 주소 설정'으로 오너클랜 발주내역 페이지 주소를 먼저 저장해주세요.")
        return {'ok': False, 'log': log}

    try:
        import playwright  # noqa: F401
    except ImportError:
        L("playwright가 설치되어 있지 않습니다. START.bat을 다시 실행하면 자동으로 설치됩니다.")
        return {'ok': False, 'log': log}

    target_dir = save_folder or os.path.join(os.path.expanduser('~'), 'Downloads')
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, save_filename)

    try:
        ok = _run_on_worker(_collect_impl, order_url, target_path, L)
    except Exception as e:
        L(f'자동화 중 오류 발생 - {_friendly_error(e)}')
        return {'ok': False, 'log': log}

    if not ok:
        return {'ok': False, 'log': log}

    if not os.path.exists(target_path):
        L(f'다운로드는 진행됐는데 파일을 못 찾았습니다: {target_path}')
        return {'ok': False, 'log': log}

    L(f'파일 저장 확인 완료: {target_path}')
    return {'ok': True, 'log': log, 'file_path': target_path}


def _detail_url_for_code(order_url, code):
    """검색결과에서 상품 카드를 클릭해서 상세페이지로 들어가는 방식이 계속
    실패했다(새 탭 감지도 0개, 클릭해도 검색결과 페이지 그대로) - 사용자가
    개발자도구로 실제 카드의 HTML을 직접 확인해줘서(2026-08-26) 원인이
    나왔다: 진짜 링크는 상품코드 글자가 아니라 상품명 글자에만 씌워져
    있었고(<a target="_blank" href="/V2/product/view.php?selfcode=코드">
    상품명</a>), 우리는 코드 글자를 클릭하고 있었으니 애초에 링크가
    아닌 글자를 클릭한 것이었다. 근데 그 href 자체가 코드 하나로 바로
    만들어지는 고정된 패턴이라, 검색→카드 클릭이라는 불안정한 단계를
    거칠 필요 없이 이 주소로 곧장 이동하면 된다."""
    try:
        parts = urlsplit(order_url)
        base = f'{parts.scheme}://{parts.netloc}'
    except Exception:
        base = 'https://www.ownerclan.com'
    return f'{base}/V2/product/view.php?selfcode={quote(code)}'


def _normalize_option_text(s):
    return re.sub(r'\s+', '', str(s or '')).lower()


def _find_matching_option_li(option_lis, option_count, target_option, code, L):
    """옵션 목록 중 실제 주문에 찍힌 옵션(target_option)과 이름이 일치하는
    항목을 찾는다. 오너클랜 옵션명에는 '07행운키링-말띠'처럼 우리 쪽 옵션
    표기('말띠')에는 없는 접두어가 붙어있을 수 있어서, 정확히 같은지가
    아니라 한쪽이 다른 쪽을 포함하는지로 비교한다(공백/대소문자 무시)."""
    target_norm = _normalize_option_text(target_option)
    names = []
    for i in range(option_count):
        li = option_lis.nth(i)
        try:
            name_attr = li.get_attribute('option-name') or ''
        except Exception:
            name_attr = ''
        names.append(name_attr)
        name_norm = _normalize_option_text(name_attr)
        if target_norm and name_norm and (target_norm in name_norm or name_norm in target_norm):
            return li, name_attr
    L(f"[{code}] 주문된 옵션('{target_option}')과 일치하는 항목을 오너클랜 옵션 목록에서 못 찾았습니다. 오너클랜 옵션 목록: {names}")
    return None, None


def _extract_base_price(page, L, code):
    """상품 상세페이지 상단에 표시되는 판매가를 읽는다. 실제 화면 구조(사용자
    스크린샷으로 확인): "상품코드 W..." 줄 바로 아래 상품명/별점이 나오고,
    그 다음 취소선(정가) 가격 -> 실제 판매가 순서로 두 개의 '~원' 숫자가
    나온 뒤 "예상배송일자" 문구가 이어진다. 사이드바 광고에도 '~원' 가격이
    많아서 페이지 전체 텍스트에서 그냥 찾으면 엉뚱한 숫자를 집을 위험이 커,
    "상품코드"~"예상배송일자" 사이 구간으로 좁혀서 그 안에서만 찾는다. 정가
    취소선 없이 가격이 하나만 뜨는 상품도 있을 수 있어 그 경우는 그 값을
    쓴다. (멤버십 전용 추가할인가는 셀러가 별도로 선점해야 적용되는 값이라
    기본 판매가로 보지 않는다 - 실제 발주 시 결제금액과 다르면 조정 필요.)"""
    try:
        text = page.locator('body').inner_text()
    except Exception as e:
        L(f"[{code}] 가격 확인용 페이지 텍스트 읽기 실패: {type(e).__name__}: {e}")
        return None
    start = text.find('상품코드')
    end = text.find('예상배송일자')
    if start == -1 or end == -1 or end <= start:
        L(f"[{code}] 가격이 있어야 할 화면 영역(상품코드~예상배송일자)을 못 찾았습니다 - 가격 확인을 건너뜁니다.")
        return None
    region = text[start:end]
    prices = re.findall(r'([\d,]{3,})원', region)
    if not prices:
        L(f"[{code}] 해당 영역에서 '~원' 가격을 못 찾았습니다.")
        return None
    try:
        return int(prices[1].replace(',', '')) if len(prices) >= 2 else int(prices[0].replace(',', ''))
    except Exception:
        return None


def _extract_option_addon(li, L, code):
    """선택된 옵션에 추가금이 있으면(예: "2세대-기본형(+850원)") 그 금액을
    더한다 - 실제 화면에서 옵션에 따라 가격이 달라지는 걸 스크린샷으로
    확인했다. 추가금 표기가 없는 옵션은 0원."""
    try:
        li_text = li.inner_text()
    except Exception:
        return 0
    m = re.search(r'\(\s*\+\s*([\d,]+)\s*원\s*\)', li_text)
    if not m:
        return 0
    try:
        return int(m.group(1).replace(',', ''))
    except Exception:
        return 0


def _check_one_stock(page, order_url, code, option, L):
    """판매사상품코드로 오너클랜 상품 상세페이지에 직접 이동해서(검색결과
    카드를 클릭하는 불안정한 단계 없이) 재고상태를 확인한다. 상품에
    옵션(색상/사이즈 등)이 있으면 '아무 옵션이나 하나 살아있으면 정상'이
    아니라, 실제 주문에 찍힌 그 옵션 하나만 정확히 찾아서 그 옵션의
    품절여부로 판정한다. 옵션 자체가 없는 단일상품은 '바로구매' 버튼
    유무로 본다. 재고상태와 함께, 매입가를 가늠할 수 있도록 화면에
    보이는 판매가(+옵션 추가금)도 같이 읽어온다 - 실제 매입가와 정확히
    같다는 보장은 없는 추정치임을 호출부에 알린다."""
    url = _detail_url_for_code(order_url, code)
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=30000)
    except Exception as e:
        L(f"[{code}] 상세페이지 이동 실패: {type(e).__name__}: {e}")
        return '확인실패', None, False

    try:
        page.wait_for_load_state('networkidle', timeout=15000)
    except Exception:
        pass
    try:
        page.get_by_text('바로구매', exact=False).first.wait_for(state='attached', timeout=10000)
    except Exception:
        pass

    # view.php?selfcode=코드로 이동했는데 실제로는 다른 페이지(로그인
    # 페이지, 품/단종 안내 등)로 리다이렉트됐을 수 있다 - 그 상태에서
    # 계속 진행하면 엉뚱한 페이지에서 옵션/가격을 읽어 잘못된 결과를 내는
    # 사고로 이어진다. 주소가 실제로 상세페이지 형태인지부터 확인한다.
    try:
        current_url = page.url
    except Exception:
        current_url = ''
    if 'view.php' not in current_url:
        L(f"[{code}] 상세페이지 주소로 이동을 시도했는데 실제로는 다른 페이지로 보입니다"
          f"(현재 주소: {current_url}) - 상품이 없거나 판매중지됐을 수 있습니다.")
        return '확인실패', None, False

    # 옵션(사이즈/색상 등)이 있는 상품은 '바로구매' 버튼이 옵션과 무관하게
    # 항상 떠 있어서, 텍스트로만 보면 특정 옵션이 품절이어도 못 잡는다.
    # 사용자가 실제 상세페이지 HTML을 캡쳐해서 확인해준 구조: 각 옵션이
    # <li class="option" option-name="07행운키링-말띠" option-soldout="0"|"1">
    # 형태로 이름과 품절 여부를 속성에 정확히 갖고 있다. 여기서 '옵션 중
    # 아무거나 하나라도 살아있으면 정상'으로 보면 안 된다 - 옵션이 여러 개인
    # 상품은 그럼 사실상 항상 정상으로만 나와서 의미가 없다. 실제 주문에
    # 찍힌 그 옵션 하나만 정확히 찾아서, 그 옵션 자체의 품절여부로 판정한다.
    try:
        option_lis = page.locator('li.option[option-soldout]')
        option_count = option_lis.count()
    except Exception as e:
        L(f"[{code}] 옵션 목록 확인 실패: {type(e).__name__}: {e}")
        return '확인실패', None, False

    base_price = _extract_base_price(page, L, code)

    # 판정 기준(사용자 지시): '바로구매' 버튼 유무는 더 이상 기준으로 안
    # 쓴다. ①찾는 옵션칸 자체에 '품절'이라고 써있거나 ②찾는 옵션이 목록에
    # 아예 없을 때만 품절로 본다 - 그 외(옵션이 있고 품절 표시가 없음)는
    # 전부 정상으로 본다.
    if option_count > 0:
        if not option:
            L(f"[{code}] 이 상품은 옵션이 {option_count}개 있는데 주문에 기록된 옵션 정보가 없어서 정확히 판정할 수 없습니다.")
            return '확인실패', None, False
        li, matched_name = _find_matching_option_li(option_lis, option_count, option, code, L)
        if li is None:
            # 찾는 옵션이 목록에 없음 -> 품절로 본다.
            L(f"[{code}] 주문 옵션 '{option}'을 오너클랜 옵션 목록에서 못 찾았습니다 - 품절로 처리합니다.")
            return '품절', None, False
        try:
            soldout = li.get_attribute('option-soldout')
        except Exception as e:
            L(f"[{code}] 옵션 품절여부 확인 실패: {type(e).__name__}: {e}")
            return '확인실패', None, False
        addon = _extract_option_addon(li, L, code)
        price = (base_price + addon) if base_price is not None else None
        if addon and price is not None:
            L(f"[{code}] 옵션 '{matched_name}'에 추가금 {addon}원 확인 - 매입예상가 {price}원(기본 {base_price}원 + 추가금 {addon}원).")
        L(f"[{code}] 주문 옵션 '{option}' → 오너클랜 옵션 '{matched_name}' 매칭, {'구매 가능' if soldout == '0' else '품절'}.")
        return ('정상' if soldout == '0' else '품절'), price, False

    # 옵션 목록 자체가 없는 단일 상품. 페이지 전체에서 '품절'을 느슨하게
    # (exact=False) 찾으면 리뷰/추천상품/안내문구 등 상품 자체와 무관한
    # 곳에 있는 글자까지 걸려서, 실제로는 정상인데도 품절로 잘못 나오는
    # 사고가 있었다(실제로 다 정상인 상품 3개가 전부 품절로 나옴).
    # '품절'이라는 글자 딱 그것만이 요소의 전체 텍스트인 경우(진짜 품절
    # 배지/라벨일 가능성이 높음)로 exact=True로 좁혀서, 긴 문장 속에
    # 우연히 '품절'이라는 단어가 섞여 있는 경우는 걸러낸다.
    try:
        has_soldout = page.get_by_text('품절', exact=True).count() > 0
    except Exception as e:
        L(f"[{code}] 상품 페이지 상태 확인 실패: {type(e).__name__}: {e}")
        return '확인실패', None, False

    # 옵션이 있는 상품인데도 li.option[option-soldout] 셀렉터가 0개로
    # 잘못 잡혀서 전부 이 '단일상품' 경로로 빠지고 있다는 지적(사용자: "옵션
    # 없는 단일상품이 아닌데 전부 그렇게 뜨네")이 있었다 - 실제 옵션 구조가
    # 우리가 아는 것과 다른 페이지가 있는지 셀렉터를 바꿔 짐작하는 대신,
    # 이 상품의 주문에 옵션 문자열이 찍혀있는데도 옵션 목록이 0개로 잡혔다면
    # 그 자체가 강한 증거이니 눈에 띄게 경고로 남기고, 다른 후보 셀렉터
    # 개수도 같이 남겨서 다음에 실제 구조를 정확히 알 수 있게 한다.
    if option:
        try:
            alt_counts = {
                'li.option(속성무관)': page.locator('li.option').count(),
                '[option-soldout] 속성 아무 태그': page.locator('[option-soldout]').count(),
                'select option': page.locator('select option').count(),
                "'옵션' 글자 포함 요소": page.get_by_text('옵션', exact=False).count(),
            }
        except Exception:
            alt_counts = {}
        L(f"[{code}] ⚠ 주문엔 옵션 '{option}'이 찍혀있는데 옵션 목록이 0개로 잡혀서 단일상품으로 처리됩니다 - "
          f"이 상품은 실제로 옵션이 있을 가능성이 높습니다(재고상태를 못 믿을 수 있음). 진단: {alt_counts}")

    L(f"[{code}] 옵션 없는 단일상품(또는 옵션 목록 인식 실패) - '품절' 배지 {'있음' if has_soldout else '없음'}.")
    return ('품절' if has_soldout else '정상'), base_price, bool(option)


def _check_stock_impl(order_url, items, L):
    page = _get_or_launch_page(L, launch_if_missing=False)
    if page is None:
        # 예전엔 여기서 빈 리스트([])를 그냥 돌려줬는데, 그러면 check_stock()이
        # 이걸 '확인할 게 하나도 없어서 정상적으로 0건'과 구분 못 하고 똑같이
        # ok=True로 리턴해버려서, 화면엔 "재고상태 확인 완료 (0건)"이라는
        # 성공 토스트가 떴다 - 사실은 로그인된 브라우저가 없어서 자동화를
        # 시작도 못 한 실패인데 성공처럼 보인 사고. 예외를 던져서 check_stock()의
        # 실패 처리 경로(ok=False + 실패 토스트)를 타게 한다.
        raise RuntimeError("아직 로그인된 브라우저가 없습니다 - 먼저 '오너클랜 로그인 설정'을 눌러 로그인해주세요.")

    results = []
    for code, option in items:
        if not code:
            continue
        label = f"{code}/{option}" if option else code
        L(f"[{label}] 오너클랜에서 재고상태 확인 중...")
        status, price, uncertain = _check_one_stock(page, order_url, code, option, L)
        results.append({'vendor_prod_id': code, 'option_name': option, 'status': status, 'price': price,
                         'option_uncertain': uncertain})
        L(f"[{label}] → {status}" + (f" (매입예상가 {price}원)" if price is not None else ""))
    return results


def check_stock(order_url, items):
    """(판매사상품코드, 주문된 옵션) 조합 목록을 받아 각각 오너클랜에서
    재고상태를 확인해서 [{'vendor_prod_id':.., 'option_name':.., 'status':
    '정상'|'품절'|'확인실패'}, ...] 리스트로 돌려준다."""
    _reset_oc_progress()
    log = []

    def L(msg):
        log.append(msg)
        _push_oc_progress(msg)

    if platform.system() != 'Windows':
        L('이 기능은 윈도우 PC에서만 동작합니다.')
        return {'ok': False, 'log': log, 'results': []}

    if not order_url:
        L("오너클랜 주소가 설정되어 있지 않습니다. '데이터 업로드' 탭에서 '바로가기 주소 설정'으로 오너클랜 발주내역 페이지 주소를 먼저 저장해주세요.")
        return {'ok': False, 'log': log, 'results': []}

    try:
        import playwright  # noqa: F401
    except ImportError:
        L("playwright가 설치되어 있지 않습니다. START.bat을 다시 실행하면 자동으로 설치됩니다.")
        return {'ok': False, 'log': log, 'results': []}

    if not items:
        # '신규주문' 조건은 없앴으니(사용자 요청: 칸이 비어있으면 무조건 확인) 이
        # 메시지도 그에 맞게 - 대상이 없는 이유는 "신규주문이 없어서"가 아니라
        # "취소/반품/배송완료·구매확정이 아닌 건 중 재고상태 칸이 빈 게 없어서"다.
        L('확인할 대상이 없습니다 - 취소/반품/배송완료·구매확정이 아니면서 재고상태 칸이 비어있는 건이 없어요 '
          '(이미 다 확인된 상태거나, 판매사상품코드 자체가 없는 경우일 수 있음).')
        return {'ok': True, 'log': log, 'results': []}

    try:
        results = _run_on_worker(_check_stock_impl, order_url, items, L)
    except Exception as e:
        L(f'자동화 중 오류 발생 - {_friendly_error(e)}')
        return {'ok': False, 'log': log, 'results': []}

    return {'ok': True, 'log': log, 'results': results}


# ---------------------------------------------------------------------------
# ORDER 버튼 - 체크된 주문만 오너클랜에서 옵션선택→배송정보 입력→결제수단
# (카드) 선택→'결제하기' 클릭까지 자동으로 하고, 그 다음(실제 카드번호 입력·
# 최종 결제 확정)은 절대 자동으로 하지 않는다 - 카드정보는 코드/DB 어디에도
# 남기지 않는다는 원칙(사용자 지시). 배송정보 입력칸들의 실제 구조(name/id
# 속성, readonly 여부 등)는 사용자가 개발자도구로 직접 확인해준 결과를
# 기반으로 한다(2026-08-26) - 이름/연락처는 name 속성으로 채우고, 우편번호는
# readonly라 자바스크립트로 값만 직접 넣는다(팝업 검색 자동화 대신).
# ---------------------------------------------------------------------------

def _place_one_order(page, order_url, item, L):
    """item: {'vendor_prod_id','option_name','qty','order_id','recipient',
    'recipient_phone','zipcode','ship_address','prod_name','delivery_note','cs_override'}"""
    code = str(item.get('vendor_prod_id') or '').strip()
    option = str(item.get('option_name') or '').strip()
    order_id = str(item.get('order_id') or '').strip()
    recipient = str(item.get('recipient') or '').strip()
    recipient_phone = str(item.get('recipient_phone') or '').strip()
    zipcode = str(item.get('zipcode') or '').strip()
    ship_address = str(item.get('ship_address') or '').strip()
    prod_name = str(item.get('prod_name') or '').strip()
    delivery_note = str(item.get('delivery_note') or '').strip()
    cs_override = str(item.get('cs_override') or '').strip()
    if cs_override:
        # CS메모에 오너클랜 상품코드가 수기로 남겨져 있으면 그걸 우선한다
        # (사용자 요청) - app.py에서 이미 vendor_prod_id를 이 값으로
        # 바꿔치기해서 넘겨준다. 여기선 어떤 코드로 주문하는지 로그로
        # 분명히 남긴다(원래 판매사상품코드 매칭과 다르게 주문됐다는 걸
        # 나중에 헷갈리지 않게).
        L(f"[{order_id}] CS메모에서 발견한 상품코드 '{cs_override}'로 주문합니다(판매사상품코드 매칭 대신 우선 적용).")

    # 괄호 기준으로 나누던 이전 방식이 틀렸다(사용자가 실제 사례로 확인:
    # "대구광역시 동구 율하동로23길 74-4 (율하동) 4116" - 여기선 "(율하동)"
    # 까지가 기본주소이고 그 뒤 "4116"이 상세주소다. 괄호 자체는 그냥
    # 법정동을 표기하는 기본주소의 일부였을 뿐이다. 사용자 지적대로 엑셀
    # 원본 셀에 실제 줄바꿈으로 기본주소/상세주소가 나뉘어 있었던 것으로
    # 보인다 - 첫 줄을 기본주소, 그 다음 줄(들)을 상세주소로 나눈다. 줄바꿈이
    # 없으면 나눌 근거가 없으니 통째로 기본주소에 넣고 상세주소는 비워둔다.
    lines = [ln.strip() for ln in ship_address.replace('\r\n', '\n').replace('\r', '\n').split('\n') if ln.strip()]
    addr_base = lines[0] if lines else ship_address
    addr_detail = ' '.join(lines[1:]) if len(lines) > 1 else ''
    try:
        qty = max(1, int(float(item.get('qty') or 1)))
    except Exception:
        qty = 1

    if not code:
        return {'ok': False, 'reason': '판매사상품코드가 없습니다.'}

    url = _detail_url_for_code(order_url, code)
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=30000)
    except Exception as e:
        return {'ok': False, 'reason': f'상세페이지 이동 실패: {type(e).__name__}: {e}'}
    try:
        page.wait_for_load_state('networkidle', timeout=15000)
    except Exception:
        pass
    try:
        page.get_by_text('바로구매', exact=False).first.wait_for(state='attached', timeout=10000)
    except Exception:
        pass
    try:
        current_url = page.url
    except Exception:
        current_url = ''
    if 'view.php' not in current_url:
        return {'ok': False, 'reason': f'상세페이지로 이동하지 못했습니다(현재 주소: {current_url}) - 상품이 없거나 판매중지됐을 수 있습니다.'}

    try:
        option_lis = page.locator('li.option[option-soldout]')
        option_count = option_lis.count()
    except Exception as e:
        return {'ok': False, 'reason': f'옵션 목록 확인 실패: {type(e).__name__}: {e}'}

    if option_count > 0:
        if not option:
            return {'ok': False, 'reason': f'이 상품은 옵션이 {option_count}개인데 주문에 옵션 정보가 없어 정확히 선택할 수 없습니다.'}
        li, matched_name = _find_matching_option_li(option_lis, option_count, option, code, L)
        if li is None:
            return {'ok': False, 'reason': f"주문 옵션 '{option}'을 오너클랜 옵션 목록에서 찾지 못했습니다."}
        try:
            soldout = li.get_attribute('option-soldout')
        except Exception:
            soldout = None
        if soldout == '1':
            return {'ok': False, 'reason': f"옵션 '{matched_name}'이 품절 상태입니다."}
        try:
            li.click(timeout=5000)
        except Exception:
            try:
                li.evaluate('el => el.click()')
            except Exception as e:
                return {'ok': False, 'reason': f'옵션 선택(클릭) 실패: {type(e).__name__}: {e}'}
        L(f"[{order_id}/{code}] 옵션 '{matched_name}' 선택 완료.")
        time.sleep(0.5)

    # 수량 - 기본값 1에서 (qty-1)번 '+' 버튼을 눌러 맞춘다. 옵션이 여러
    # 단계(색상→사이즈 등)인 상품은 이 시점에 두 번째 단계 선택이 아직 안
    # 끝나있을 수 있다 - 그런 경우 이 자동화는 아직 대응 못 한다(1차 버전).
    if qty > 1:
        try:
            plus_btn = page.get_by_text('+', exact=True).first
            for _ in range(qty - 1):
                plus_btn.click(timeout=3000)
                time.sleep(0.2)
            L(f"[{order_id}/{code}] 수량을 {qty}개로 설정.")
        except Exception as e:
            L(f"[{order_id}/{code}] 수량 설정 실패({type(e).__name__}) - 기본값(1개)으로 진행합니다. 결제 전 직접 확인해주세요.")

    # 바로 구매
    try:
        buy_btn = page.get_by_text('바로 구매', exact=False).first
        if buy_btn.count() == 0:
            buy_btn = page.get_by_text('바로구매', exact=False).first
        buy_btn.click(timeout=10000)
    except Exception:
        try:
            page.get_by_text('바로구매', exact=False).first.evaluate('el => el.click()')
        except Exception as e2:
            return {'ok': False, 'reason': f"'바로 구매' 버튼 클릭 실패: {type(e2).__name__}: {e2}"}
    try:
        page.wait_for_load_state('domcontentloaded', timeout=15000)
    except Exception:
        pass
    time.sleep(1)

    # 주문서(ORDER SHEET) 페이지 - 원장주문코드에 상품주문번호(order_id)를
    # 남긴다(사용자 지시) - 나중에 오너클랜 발주내역을 다시 받으면 이 값으로
    # 매입 매칭이 자동으로 된다.
    try:
        page.get_by_placeholder('외부적으로 주문서를 관리할 수 있는 코드를 남길 수 있습니다').fill(order_id)
    except Exception as e:
        L(f"[{order_id}/{code}] 원장주문코드 입력 실패({type(e).__name__}) - 결제 전 직접 입력해주세요.")

    # 배송정보 - 사용자가 직접 화면을 확인해서 알려준 구조(2026-08-26):
    # "기본주소/신규배송지/직접입력" 셋 중 "신규배송지"를 선택해야 우편번호
    # 검색 버튼(우편번호 검색)이 정상 동작한다 - "직접입력"은 틀린 선택이었다.
    try:
        page.get_by_text('신규배송지', exact=True).first.click(timeout=8000)
    except Exception as e:
        try:
            page.get_by_text('신규배송지', exact=True).first.evaluate('el => el.click()')
        except Exception as e2:
            L(f"[{order_id}/{code}] 주소 '신규배송지' 선택 실패({type(e2).__name__}) - 우편번호/주소는 아래서 직접 값을 넣으니 "
              f"큰 영향은 없지만, 결제 전 라디오 버튼이 '신규배송지'로 선택돼있는지 확인해주세요.")

    # 이름/연락처는 실제 개발자도구로 확인한 name 속성으로 직접 채운다
    # (placeholder가 아니라 name="receiver_name"/"receiver_tel21").
    try:
        page.locator('input[name="receiver_name"]').fill(recipient)
    except Exception as e:
        L(f"[{order_id}/{code}] 수령인 '이름' 입력 실패({type(e).__name__}) - 결제 전 '{recipient}'로 직접 입력해주세요.")
    try:
        page.locator('input[name="receiver_tel21"]').fill(recipient_phone)
    except Exception as e:
        L(f"[{order_id}/{code}] 연락처 입력 실패({type(e).__name__}) - 결제 전 직접 입력해주세요.")

    # 우편번호(#rpost)는 readonly고 클릭하면 카카오 우편번호 검색 팝업이
    # 뜨는 구조로 확인됐다(onclick="this.blur();get_post()") - 팝업에서
    # 직접 검색해 고르는 과정을 자동화하는 대신, 우리가 이미 알고 있는
    # 우편번호/주소 값을 자바스크립트로 그 칸에 곧장 넣는다(팝업을 실제로
    # 열고 검색결과를 클릭하는 것보다 훨씬 안정적). 기본주소(#raddr1)는
    # 같은 폼의 명명 규칙(rpost, raddr2)으로 짐작했던 건데 실제로 맞는
    # 것으로 확인됐다(사용자 캡처).
    try:
        page.locator('#rpost').evaluate('(el, v) => { el.value = v; }', zipcode)
    except Exception as e:
        L(f"[{order_id}/{code}] 우편번호 입력 실패({type(e).__name__}) - 결제 전 '{zipcode}'로 직접 입력해주세요.")
    try:
        page.locator('#raddr1').evaluate('(el, v) => { el.value = v; }', addr_base)
    except Exception as e:
        L(f"[{order_id}/{code}] 기본주소 입력 실패({type(e).__name__}) - 결제 전 '{addr_base}'로 직접 입력해주세요.")
    # 줄바꿈이 없어서 addr_detail이 비어있을 때 기본주소 전체를 여기 또
    # 채워넣던 게 실제로는 기본주소/상세주소 두 칸에 똑같은 긴 주소가
    # 중복으로 찍혀서 이상해 보이는 결과였다(사용자 지적: "상세주소
    # 병신되네") - 나눌 근거가 없으면 그냥 빈 채로 둔다.
    if addr_detail:
        try:
            page.locator('input[name="raddr2"]').fill(addr_detail)
        except Exception as e:
            L(f"[{order_id}/{code}] 상세주소 입력 실패({type(e).__name__}) - 결제 전 직접 확인해주세요.")
    else:
        L(f"[{order_id}/{code}] 배송지에 줄바꿈으로 나뉜 상세주소가 없어서 상세주소 칸은 비워뒀습니다 - "
          f"필요하면 결제 전 직접 확인해주세요.")

    # 배송시 요청사항(textarea[name="order_prmsg[]"]) - 상품명을 넣던 건
    # 잘못이었다(사용자 지적: "배송요청사항에 왜 상품명을 넣냐 배송요청을
    # 넣어야지") - 이 칸엔 주문자가 실제로 남긴 배송 요청사항이 들어가야
    # 한다. 다팔자/TOSS 표준 컬럼엔 이 값이 따로 없어서, 업로드 시점에
    # 저장해둔 원본 행 전체(raw_json)에서 흔한 컬럼명 후보로 찾은 값
    # (app.py의 _extract_delivery_note)을 넣는다 - 못 찾으면 빈 채로
    # 둔다(엉뚱하게 상품명 같은걸 대신 채우지 않는다).
    if delivery_note:
        try:
            page.locator('textarea[name="order_prmsg[]"]').first.fill(delivery_note)
        except Exception as e:
            L(f"[{order_id}/{code}] 배송요청사항 입력 실패({type(e).__name__}) - 결제 전 직접 확인해주세요.")
    else:
        L(f"[{order_id}/{code}] 배송요청사항 원본 데이터를 못 찾아서 비워뒀습니다 - 필요하면 결제 전 직접 입력해주세요.")

    # 결제수단: 카드
    try:
        page.get_by_text('카드', exact=True).first.click(timeout=5000)
    except Exception as e:
        L(f"[{order_id}/{code}] 결제수단 '카드' 선택 실패({type(e).__name__}) - 결제 전 직접 확인해주세요.")

    # '결제하기' - 여기까지만 자동이다. 이 버튼을 누르면 카드결제창이
    # 뜨는데, 카드번호 입력과 최종 결제 확정은 절대 자동으로 하지 않고
    # 반드시 사람이 직접 한다. 바로 위에서 여러 칸을 자바스크립트로 값만
    # 바꿔치기했는데(우편번호/기본주소), 화면이 그 변화를 반영할 시간을
    # 못 주고 곧바로 클릭하면 버튼이 아직 유효성 검사를 못 마친 상태일 수
    # 있어 잠깐 대기한 뒤, 다른 버튼들처럼 클릭 실패시 자바스크립트 클릭도
    # 시도한다.
    time.sleep(1)
    try:
        page.get_by_text('결제하기', exact=True).first.click(timeout=10000)
    except Exception as e:
        try:
            page.get_by_text('결제하기', exact=True).first.evaluate('el => el.click()')
        except Exception as e2:
            return {'ok': False, 'reason': f"'결제하기' 버튼 클릭 실패({type(e2).__name__}) - 주문서 화면에서 직접 확인해주세요."}

    L(f"[{order_id}/{code}] '결제하기' 클릭 완료 - 카드결제창이 뜨면 직접 카드정보를 입력하고 결제를 완료해주세요.")
    return {'ok': True, 'reason': "주문서 작성 및 '결제하기' 클릭까지 완료(카드결제는 직접 진행 필요)."}


def _wait_for_payment_finish(page, L, max_wait=600):
    """카드결제는 사람이 직접 하니, 다음 건으로 넘어가기 전에 그 시간을
    기다려준다. '주문완료'/'결제완료' 같은 문구가 뜨면 바로 다음으로
    넘어가고, 그 문구를 아직 실제로 확인해본 적이 없어(1차 버전) 못
    찾으면 최대 대기시간(기본 10분) 후 무조건 다음 건으로 넘어간다 -
    감지가 안 된다고 자동화가 거기서 영영 멈춰있으면 안 되기 때문."""
    waited = 0
    interval = 3
    while waited < max_wait:
        time.sleep(interval)
        waited += interval
        try:
            text = page.locator('body').inner_text()
        except Exception:
            continue
        if re.search(r'(주문|결제)\s*(이|가)?\s*완료', text):
            L('결제/주문 완료로 보이는 화면을 확인했습니다.')
            return True
    L(f'{max_wait}초를 기다려도 완료 화면을 확인하지 못했습니다 - 직접 확인해주세요. 다음 건으로 진행합니다.')
    return False


def _place_orders_impl(order_url, items, L):
    page = _get_or_launch_page(L, launch_if_missing=False)
    if page is None:
        raise RuntimeError("아직 로그인된 브라우저가 없습니다 - 먼저 '오너클랜 로그인 설정'을 눌러 로그인해주세요.")

    results = []
    for item in items:
        code = item.get('vendor_prod_id', '')
        order_id = item.get('order_id', '')
        label = f"{order_id}/{code}"
        L(f"[{label}] 발주 자동화 시작...")
        try:
            r = _place_one_order(page, order_url, item, L)
        except Exception as e:
            r = {'ok': False, 'reason': f'자동화 중 오류: {_friendly_error(e)}'}
        r['order_id'] = order_id
        results.append(r)
        L(f"[{label}] → {'성공(결제하기 클릭까지)' if r['ok'] else '실패: ' + r['reason']}")
        if r['ok']:
            L(f"[{label}] 카드결제를 직접 완료(또는 취소)해주세요 - 완료되면 자동으로 다음 건으로 넘어갑니다(최대 10분 대기).")
            _wait_for_payment_finish(page, L)
    return results


def place_orders(order_url, items):
    """[{'vendor_prod_id','option_name','qty','order_id','recipient',
    'recipient_phone','zipcode','ship_address'}, ...] 목록을 한 건씩 순서대로
    오너클랜에서 옵션선택→배송정보 입력→결제수단(카드) 선택→'결제하기'
    클릭까지 자동으로 진행한다. 카드번호 입력과 최종 결제 확정은 절대
    자동으로 하지 않는다 - 이 프로젝트는 카드정보를 코드/DB 어디에도
    저장하거나 입력하지 않는다는 원칙을 지킨다."""
    log = []

    def L(msg):
        log.append(msg)

    if platform.system() != 'Windows':
        L('이 기능은 윈도우 PC에서만 동작합니다.')
        return {'ok': False, 'log': log, 'results': []}

    if not order_url:
        L("오너클랜 주소가 설정되어 있지 않습니다. '데이터 업로드' 탭에서 '바로가기 주소 설정'으로 오너클랜 발주내역 페이지 주소를 먼저 저장해주세요.")
        return {'ok': False, 'log': log, 'results': []}

    if not items:
        L('발주 대상으로 체크된 주문이 없습니다 (표에서 "발주" 체크박스를 먼저 켜주세요).')
        return {'ok': True, 'log': log, 'results': []}

    try:
        results = _run_on_worker(_place_orders_impl, order_url, items, L)
    except Exception as e:
        L(f'자동화 중 오류 발생 - {_friendly_error(e)}')
        return {'ok': False, 'log': log, 'results': []}

    return {'ok': True, 'log': log, 'results': results}
