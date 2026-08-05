# -*- coding: utf-8 -*-
"""'지금 수집' 버튼 - 다팔자(윈도우 프로그램) 화면을 직접 조작해서
기간 1개월 → 주문수집 → 엑셀 전체다운로드 → 저장까지 자동으로 수행하고,
저장된 엑셀을 곧바로 마진보드에 업로드한다.

다팔자는 웹사이트가 아니라 설치형 윈도우 프로그램이라 브라우저 자동화(Playwright)가
아니라 윈도우 UI 자동화(pywinauto)로 창의 버튼을 이름으로 찾아서 클릭하는 방식이다.
실사용 환경(사용자 PC)에서 검증이 안 된 1차 버전이라, 각 단계를 전부 로그로 남기고,
버튼을 못 찾으면 그 화면에 실제로 존재하는 컨트롤 이름들을 함께 로그에 남겨서
다음에 정확히 어떤 이름/타입으로 고쳐야 하는지 바로 알 수 있게 한다.
"""
import ctypes
import os
import platform
import re
import time


def _find_hwnds_by_class(class_name, exclude_handles=None):
    """UI Automation의 Desktop(backend='uia').windows()가 특정 창(윈도우 표준
    파일 저장 대화상자 등)을 목록에서 통째로 빠뜨리는 경우가 실제로 발생했다
    (60초를 기다려도 계속 없다고 나왔는데, 사용자는 화면에서 그 창을 직접 보고
    입력까지 하고 있었음). UIA 트리 열거에 의존하지 않는, 훨씬 더 원초적인
    윈도우 API(EnumWindows)로 직접 찾으면 이런 누락 문제를 피할 수 있다.
    윈도우 표준 파일 열기/저장 대화상자는 운영체제 버전에 상관없이 클래스명이
    항상 '#32770'으로 고정되어 있다."""
    exclude_handles = exclude_handles or set()
    found = []

    def _callback(hwnd, _lparam):
        try:
            if hwnd in exclude_handles:
                return True
            if not ctypes.windll.user32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
            if buf.value == class_name:
                found.append(hwnd)
        except Exception:
            pass
        return True

    wndenumproc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(_callback)
    ctypes.windll.user32.EnumWindows(wndenumproc, 0)
    return found


def bring_marginboard_to_front(log=None):
    """DPJ 자동화가 끝난 뒤 다팔자 창이 화면 맨 앞에 그대로 남아있으면,
    사용자가 자동화가 끝났는지 아닌지 바로 알기 어렵다는 피드백을 받았다.
    제목에 '이유상점 Margin Board'가 들어간 창(마진보드 브라우저 탭)을 찾아서
    맨 앞으로 가져온다. 그냥 SetForegroundWindow만 부르면 백그라운드
    프로세스가 호출한다는 이유로 윈도우 보안 정책 때문에 조용히 무시되는
    경우가 많아서, AttachThreadInput으로 우회하는 표준 기법을 같이 쓴다."""
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        found = []

        def _callback(hwnd, _lparam):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length == 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if '이유상점 Margin Board' in buf.value:
                    found.append(hwnd)
            except Exception:
                pass
            return True

        wndenumproc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(_callback)
        user32.EnumWindows(wndenumproc, 0)
        if not found:
            if log is not None:
                log("마진보드 브라우저 창을 찾지 못해 화면 앞으로 가져오지 못했습니다(무시하고 계속 진행).")
            return False
        hwnd = found[0]
        # 최소화 상태였다면 그냥 복원(SW_RESTORE)만 하면 예전에 남아있던
        # 이상한 크기/위치(예: 화면 옆으로 찌그러진 상태)로 그대로 돌아오는
        # 사고가 있었다 - 항상 최대화(SW_MAXIMIZE)로 띄워서 매번 온전한
        # 크기로 보이게 한다.
        SW_MAXIMIZE = 3
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
        fg_hwnd = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None)
        target_thread = user32.GetWindowThreadProcessId(hwnd, None)
        current_thread = kernel32.GetCurrentThreadId()
        user32.AttachThreadInput(current_thread, fg_thread, True)
        user32.AttachThreadInput(current_thread, target_thread, True)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(current_thread, fg_thread, False)
        user32.AttachThreadInput(current_thread, target_thread, False)
        return True
    except Exception as e:
        if log is not None:
            log(f"창을 앞으로 가져오는 데 실패했지만 무시하고 계속 진행합니다: {type(e).__name__}: {e}")
        return False


