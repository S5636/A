# -*- coding: utf-8 -*-
"""'오너클랜 매입 수집' - 오너클랜(웹사이트)의 주문/배송조회 페이지에서
발주내역 엑셀을 자동으로 다운로드해서 마진보드에 업로드한다.

다팔자는 설치형 윈도우 프로그램이라 화면 좌표를 더듬는 방식(pywinauto)이
필요했지만, 오너클랜은 그냥 웹사이트라서 Playwright로 HTML 요소를 이름/텍스트로
직접 찾아 조작한다 - 다팔자 자동화 때 겪은 "버튼을 못 찾는다" 류 문제가 훨씬
덜 발생하고, 화면에 아무것도 안 띄우고(headless) 완전히 백그라운드로 돌릴 수
있다는 게 가장 큰 차이다.

로그인은 최초 1회만 눈에 보이는(headed) 브라우저로 사용자가 직접 로그인하면,
그 로그인 상태(쿠키 등)를 이 파일 옆의 ownerclan_profile 폴더에 저장해서
재사용한다. 그 다음부터는 화면에 안 보이는 브라우저로 백그라운드에서 동작한다.

주의: ownerclan_profile 폴더는 로그인 세션이 들어있는 사용자의 로컬 상태라,
shop_data.db/fees_config.json/settings.json과 마찬가지로 업데이트 zip을 만들
때 절대 포함하면 안 된다 (포함하면 로그인 세션이 날아가서 매번 다시 로그인해야
함)."""
import os
import platform
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(BASE_DIR, 'ownerclan_profile')

ORDER_PAGE_PATH_HINTS = ('orderShippingSearch', 'order', '주문')


def _friendly_error(e):
    return f'{type(e).__name__}: {e}'


