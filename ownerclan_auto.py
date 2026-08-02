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
from urllib.parse import urlsplit

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(BASE_DIR, 'ownerclan_profile')

_state = {'pw': None, 'context': None, 'page': None}

# Playwright sync API는 만든 스레드에서만 안전하게 쓸 수 있어서, 브라우저를
# 다루는 실제 작업은 전부 이 스레드 하나에서만 실행하고, 다른 스레드(Flask 요청
# 스레드)는 큐에 작업을 넣고 결과를 기다리기만 한다.
_task_queue = queue.Queue()
_worker_started = False
_worker_start_lock = threading.Lock()


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
    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(
        PROFILE_DIR, headless=False,
        args=['--disable-blink-features=AutomationControlled'],
        no_viewport=True,
    )
    page = context.pages[0] if context.pages else context.new_page()
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    page.on('dialog', lambda d: d.accept())
    _state['pw'] = pw
    _state['context'] = context
    _state['page'] = page
    return page


def _set_window_state(page, state, L=None):
    """창을 닫지 않고 최소화(minimized)/복원(normal)만 한다 - 로그인 세션은
    브라우저 프로세스가 살아있는 한 유지되므로, 닫는 대신 이 방식으로 화면에
    보였다 안 보였다만 시킨다."""
    try:
        cdp = page.context.new_cdp_session(page)
        info = cdp.send('Browser.getWindowForTarget')
        cdp.send('Browser.setWindowBounds', {
            'windowId': info['windowId'],
            'bounds': {'windowState': state},
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
    # 다시 화면에 보이게 복원한다.
    _set_window_state(page, 'normal', L)
    L('★ 지금 새로 뜨거나 앞으로 나온 이 창(평소 쓰시는 크롬 창이 아닙니다)에서 직접 로그인해주세요. 로그인하시면 자동으로 감지해서 창을 최소화하고 백그라운드로 보냅니다 (최대 5분 대기).')

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


def _site_home_url(order_url):
    try:
        parts = urlsplit(order_url)
        return f'{parts.scheme}://{parts.netloc}/'
    except Exception:
        return 'https://ownerclan.com/'


def _check_one_stock(page, home_url, code, L):
    """상품 하나(판매사상품코드)를 오너클랜에서 검색해서 재고상태를 확인한다.
    옵션(사이즈 등) 단위까지는 못 보고 상품 전체 기준이다 - '바로구매' 버튼이
    있으면(=적어도 하나는 살 수 있는 옵션이 있으면) '정상', 없고 '품절' 표시만
    있으면 '품절', 둘 다 아니면(검색 실패 등) '확인실패'로 본다."""
    try:
        page.goto(home_url, wait_until='domcontentloaded', timeout=30000)
    except Exception as e:
        L(f"[{code}] 오너클랜 홈 이동 실패: {type(e).__name__}: {e}")
        return '확인실패'

    search_box = None
    for placeholder_guess in ('최저가 핫딜', '통합검색', '검색어를 입력'):
        try:
            loc = page.get_by_placeholder(placeholder_guess, exact=False)
            if loc.count() > 0:
                search_box = loc.first
                break
        except Exception:
            continue
    if search_box is None:
        try:
            loc = page.locator('input[type="text"], input[type="search"]').first
            if loc.count() > 0:
                search_box = loc
        except Exception:
            pass
    if search_box is None:
        L(f"[{code}] 검색창을 못 찾았습니다.")
        return '확인실패'

    try:
        search_box.fill(code)
        search_box.press('Enter')
        page.wait_for_timeout(1500)
    except Exception as e:
        L(f"[{code}] 검색 실행 실패: {type(e).__name__}: {e}")
        return '확인실패'

    # 검색 결과 목록에서 이 상품코드로 가는 링크를 찾아 들어간다. 코드 자체가
    # 결과 페이지 어딘가(링크 텍스트나 URL)에 보통 노출되니 그걸로 찾고, 못
    # 찾으면 첫 번째 상품 링크로 대체한다(검색이 이미 이 코드로 좁혀졌으므로).
    try:
        code_link = page.get_by_text(code, exact=False).first
        if code_link.count() > 0:
            code_link.click(timeout=10000)
        else:
            page.locator('a[href*="product"]').first.click(timeout=10000)
        page.wait_for_timeout(1500)
    except Exception as e:
        L(f"[{code}] 검색 결과에서 상품 페이지로 못 들어갔습니다: {type(e).__name__}: {e}")
        return '확인실패'

    try:
        has_buy_now = page.get_by_text('바로구매', exact=True).count() > 0
        has_soldout = page.get_by_text('품절', exact=False).count() > 0
    except Exception as e:
        L(f"[{code}] 상품 페이지 상태 확인 실패: {type(e).__name__}: {e}")
        return '확인실패'

    if has_buy_now:
        return '정상'
    if has_soldout:
        return '품절'
    L(f"[{code}] '바로구매'도 '품절'도 못 찾았습니다 - 상품 페이지가 맞는지 확인 필요.")
    return '확인실패'


def _check_stock_impl(order_url, codes, L):
    page = _get_or_launch_page(L, launch_if_missing=False)
    if page is None:
        L("아직 로그인된 브라우저가 없습니다 - 먼저 '오너클랜 로그인 설정'을 눌러 로그인해주세요.")
        return {}

    home_url = _site_home_url(order_url)
    results = {}
    for code in codes:
        if not code:
            continue
        L(f"[{code}] 오너클랜에서 재고상태 확인 중...")
        status = _check_one_stock(page, home_url, code, L)
        results[code] = status
        L(f"[{code}] → {status}")
    return results


def check_stock(order_url, codes):
    """판매사상품코드 목록을 받아 각각 오너클랜에서 재고상태를 확인해서
    {코드: '정상'|'품절'|'확인실패'} 딕셔너리로 돌려준다."""
    log = []

    def L(msg):
        log.append(msg)

    if platform.system() != 'Windows':
        L('이 기능은 윈도우 PC에서만 동작합니다.')
        return {'ok': False, 'log': log, 'results': {}}

    if not order_url:
        L("오너클랜 주소가 설정되어 있지 않습니다. '데이터 업로드' 탭에서 '바로가기 주소 설정'으로 오너클랜 발주내역 페이지 주소를 먼저 저장해주세요.")
        return {'ok': False, 'log': log, 'results': {}}

    try:
        import playwright  # noqa: F401
    except ImportError:
        L("playwright가 설치되어 있지 않습니다. START.bat을 다시 실행하면 자동으로 설치됩니다.")
        return {'ok': False, 'log': log, 'results': {}}

    if not codes:
        L('확인할 판매사상품코드가 없습니다 (신규주문 건이 없는 것 같아요).')
        return {'ok': True, 'log': log, 'results': {}}

    try:
        results = _run_on_worker(_check_stock_impl, order_url, codes, L)
    except Exception as e:
        L(f'자동화 중 오류 발생 - {_friendly_error(e)}')
        return {'ok': False, 'log': log, 'results': {}}

    return {'ok': True, 'log': log, 'results': results}