def _wrap_hwnd_uia(hwnd):
    # UIAElementInfo의 실제 생성자 매개변수 이름은 'handle'이 아니라
    # 'handle_or_elem'이다 (이걸 몰라서 handle=hwnd로 호출했다가 매 시도마다
    # TypeError가 나서 저장창을 찾고도 못 쓰는 사고가 실제로 발생했다).
    # 위치 인자로 넘기면 pywinauto 버전별 매개변수 이름 차이와도 상관없다.
    from pywinauto.uia_element_info import UIAElementInfo
    from pywinauto.controls.uiawrapper import UIAWrapper
    return UIAWrapper(UIAElementInfo(hwnd))


def _find_control(win, title, control_type=None):
    """정확한 title+control_type 매칭을 먼저 시도하고, 안 되면 점점 느슨하게 찾는다.
    같은 이름의 컨트롤이 여러 개면 pywinauto가 ElementAmbiguousError를 던지는데,
    found_index=0으로 '여러 개 중 첫 번째'를 명시해서 그런 경우도 못 찾은 걸로
    처리되지 않게 한다."""
    if control_type:
        try:
            ctrl = win.child_window(title=title, control_type=control_type, found_index=0)
            if ctrl.exists(timeout=1):
                return ctrl
        except Exception:
            pass
    try:
        ctrl = win.child_window(title=title, found_index=0)
        if ctrl.exists(timeout=1):
            return ctrl
    except Exception:
        pass
    try:
        ctrl = win.child_window(title_re=f'.*{re.escape(title)}.*', found_index=0)
        if ctrl.exists(timeout=1):
            return ctrl
    except Exception:
        pass
    return None


def _find_smallest_text_match(win, title, log=None, descendants=None):
    """다팔자 화면이 버튼 하나하나를 따로 노출하는 게 아니라, 화면 전체 글자를
    큰 덩어리 하나(웹뷰 컨테이너)로 노출하고 있을 수 있다. pywinauto의
    child_window 이름 매칭은 정확한 하나의 컨트롤을 찾는 방식이라 이런 큰 덩어리
    안에 파묻힌 글자는 못 찾는다. 그래서 화면의 모든 요소를 직접 훑어서 title을
    포함하는 요소들을 전부 모으고, 그 중 화면에서 차지하는 면적이 제일 작은 것을
    고른다 (작을수록 그 버튼 자체일 가능성이 높고, 큰 덩어리를 클릭하면 엉뚱한
    위치를 누르게 된다). descendants를 미리 받으면 중복으로 트리를 다시 훑지
    않는다 (다팔자처럼 트리가 크고 느린 앱에서 반복 조회가 타임아웃/에러로
    이어지는 걸 줄이기 위함)."""
    candidates = []
    try:
        if descendants is None:
            descendants = win.descendants()
        for c in descendants:
            try:
                t = c.window_text().strip()
            except Exception:
                continue
            if t == title or title in t:
                # 화면에 실제로 보이지 않는(숨겨진/제거 대기중인) 요소는 좌표가
                # 남아있는 유령 요소일 수 있다 - 이런 걸 '면적이 작다'고 골라서
                # 클릭하면 그 좌표에 실제로 떠 있는 엉뚱한 화면 요소(예: 사이드바의
                # 다른 마켓 항목)가 대신 눌린다. 그래서 안 보이는 요소는 아예 제외한다.
                try:
                    if not c.is_visible():
                        continue
                except Exception:
                    pass
                try:
                    r = c.rectangle()
                    area = max(0, r.width()) * max(0, r.height())
                except Exception:
                    area = float('inf')
                if area <= 0:
                    continue
                exact = (t == title)
                candidates.append((0 if exact else 1, area, len(t), c))
    except Exception as e:
        if log is not None:
            log(f"'{title}' 요소 탐색 중 오류: {type(e).__name__}: {e}")
        return None
    if not candidates:
        if log is not None:
            log(f"'{title}' 글자를 포함하는 요소를 하나도 못 찾았습니다 (총 {len(descendants) if descendants else 0}개 요소 중).")
        return None
    # 이름이 정확히 일치하는 요소를 우선하고(부분 일치 텍스트 덩어리보다 신뢰도가
    # 높음), 그다음 면적이 작은 순으로 고른다.
    candidates.sort(key=lambda x: (x[0], x[1], x[2]))
    if log is not None:
        top = [(ex, round(a), n) for ex, a, n, _ in candidates[:5]]
        log(f"'{title}' 텍스트를 포함하는 요소 {len(candidates)}개 중 정확일치/면적 작은 순 상위 (0=정확일치): {top}")
    return candidates[0][3]