def setup_login(start_url, wait_seconds=300):
    """화면에 보이는(headed) 브라우저를 띄워서 사용자가 직접 로그인하게 하고,
    로그인 상태를 저장한다. 로그인 완료를 자동으로 감지하면 바로 닫고, 감지가
    안 되더라도 wait_seconds가 지나면 그때까지의 상태를 그대로 저장하고 닫는다
    (사용자가 이미 로그인해놓고 다른 걸 하고 있었을 수도 있으니, 못 찾았다고
    실패 처리하지 않는다)."""
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
        from playwright.sync_api import sync_playwright
    except ImportError:
        L("playwright가 설치되어 있지 않습니다. START.bat을 다시 실행하면 자동으로 설치됩니다.")
        return {'ok': False, 'log': log}

    try:
        os.makedirs(PROFILE_DIR, exist_ok=True)
        with sync_playwright() as p:
            L('로그인용 브라우저 창을 여는 중... (창이 뜨면 직접 로그인해주세요)')
            context = p.chromium.launch_persistent_context(
                PROFILE_DIR, headless=False,
                args=['--start-maximized', '--disable-blink-features=AutomationControlled'],
                no_viewport=True,
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page.goto(start_url, wait_until='domcontentloaded', timeout=30000)
            L('브라우저 창에서 오너클랜에 직접 로그인해주세요. 로그인하시면 자동으로 감지해서 창을 닫습니다 (최대 5분 대기).')

            logged_in = False
            deadline = time.time() + wait_seconds
            while time.time() < deadline:
                try:
                    if page.locator('text=마이페이지').count() > 0 or page.locator('text=로그아웃').count() > 0:
                        logged_in = True
                        break
                except Exception:
                    pass
                time.sleep(2)

            if logged_in:
                L('로그인이 확인됐습니다. 로그인 상태를 저장하고 창을 닫습니다.')
            else:
                L(f'{wait_seconds}초 동안 로그인 완료를 자동으로 확인하지 못했습니다 - 그래도 지금까지의 상태를 저장합니다 (이미 로그인하셨다면 문제 없습니다).')

            context.close()
        return {'ok': logged_in, 'log': log}
    except Exception as e:
        L(f'로그인 설정 중 오류 발생 - {_friendly_error(e)}')
        return {'ok': False, 'log': log}


def collect_and_upload(order_url, save_folder=None, save_filename='오너클랜.xlsx'):
    """저장된 로그인 상태로 백그라운드(headless) 브라우저를 띄워서 조회기간을
    1개월로 맞추고 엑셀다운로드를 눌러 발주내역 파일을 받는다."""
    log = []

    def L(msg):
        log.append(msg)

    if platform.system() != 'Windows':
        L('이 기능은 윈도우 PC에서만 동작합니다.')
        return {'ok': False, 'log': log}

    if not order_url:
        L("오너클랜 주소가 설정되어 있지 않습니다. '데이터 업로드' 탭에서 '바로가기 주소 설정'으로 오너클랜 발주내역 페이지 주소를 먼저 저장해주세요.")
        return {'ok': False, 'log': log}

    if not os.path.isdir(PROFILE_DIR):
        L("아직 오너클랜 로그인이 설정되어 있지 않습니다. 먼저 '오너클랜 로그인 설정'을 눌러 1회 로그인해주세요.")
        return {'ok': False, 'log': log}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        L("playwright가 설치되어 있지 않습니다. START.bat을 다시 실행하면 자동으로 설치됩니다.")
        return {'ok': False, 'log': log}

    target_dir = save_folder or os.path.join(os.path.expanduser('~'), 'Downloads')
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, save_filename)

    try:
        with sync_playwright() as p:
            L('오너클랜 페이지를 여는 중 (백그라운드)...')
            # headless=True(완전히 안 보이는 모드)로 실행했더니 오너클랜이 이걸
            # 자동화 프로그램으로 감지해서 로그인 세션을 계속 무효화시키고 매번
            # 비밀번호를 다시 요구하는 문제가 실제로 발생했다 (headless 크롬은
            # navigator.webdriver 같은 값으로 쉽게 구분됨). 그래서 화면에는 안
            # 보이지만(창을 화면 밖 좌표로 띄움) 기술적으로는 '진짜 브라우저'로
            # 인식되는 방식으로 바꿨다 - 로그인 세션이 훨씬 안정적으로 유지된다.
            context = p.chromium.launch_persistent_context(
                PROFILE_DIR, headless=False,
                args=[
                    '--window-position=-32000,-32000',
                    '--window-size=1280,900',
                    '--disable-blink-features=AutomationControlled',
                ],
                no_viewport=True,
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page.on('dialog', lambda d: d.accept())
            page.goto(order_url, wait_until='domcontentloaded', timeout=30000)
            page.wait_for_timeout(1500)

            if page.locator('text=로그인').count() > 0 and page.locator('text=마이페이지').count() == 0:
                L("로그인 상태가 아닌 것 같습니다 (로그인 유지기간이 끝났을 수 있어요) - '오너클랜 로그인 설정'을 다시 눌러 재로그인해주세요.")
                context.close()
                return {'ok': False, 'log': log}

            L("조회기간을 '1개월'로 설정...")
            try:
                page.get_by_text('1개월', exact=True).first.click(timeout=10000)
            except Exception as e:
                L(f"'1개월' 버튼을 못 찾았습니다 (기본 기간으로 진행합니다): {_friendly_error(e)}")
            page.wait_for_timeout(500)

            try:
                page.get_by_text('조회하기', exact=True).first.click(timeout=10000)
                L("'조회하기' 버튼 클릭 완료.")
                page.wait_for_timeout(1500)
            except Exception as e:
                L(f"'조회하기' 버튼을 못 찾았습니다 (그냥 다음 단계로 진행합니다): {_friendly_error(e)}")

            L("'엑셀다운로드' 버튼 클릭 및 다운로드 대기...")
            try:
                with page.expect_download(timeout=60000) as download_info:
                    page.get_by_text('엑셀다운로드', exact=True).first.click(timeout=10000)
                download = download_info.value
                download.save_as(target_path)
            except Exception as e:
                L(f"엑셀 다운로드에 실패했습니다: {_friendly_error(e)}")
                context.close()
                return {'ok': False, 'log': log}

            context.close()

        if not os.path.exists(target_path):
            L(f'다운로드는 진행됐는데 파일을 못 찾았습니다: {target_path}')
            return {'ok': False, 'log': log}

        L(f'파일 저장 확인 완료: {target_path}')
        return {'ok': True, 'log': log, 'file_path': target_path}
    except Exception as e:
        L(f'자동화 중 오류 발생 - {_friendly_error(e)}')
        return {'ok': False, 'log': log}
