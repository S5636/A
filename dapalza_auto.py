# -*- coding: utf-8 -*-
"""'지금 수집' 버튼 - 다팔자(윈도우 프로그램) 화면을 직접 조작해서
기간 1개월 → 주문수집 → 엑셀 전체다운로드 → 저장까지 자동으로 수행하고,
저장된 엑셀을 곧바로 마진보드에 업로드한다.

다팔자는 웹사이트가 아니라 설치형 윈도우 프로그램이라 브라우저 자동화(Playwright)가
아니라 윈도우 UI 자동화(pywinauto)로 창의 버튼을 이름으로 찾아서 클릭하는 방식이다.
실사용 환경(사용자 PC)에서 검증이 안 된 1차 버전이라, 각 단계를 전부 로그로 남겨서
어느 단계에서 막혔는지 화면에 그대로 보여준다.
"""
import os
import platform
import time


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
        win = Desktop(backend='uia').window(title_re='.*다팔자.*')
        win.wait('visible', timeout=10)
        win.set_focus()
        L('다팔자 창을 찾았습니다.')

        try:
            L("'주문관리' 탭 클릭...")
            win.child_window(title='주문관리', control_type='TabItem').click_input()
        except Exception:
            try:
                win.child_window(title='주문관리').click_input()
            except Exception as e:
                L(f"'주문관리' 탭을 못 찾았습니다 (이미 열려있으면 무시해도 됨): {e}")
        time.sleep(1)

        L("기간을 '1개월'로 설정...")
        win.child_window(title='1개월', control_type='Button').click_input()
        time.sleep(1)

        L("'조회' 버튼 클릭...")
        win.child_window(title='조회', control_type='Button').click_input()
        time.sleep(2)

        L("'주문수집 및 통합화' 버튼 클릭...")
        win.child_window(title='주문수집 및 통합화', control_type='Button').click_input()
        L(f'수집이 끝날 때까지 {wait_after_collect}초 대기...')
        time.sleep(wait_after_collect)

        L("'엑셀' 버튼 클릭...")
        win.child_window(title='엑셀', control_type='Button').click_input()
        time.sleep(1)

        L("'엑셀 다운로드' 창에서 '전체 다운로드' 클릭...")
        dl_win = Desktop(backend='uia').window(title='엑셀 다운로드')
        dl_win.wait('visible', timeout=10)
        dl_win.child_window(title='전체 다운로드', control_type='Button').click_input()
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

        save_win.child_window(title='저장(S)', control_type='Button').click_input()
        time.sleep(1)
        try:
            confirm = Desktop(backend='uia').window(title_re='.*(덮어쓰|같은 이름).*')
            if confirm.exists(timeout=2):
                L('같은 이름 파일 덮어쓰기 확인창에서 예 클릭...')
                confirm.child_window(title='예', control_type='Button').click_input()
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