def _find_near(anchor, title, log=None, max_up=10):
    """전체 창(963개 요소)을 다 훑어서 title을 찾으면, 완전히 동떨어진 곳에
    우연히 같은 글자가 들어간 요소(예: 주문 목록 어딘가의 텍스트)를 잘못 골라서
    엉뚱한 좌표를 클릭하는 사고가 실제로 발생했다 ('확인'을 찾다가 TOSS 관련
    요소를 클릭한 사례). 그래서 anchor(예: 팝업 안의 '완료하였습니다' 텍스트
    요소)의 조상을 위로 올라가면서, 그 조상 범위 안에서만 title을 찾는다 - 같은
    모달/패널 안에 있을 가능성이 훨씬 높아서 오탐이 크게 줄어든다. 작은 범위부터
    순서대로 시도해서 처음 찾은 곳(=가장 좁은 범위)을 바로 채택한다. (이전에는
    후보 범위 크기에 상한선(200개)을 둬서, 모달 자체가 200개보다 큰 경우 계속
    건너뛰다가 결국 못 찾는 버그가 있었다 - 실제로 발생해서 상한선을 없앴다.)"""
    container = anchor
    for _ in range(max_up):
        try:
            parent = container.parent()
        except Exception:
            break
        if parent is None:
            break
        container = parent
        try:
            desc = container.descendants()
        except Exception:
            continue
        if len(desc) < 2:
            # 범위가 너무 좁으면(자기 자신 근처만) 아직 버튼이 안 들어있을 수
            # 있으니 계속 위로 올라간다.
            continue
        match = _find_smallest_text_match(container, title, None, descendants=desc)
        if match is not None:
            if log is not None:
                log(f"'{title}' - 근처 범위({len(desc)}개 요소) 안에서 발견.")
            return match
    return None


def _dump_controls(win, limit=50, descendants=None):
    try:
        if descendants is None:
            descendants = win.descendants()
        texts = []
        for c in descendants:
            try:
                t = c.window_text().strip()
            except Exception:
                continue
            if t:
                texts.append(t)
        seen = []
        for t in texts:
            if t not in seen:
                seen.append(t)
        return seen[:limit]
    except Exception as e:
        return [f'(진단정보 수집도 실패: {e})']


def _dump_structure(win, descendants=None):
    """텍스트가 있는 컨트롤만 보면 안 보이는 경우를 위해, 클래스명/컨트롤타입까지
    전부(빈 글자 포함) 찍어서 UIA가 이 창의 내용을 얼마나 볼 수 있는지 확인한다."""
    try:
        cls = win.class_name()
    except Exception as e:
        cls = f'(조회 실패: {e})'
    try:
        desc = descendants if descendants is not None else win.descendants()
        info = []
        for c in desc[:80]:
            try:
                info.append(f"{c.friendly_class_name()}/{c.element_info.control_type}:'{c.window_text().strip()}'")
            except Exception:
                info.append('(조회실패)')
        return cls, len(desc), info
    except Exception as e:
        return cls, 0, [f'(하위 요소 조회 실패: {e})']


def _click(win, title, control_type=None, log=None):
    # 트리를 한 번만 훑어서 여러 곳에 재사용한다 - 다팔자처럼 트리가 크고 느린
    # 앱에서 같은 창을 반복해서 훑으면 그 자체가 타임아웃/에러로 이어질 수 있다.
    try:
        descendants = win.descendants()
    except Exception as e:
        descendants = None
        if log is not None:
            log(f"화면 요소 목록을 가져오는 데 실패했습니다: {type(e).__name__}: {e}")

    ctrl = _find_smallest_text_match(win, title, log, descendants=descendants)
    if ctrl is None:
        ctrl = _find_control(win, title, control_type)
    if ctrl is None:
        if log is not None:
            visible = _dump_controls(win, descendants=descendants)
            log(f"'{title}' 버튼(컨트롤)을 찾지 못했습니다. 지금 이 화면에 보이는 글자들: {visible}")
        raise RuntimeError(f"'{title}'를 찾지 못했습니다.")
    ctrl.click_input()


def _dismiss_invalid_filename_dialog(log=None):
    """저장창의 파일 목록에서 이미 선택돼 있는 항목을 실수로 한 번 더 클릭하면
    윈도우 탐색기가 그걸 '이름 바꾸기' 시작 신호로 받아들여서, 그 상태에서
    경로 문자열(콜론/역슬래시 포함)을 입력하면 '파일 이름에는 다음 문자를
    사용할 수 없습니다' 오류창이 뜬다. 이 오류가 뜬 시점엔 실제 파일은
    아직 바뀌지 않은 상태(취소 대기)라, 확인을 눌러 닫아주기만 하면 안전하다."""
    try:
        dlg = Desktop(backend='uia').window(title_re='.*이름\\s*바꾸기.*')
        if dlg.exists(timeout=1):
            if log is not None:
                log('실수로 파일 목록의 항목이 "이름 바꾸기" 모드로 들어가 오류창이 떴습니다 (파일은 바뀌지 않았습니다) - 닫고 계속 진행합니다.')
            try:
                _click(dlg, '확인', 'Button', log)
            except Exception:
                dlg.type_keys('{ENTER}')
            time.sleep(0.3)
            return True
    except Exception:
        pass
    return False


