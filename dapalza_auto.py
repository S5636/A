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
import os
import platform
import re
import time


def _find_control(win, title, control_type=None):
    """정확한 title+control_type 매칭을 먼저 시도하고, 안 되면 점점 느슨하게 찾는다."""
    if control_type:
        try:
            ctrl = win.child_window(title=title, control_type=control_type)
            if ctrl.exists(timeout=1):
                return ctrl
        except Exception:
            pass
    try:
        ctrl = win.child_window(title=title)
        if ctrl.exists(timeout=1):
            return ctrl
    except Exception:
        pass
    try:
        ctrl = win.child_window(title_re=f'.*{re.escape(title)}.*')
        if ctrl.exists(timeout=1):
            return ctrl
    except Exception:
        pass
    return None


def _dump_controls(win, limit=50):
    try:
        texts = []
        for c in win.descendants():
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


def _click(win, title, control_type=None, log=None):
    ctrl = _find_control(win, title, control_type)
    if ctrl is None:
        if log is not None:
            visible = _dump_controls(win)
            log(f"'{title}' 버튼(컨트롤)을 찾지 못했습니다. 지금 이 화면에 보이는 글자들: {visible}")
        raise RuntimeError(f"'{title}'를 찾지 못했습니다.")
    ctrl.click_input()


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
        for _ in range(6):
            try:
                candidate = desktop.window(title_re='.*다팔자.*')
                if candidate.exists(timeout=5):
                    win = candidate
                    break
            except Exception:
                pass
            time.sleep(2)
        if win is None:
            try:
                titles = [w.window_text() for w in desktop.windows() if w.window_text().strip()]
            except Exception as e:
                titles = [f'(창 목록 조회 실패: {e})']
            L(f"다팔자 창을 찾지 못했습니다. 지금 열려있는 창 제목들: {titles}")
            return {'ok': False, 'log': log}
        win.wait('visible', timeout=15)
        win.set_focus()
        L('다팔자 창을 찾았습니다.')

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

        L("'주문수집 및 통합화' 버튼 클릭...")
        _click(win, '주문수집 및 통합화', 'Button', L)
        L(f'수집이 끝날 때까지 {wait_after_collect}초 대기...')
        time.sleep(wait_after_collect)

        L("'엑셀' 버튼 클릭...")
        _click(win, '엑셀', 'Button', L)
        time.sleep(1)

        L("'엑셀 다운로드' 창에서 '전체 다운로드' 클릭...")
        dl_win = Desktop(backend='uia').window(title='엑셀 다운로드')
        dl_win.wait('visible', timeout=10)
        _click(dl_win, '전체 다운로드', 'Button', L)
        time.sleep(1.5)

        L('파일 저장 대화상자를 찾는 중...')
        save_win = Desktop(backend='uia').window(title_re='.*(다운로드|저장).*')
        save_win.wait('visible', timeout=10)

        target_dir = save_folder or os.path.join(os.path.expanduser('~'), 'Downloads')
        os.makedirs(target_dir, exist_ok=True)
        target_path = os.path.join(target_dir, save_filename)

        L(f'저장 경로를 지정: {target_path}')
        try:
            edit_ctrl = save_win.child_window(control_type='Edit', found_index=0)
            edit_ctrl.set_edit_text(target_path)
        except Exception as e:
            L(f'저장 경로 입력에 실패해서 다팔자가 제안한 기본 파일명으로 저장을 진행합니다: {e}')

        _click(save_win, '저장(S)', 'Button', L)
        time.sleep(1)
        try:
            confirm = Desktop(backend='uia').window(title_re='.*(덮어쓰|같은 이름).*')
            if confirm.exists(timeout=2):
                L('같은 이름 파일 덮어쓰기 확인창에서 예 클릭...')
                _click(confirm, '예', 'Button', L)
        except Exception:
            pass

        L('파일이 실제로 저장됐는지 확인하는 중...')
        for _ in range(20):
            if os.path.exists(target_path) and (time.time() - os.path.getmtime(target_path)) < 60:
                break
            time.sleep(1)
        else:
            L(f'저장된 파일을 끝내 못 찾았습니다: {target_path} (파일명 입력이 실패했을 수 있어요)')
            return {'ok': False, 'log': log}

        L(f'파일 저장 확인 완료: {target_path}')
        return {'ok': True, 'log': log, 'file_path': target_path}
    except Exception as e:
        L(f'자동화 중 오류 발생 - {type(e).__name__}: {e}')
        return {'ok': False, 'log': log}