def collect_and_upload(save_folder=None, save_filename='다팔자_자동수집.xlsx', wait_after_collect=10):
    log = []

    def L(msg):
        log.append(msg)

    if platform.system() != 'Windows':
        L('이 기능은 윈도우 PC에서만 동작합니다 (다팔자가 윈도우 프로그램이라서요).')
        return {'ok': False, 'log': log}

    try:
        from pywinauto import Desktop
    except ImportError:
        L("pywinauto가 설치되어 있지 않습니다. START.bat을 다시 실행하면 자동으로 설치됩니다 (안 되면 콘솔창에 뜨는 에러를 캡처해서 보내주세요).")
        return {'ok': False, 'log': log}

    try:
        L('다팔자 창을 찾는 중...')
        desktop = Desktop(backend='uia')
        win = None
        for attempt in range(6):
            try:
                candidates = [w for w in desktop.windows() if '다팔자' in w.window_text()]
            except Exception:
                candidates = []
            if candidates:
                # 제목에 '다팔자'가 들어간 창이 여러 개(알림창 등)일 수 있어서,
                # 그 중 화면에 차지하는 면적이 가장 큰 것을 실제 메인 창으로 간주한다.
                def _area(w):
                    try:
                        r = w.rectangle()
                        return max(0, r.width()) * max(0, r.height())
                    except Exception:
                        return 0
                candidates.sort(key=_area, reverse=True)
                L(f"'다팔자'가 제목에 들어간 창 {len(candidates)}개 발견 (면적 큰 순): " +
                  str([(c.window_text(), _area(c)) for c in candidates]))
                win = candidates[0]
                break
            time.sleep(2)
        if win is None:
            try:
                titles = [w.window_text() for w in desktop.windows() if w.window_text().strip()]
            except Exception as e:
                titles = [f'(창 목록 조회 실패: {e})']
            L(f"다팔자 창을 찾지 못했습니다. 지금 열려있는 창 제목들: {titles}")
            return {'ok': False, 'log': log}
        # desktop.windows()가 돌려주는 건 WindowSpecification이 아니라 UIAWrapper라서
        # .wait()가 없다 - 이미 존재가 확인된 요소이니 포커스만 주면 된다.
        win.set_focus()
        L('다팔자 창을 찾았습니다.')

        # 다팔자는 클래스명이 Chrome_WidgetWin_1(Electron/Chromium 기반)이다.
        # Electron 앱은 접근성 트리를 처음부터 만들어두지 않고, 윈도우 접근성
        # 도구(UIA 등)가 창을 건드려야 그때부터 만들기 시작하는 경우가 많아서
        # 몇 초 정도 걸릴 수 있다. 요소 개수가 초기값(제목표시줄 정도인 11개
        # 안팎)보다 뚜렷하게 늘어날 때까지 반복 확인하면서 기다린다.
        L('창 접근성 정보가 완전히 준비될 때까지 확인하는 중 (Electron 앱은 시간이 걸릴 수 있음)...')
        # 예전엔 최대 15초(10x1.5초)만 기다렸는데, 다팔자가 자체 자동동기화
        # 중이면(화면 하단에 '자동동기화 상태 갱신...' 토스트가 뜨는 그 상황)
        # 렌더러가 바빠서 15초 안에도 접근성 트리가 안 열릴 수 있다는 게
        # 실제 로그로 확인됐다 (11개에서 15초 내내 안 늘어남). 60초로 늘렸는데도
        # 그 자동동기화가 더 오래 걸리는 경우가 있어서 다시 막힌 사례가 있었다 -
        # 120초까지 한 번 더 넉넉하게 늘렸다.
        initial_descendants = []
        last_wait_log = 0
        for i in range(80):
            time.sleep(1.5)
            elapsed = round((i + 1) * 1.5, 1)
            try:
                initial_descendants = win.descendants()
                cnt = len(initial_descendants)
            except Exception as e:
                cnt = -1
                L(f'  - {elapsed}초 경과, 조회 실패: {type(e).__name__}: {e}')
                continue
            if elapsed - last_wait_log >= 6:
                L(f'  - {elapsed}초 경과, 하위 요소 {cnt}개 확인됨')
                last_wait_log = elapsed
            if cnt > 20:
                L(f'  - {elapsed}초 경과, 하위 요소 {cnt}개로 늘어남 - 준비 완료.')
                break
        visible_now = _dump_controls(win, descendants=initial_descendants)
        L(f'창을 찾은 직후 화면에 보이는 글자들 (참고용): {visible_now}')
        cls, desc_count, structure = _dump_structure(win, descendants=initial_descendants)
        L(f'창 클래스명: {cls} / 하위 요소 총 {desc_count}개')
        L(f'하위 요소 구조(클래스/타입/텍스트, 최대 80개): {structure}')

        if desc_count <= 20:
            # 120초를 다 기다려도 창 내용이 안 늘어났다 - 다팔자 자체가 아직
            # 자동동기화 등으로 바쁜 상태로 보인다. 이 상태로 계속 진행하면
            # '주문관리'/'1개월' 버튼을 못 찾는 실패로 이어질 게 뻔하니, 여기서
            # 명확한 이유를 남기고 바로 멈춘다 - 다팔자가 자체 작업을 끝낼 때까지
            # 잠깐 기다렸다가 다시 시도해달라고 안내한다.
            L('다팔자 창이 120초가 지나도 내용을 못 그려내고 있습니다 - 다팔자 자체가 자동동기화 등으로 바쁜 상태일 가능성이 큽니다.')
            raise RuntimeError('다팔자가 아직 바쁜 상태로 보입니다(자체 자동동기화 등) - 다팔자 창에서 작업이 끝난 걸 확인한 뒤 잠시 후 다시 시도해주세요.')

        try:
            L("'주문관리' 탭 클릭...")
            _click(win, '주문관리', 'TabItem', L)
        except Exception as e:
            L(f"'주문관리' 탭을 못 찾았습니다 (이미 열려있으면 무시해도 됨): {e}")
        time.sleep(1)

        L("기간을 '1개월'로 설정...")
        _click(win, '1개월', 'Button', L)
        time.sleep(1)

        L("'조회' 버튼 클릭...")
        _click(win, '조회', 'Button', L)
        time.sleep(2)

        L("'주문수집 및 동기화' 버튼 클릭...")
        _click(win, '주문수집 및 동기화', 'Button', L)

        L('수집 완료 팝업을 기다리는 중 (마켓/주문이 많으면 시간이 꽤 걸릴 수 있음)...')
        # 실제로 뜨는 '수집 완료' 팝업은 별도의 윈도우 창이 아니라, 메인 창
        # 안에서 카드 형태로 겹쳐 뜨는 내부 모달이다 (사용자 스크린샷으로 확인:
        # 진행바 100% + '주문 수집을 완료하였습니다.' 문구 + '확인' 버튼이 전부
        # 다팔자 메인 창 안에 있고, 별도 제목표시줄이 없다). 그래서 Desktop().windows()로
        # 별도 창을 찾는 방식은 애초에 안 맞았고, 메인 창 안에서 '확인' 버튼을
        # 정확한 이름으로 찾는 _find_control()만으로는 실패했었다 - 다른 버튼들처럼
        # 이것도 큰 텍스트 덩어리 안에 파묻혀 있을 수 있어서, 성공률이 검증된
        # _find_smallest_text_match()로 먼저 찾는다. '완료하였습니다' 문구가 실제로
        # 보일 때만 확인 버튼을 누르게 해서, 평소에 어딘가 있을지 모르는 엉뚱한
        # '확인' 버튼을 잘못 눌러버리는 것도 막는다.
        confirmed = False
        poll_seconds = max(wait_after_collect, 600)
        check_interval = 2
        last_progress_log = 0
        elapsed = 0
        while elapsed < poll_seconds:
            time.sleep(check_interval)
            elapsed += check_interval
            if elapsed - last_progress_log >= 15:
                L(f'  - {elapsed}초 경과, 계속 확인 중...')
                last_progress_log = elapsed

            try:
                descendants = win.descendants()
            except Exception:
                descendants = None

            done_marker = _find_smallest_text_match(win, '완료하였습니다', None, descendants=descendants)
            if done_marker is not None:
                L("'주문 수집을 완료하였습니다' 메시지 확인됨 - '확인' 버튼 찾는 중...")
                # 창 전체(963개 요소)를 다 훑으면 완전히 동떨어진 곳의 '확인'을
                # 잘못 고를 수 있어서(실제로 TOSS 관련 요소를 잘못 클릭한 사고 발생),
                # 완료 메시지 요소 근처 범위부터 먼저 찾는다.
                ok_ctrl = _find_near(done_marker, '확인', L)
                if ok_ctrl is None:
                    ok_ctrl = _find_smallest_text_match(win, '확인', L, descendants=descendants)
                if ok_ctrl is None:
                    ok_ctrl = _find_control(win, '확인', 'Button')
                if ok_ctrl is not None:
                    try:
                        ok_ctrl.click_input()
                        L("'확인' 버튼 클릭 완료.")
                        confirmed = True
                        break
                    except Exception as e:
                        L(f"'확인' 버튼 클릭 실패: {type(e).__name__}: {e}")
                else:
                    L("완료 메시지는 보이는데 '확인' 버튼을 못 찾았습니다 - 계속 재시도합니다.")

            # 혹시 별도 팝업 창으로 뜨는 구버전/다른 상황도 대비해서 함께 확인한다.
            try:
                popups = [w for w in Desktop(backend='uia').windows()
                          if '수집' in w.window_text() and w.window_text() != win.window_text()]
                if popups:
                    L(f"수집 완료 팝업(별도 창) 발견: '{popups[0].window_text()}' - 확인 클릭...")
                    _click(popups[0], '확인', 'Button', L)
                    confirmed = True
                    break
            except Exception:
                pass
        if not confirmed:
            L(f'{poll_seconds}초 안에 수집 완료 확인 팝업을 못 찾았습니다 - 그냥 다음 단계로 진행합니다.')
        time.sleep(1)

        L("'엑셀' 버튼 클릭...")
        _click(win, '엑셀', 'Button', L)
        time.sleep(1)

        # '수집 완료'와 마찬가지로 '엑셀 다운로드' 패널도 별도 창이 아니라
        # 메인 창 안에 겹쳐 뜨는 내부 패널일 가능성이 높아서, 먼저 메인 창
        # 안에서 '전체 다운로드'를 찾아보고, 그래도 없을 때만 별도 창을 확인한다.
        L("'전체 다운로드' 버튼을 찾는 중...")
        dl_ctrl = None
        for i in range(30):
            try:
                descendants = win.descendants()
            except Exception:
                descendants = None
            dl_ctrl = _find_smallest_text_match(win, '전체 다운로드', None, descendants=descendants)
            if dl_ctrl is not None:
                break
            time.sleep(1)
        # '전체 다운로드'를 누르면 새 저장창(윈도우 표준 파일 저장 대화상자)이
        # 뜬다 - 이 창을 나중에 제목으로 찾으려다가, 사용자가 이미 열어둔 다른
        # 탐색기 창(제목에 '다운로드'가 들어간 경우 등)을 잘못 잡는 사고가 실제로
        # 반복해서 발생했다. 그래서 제목으로 찾는 대신, 지금 이 순간까지 열려있던
        # 모든 창의 핸들을 미리 저장해두고, 클릭 이후에 '새로 생긴 창'만 후보로
        # 삼는다 - 기존에 열려있던 창은 애초에 후보에서 제외되니 훨씬 안전하다.
        try:
            pre_download_handles = set(w.handle for w in Desktop(backend='uia').windows())
        except Exception:
            pre_download_handles = set()
        try:
            pre_dialog_hwnds = set(_find_hwnds_by_class('#32770'))
        except Exception:
            pre_dialog_hwnds = set()

        if dl_ctrl is not None:
            L("메인 창 안에서 '전체 다운로드' 버튼 발견 - 클릭...")
            dl_ctrl.click_input()
        else:
            # 지금까지 확인된 바로는 별도 창이 아니라 메인 창 안 패널이었지만,
            # 혹시 모를 다른 상황(버전 차이 등)을 대비해 짧게만 별도 창도 확인한다.
            L("메인 창 안에서 못 찾아서 혹시 모를 별도 '엑셀 다운로드' 창을 잠깐 확인합니다...")
            try:
                dl_win = Desktop(backend='uia').window(title='엑셀 다운로드')
                dl_win.wait('visible', timeout=5)
                _click(dl_win, '전체 다운로드', 'Button', L)
            except Exception as e:
                L(f"별도 창도 없었습니다: {type(e).__name__}: {e} - 그래도 다음 단계로 진행합니다.")
        time.sleep(2)

        L('파일 저장 대화상자를 찾는 중...')
        # 직전 시도에서 60초를 기다려도 Desktop(backend='uia').windows()가 이
        # 저장창을 목록에서 아예 빠뜨리는 게 확인됐다 (사용자는 화면에서 그 창을
        # 직접 보고 입력까지 하고 있었는데 로그는 '없다'고 나옴) - 이건 UI
        # Automation 쪽 열거 방식 자체의 한계로 보인다. 그래서 훨씬 더 원초적인
        # 방법을 최우선으로 쓴다: 윈도우 표준 파일 저장/열기 대화상자는 운영체제
        # 버전에 상관없이 창 클래스명이 항상 '#32770'으로 고정돼 있다 - 이걸
        # EnumWindows로 직접 찾으면 UIA 열거가 놓치는 경우도 잡을 수 있다.
        # 그래도 안 되면 예전 방식(새로 생긴 창 / 정확한 제목)을 순서대로 더 써본다.
        save_win = None
        for i in range(60):
            try:
                new_dialogs = [h for h in _find_hwnds_by_class('#32770') if h not in pre_dialog_hwnds]
            except Exception:
                new_dialogs = []
            if new_dialogs:
                try:
                    save_win = _wrap_hwnd_uia(new_dialogs[0])
                    L(f"클래스명(#32770)으로 저장창 발견: '{save_win.window_text()}'.")
                    break
                except Exception as e:
                    L(f'저장창을 UIA로 감싸는 데 실패: {type(e).__name__}: {e}')

            try:
                new_windows = [w for w in Desktop(backend='uia').windows()
                                if w.handle not in pre_download_handles]
            except Exception:
                new_windows = []
            if new_windows:
                named = [w for w in new_windows
                         if any(k in w.window_text() for k in ('다운로드', '저장', '엑셀'))]
                save_win = named[0] if named else new_windows[0]
                L(f"새로 나타난 창 발견: '{save_win.window_text()}' - 이걸 저장창으로 사용합니다.")
                break

            try:
                titled = Desktop(backend='uia').window(title_re='(?i).*주문.*(엑셀|excel).*다운로드.*')
                if titled.exists(timeout=0):
                    save_win = titled
                    L(f"정확한 제목으로 저장창 발견: '{save_win.window_text()}'.")
                    break
            except Exception:
                pass

            if i == 20:
                try:
                    all_titles = [w.window_text() for w in Desktop(backend='uia').windows() if w.window_text().strip()]
                except Exception as e:
                    all_titles = [f'(조회 실패: {e})']
                L(f'아직 저장창을 못 찾았습니다 (20초 경과). 지금 열려있는 창 제목들: {all_titles}')
            time.sleep(1)
        if save_win is None:
            L('60초 안에 저장창을 못 찾았습니다 - 엉뚱한 창을 잘못 잡느니 여기서 멈춥니다.')
            raise RuntimeError('저장 대화상자를 찾지 못했습니다.')
        # save_win이 WindowSpecification이면 .wait()가 있지만, EnumWindows나
        # Desktop().windows()로 직접 찾은 경우는 UIAWrapper라서 .wait()가 없다
        # (이미 찾은 시점에 존재/화면표시가 확인된 창이니 굳이 또 기다릴 필요도 없다).
        try:
            save_win.wait('visible', timeout=30)
        except AttributeError:
            pass

        target_dir = save_folder or os.path.join(os.path.expanduser('~'), 'Downloads')
        os.makedirs(target_dir, exist_ok=True)
        target_path = os.path.join(target_dir, save_filename)

        # 같은 이름의 파일이 이미 그 폴더에 남아있으면 나중에 이름 바꿀 때
        # 걸리니 미리 지운다.
        if os.path.exists(target_path):
            try:
                os.remove(target_path)
                L(f'기존에 남아있던 같은 이름의 파일을 먼저 정리했습니다: {target_path}')
            except Exception as e:
                L(f'기존 파일 정리 실패(무시하고 진행): {type(e).__name__}: {e}')

        # 저장창의 '파일 이름' 칸을 직접 조작해서 우리가 원하는 경로를 넣는
        # 방식은 여러 번 사고가 났다 - 값 설정이 COM 에러로 실패 -> 예비로
        # 그 칸을 클릭하는데 그 좌표가 낡아서 목록의 다른 파일을 잘못 클릭 ->
        # 윈도우가 그걸 '이름 바꾸기' 시작으로 오인 -> 거기에 경로 문자열
        # (콜론/역슬래시)을 넣으니 '파일 이름에 이 문자는 못 쓴다'는 오류가
        # 반복해서 났다. 그래서 아예 그 칸을 절대 건드리지 않기로 바꿨다:
        # 다팔자가 제안하는 기본 파일명 그대로 Enter만 눌러 저장시키고,
        # 저장이 끝난 뒤 폴더에 새로 생긴 파일을 파이썬 os.replace()로
        # 우리가 원하는 이름으로 바꾼다 - 이건 화면을 전혀 건드리지 않는
        # 순수 파일시스템 작업이라 이런 종류의 사고 자체가 날 수가 없다.
        try:
            before_files = {f for f in os.listdir(target_dir) if f.lower().endswith('.xlsx')}
        except Exception:
            before_files = set()

        L('저장창의 파일이름 칸은 건드리지 않고, 다팔자가 제안하는 기본 파일명 그대로 저장한 뒤 파이썬으로 안전하게 이름을 바꿉니다...')
        try:
            save_win.set_focus()
        except Exception:
            pass
        try:
            save_win.type_keys('{ENTER}')
            L('Enter로 저장 실행.')
        except Exception as e:
            L(f'Enter 입력 실패({type(e).__name__}: {e}) - 저장 버튼을 직접 찾아 클릭 시도...')
            _click(save_win, '저장', 'Button', L)
        time.sleep(1)
        _dismiss_invalid_filename_dialog(L)
        try:
            confirm = Desktop(backend='uia').window(title_re='.*(덮어쓰|같은 이름).*')
            if confirm.exists(timeout=2):
                L('같은 이름 파일 덮어쓰기 확인창에서 예 클릭...')
                _click(confirm, '예', 'Button', L)
        except Exception:
            pass

        def _find_new_xlsx():
            try:
                now_files = {f for f in os.listdir(target_dir) if f.lower().endswith('.xlsx')}
            except Exception:
                return None
            candidates = []
            for f in (now_files - before_files):
                p = os.path.join(target_dir, f)
                try:
                    if (time.time() - os.path.getmtime(p)) < 90:
                        candidates.append(p)
                except Exception:
                    continue
            if not candidates:
                return None
            candidates.sort(key=os.path.getmtime, reverse=True)
            return candidates[0]

        L('새로 저장된 파일을 찾는 중...')
        new_file_path = None
        for _ in range(20):
            new_file_path = _find_new_xlsx()
            if new_file_path:
                break
            time.sleep(1)

        if new_file_path is None:
            # Enter가 다른 창(사용자가 그 사이 클릭한 다른 프로그램 등)으로 샜을
            # 수 있으니, 저장창이 아직 열려있다면 이번엔 '저장' 버튼을 직접 찾아서
            # 마지막으로 한 번 더 시도해본다.
            L('아직 새 파일을 못 찾았습니다 - 저장창이 남아있으면 저장 버튼을 직접 클릭해서 재시도합니다...')
            try:
                try:
                    still_there = save_win.exists(timeout=2)
                except AttributeError:
                    # EnumWindows로 직접 찾은 경우는 UIAWrapper라서 .exists()가
                    # 없다 - is_visible()로 대신 확인한다.
                    still_there = save_win.is_visible()
                if still_there:
                    save_win.set_focus()
                    _click(save_win, '저장', 'Button', L)
                    time.sleep(1)
                    _dismiss_invalid_filename_dialog(L)
                    try:
                        confirm = Desktop(backend='uia').window(title_re='.*(덮어쓰|같은 이름).*')
                        if confirm.exists(timeout=2):
                            _click(confirm, '예', 'Button', L)
                    except Exception:
                        pass
                    for _ in range(10):
                        new_file_path = _find_new_xlsx()
                        if new_file_path:
                            break
                        time.sleep(1)
            except Exception as e:
                L(f'재시도 중 오류: {type(e).__name__}: {e}')
        if new_file_path is None:
            L(f'저장된 파일을 끝내 못 찾았습니다: {target_dir} 폴더에 새 xlsx 파일이 안 보입니다 (자동화 중 다른 창을 조작하면 Enter/클릭이 엉뚱한 곳으로 샐 수 있어요 - 자동화가 끝날 때까지 다른 창은 건드리지 말아주세요)')
            return {'ok': False, 'log': log}

        try:
            if os.path.exists(target_path):
                os.remove(target_path)
            os.replace(new_file_path, target_path)
            L(f'파일 저장 확인 완료 - 원하는 이름으로 바꿨습니다: {target_path}')
        except Exception as e:
            L(f'파일은 저장됐지만 이름 바꾸기에 실패({type(e).__name__}: {e}) - 원래 이름 그대로 사용합니다: {new_file_path}')
            target_path = new_file_path

        # 다팔자 자체가 저장 완료 후 '전체 주문관리 엑셀이 저장되었습니다' 같은
        # 확인 팝업을 추가로 띄운다. 이걸 안 닫아두면 다음번 '지금 수집' 실행이
        # 이 팝업이 화면에 남아있는 상태로 시작하게 돼서 다음 자동화가 엉뚱하게
        # 동작할 위험이 있다 - 여기서 확인/예 버튼을 찾아 눌러서 정리한다. 못
        # 찾아도 실패로 처리하진 않는다 (파일 저장 자체는 이미 확인됐으므로).
        try:
            time.sleep(1)
            descendants = win.descendants()
            done_marker = _find_smallest_text_match(win, '저장되었습니다', None, descendants=descendants)
            if done_marker is not None:
                ok_ctrl = _find_near(done_marker, '예', L) or _find_near(done_marker, '확인', L)
                if ok_ctrl is not None:
                    ok_ctrl.click_input()
                    L('저장 완료 팝업을 확인 눌러서 정리했습니다 (다음 실행에 영향 안 주도록).')
                else:
                    L('저장 완료 팝업은 보이는데 확인/예 버튼을 못 찾았습니다 - 수동으로 닫아주세요.')
        except Exception as e:
            L(f'저장 완료 팝업 정리 중 오류(파일 저장 자체는 이미 확인됐으니 무시해도 됨): {type(e).__name__}: {e}')

        return {'ok': True, 'log': log, 'file_path': target_path}
    except Exception as e:
        L(f'자동화 중 오류 발생 - {type(e).__name__}: {e}')
        return {'ok': False, 'log': log}
