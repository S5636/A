# -*- coding: utf-8 -*-
"""이유상점 Margin Board - 웹 대시보드 (Flask)

실행: python app.py  →  http://127.0.0.1:5000
"""
import io
import os
import json
import re
import sqlite3
import time
import traceback

from flask import Flask, request, jsonify, render_template, send_from_directory
from werkzeug.exceptions import HTTPException

import calc_engine as ce
import parsers
import vat_parser as vat
import dapalza_auto
import ownerclan_auto

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'shop_data.db')
FEES_PATH = os.path.join(BASE_DIR, 'fees_config.json')
SETTINGS_PATH = os.path.join(BASE_DIR, 'settings.json')
SETTLEMENT_UPLOAD_DIR = r'C:\Users\SEONG\Desktop\이유상점_정산\정산_업로드_대기'
# STOCK(재고확인) 대상에서 제외할, 이미 다 끝난 주문상태(사용자 지적: "배송완료
# 구매확정 반품 취소는 할필요가 없는데") - 취소/반품 계열은 calc_engine의
# is_cancelled가 걸러주지만, 정상적으로 다 끝난 배송완료/구매확정/교환완료는
# is_cancelled가 아니라서 따로 걸러야 한다.
FINISHED_SELL_STATUSES = {'배송완료', '구매확정', '교환완료'}

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB
# 로컬 1인용 앱이라 static 파일(CSS/JS) 캐시를 꺼서, 업데이트 zip으로 갈아끼운 뒤
# 브라우저가 옛날 버전을 계속 보여주는 문제를 원천 차단한다.
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
# [치명적] launcher.py가 debug=False로 실행하는데, Flask는 debug=False일 때
# Jinja 템플릿(templates/index.html)을 최초 1회만 컴파일해서 메모리에 캐시해두고
# 그 뒤로는 파일이 바뀌어도 다시 안 읽는다 - 그래서 zip을 새로 풀어도, 서버
# 프로세스를 재시작하기 전까지는 build_version 숫자만 새로 보이고(이 값은
# 요청마다 파일에서 새로 읽음) 실제 화면 레이아웃/버튼 위치 등은 예전 그대로
# 계속 보이는 사고가 있었다. 항상 최신 파일을 다시 읽게 강제한다.
app.config['TEMPLATES_AUTO_RELOAD'] = True


@app.errorhandler(Exception)
def handle_uncaught_error(e):
    # /api/* 라우트에서 미처 못 잡은 예외가 새하얀 "INTERNAL SERVER ERROR" 페이지로
    # 그대로 노출되던 문제 - 콘솔에 전체 traceback을 찍고, 화면에는 JSON 에러로
    # 내려줘서 최소한 무엇이 문제인지 콘솔 창(검은 창)에서 확인할 수 있게 한다.
    # 404 같은 정상적인 HTTP 예외는 그대로 흘려보내야 한다 (안 그러면 404가 500으로 둔갑함).
    if isinstance(e, HTTPException):
        return e
    if request.path.startswith('/api/'):
        traceback.print_exc()
        return jsonify({'error': _friendly_error(e)}), 500
    raise e


def _read_with_retry(path, retries=30, delay=0.5):
    # 다팔자가 직접 디스크에 저장한 파일이라 메모리 우회가 불가능한 유일한 경로.
    # 윈도우 백신이 방금 생성된 파일을 순간적으로 잠그는 경우가 있어 짧게 재시도한다.
    last_err = None
    for _ in range(retries):
        try:
            with open(path, 'rb') as f:
                return f.read()
        except PermissionError as e:
            last_err = e
            time.sleep(delay)
    raise last_err


def _friendly_error(e):
    # 파이썬 예외 원문(영어 클래스명, WinError 코드 등)을 그대로 화면에 띄우면
    # 개발자가 아닌 사용자는 못 알아본다 - 흔한 케이스는 평범한 한국어 문장으로 바꾸고,
    # 나머지는 콘솔 창에서 확인하라고 안내한다.
    if isinstance(e, PermissionError):
        return '파일에 일시적으로 접근할 수 없었습니다 (보안 프로그램이 검사 중일 수 있어요). 잠시 후 다시 시도해주세요.'
    return f'처리 중 오류가 발생했습니다. 콘솔 창(검은 화면)에 자세한 내용이 남았어요. ({type(e).__name__})'


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS merged_orders (
        order_id TEXT PRIMARY KEY, source TEXT, market TEXT, sell_status TEXT, buy_status TEXT,
        order_date TEXT, prod_id TEXT, prod_name TEXT, qty TEXT, order_amt TEXT, ship_fee TEXT,
        add_ship_fee TEXT, fee_rate TEXT, market_fee TEXT, settle_amt TEXT, vendor_prod_id TEXT,
        buy_cost TEXT, buy_ship_fee TEXT, buy_total TEXT, final_margin TEXT, margin_rate TEXT,
        margin_chk TEXT DEFAULT 'AUTO', bundle_no TEXT DEFAULT '', ad_chk TEXT DEFAULT 'N',
        option_name TEXT DEFAULT '', recipient TEXT DEFAULT '', ship_address TEXT DEFAULT '',
        discount_amt TEXT DEFAULT '0', settle_amt_dpj TEXT DEFAULT '', recipient_phone TEXT DEFAULT '',
        zipcode TEXT DEFAULT '', raw_json TEXT DEFAULT '', order_chk TEXT DEFAULT 'N')""")
    # 이미 만들어져 있던(예전 버전) merged_orders에는 option_name 컬럼이
    # 없으므로, CREATE TABLE IF NOT EXISTS로는 안 생긴다 - 있는지 확인해서
    # 없으면 추가한다 (재고상태를 상품코드 단위가 아니라 실제 주문된 옵션
    # 단위로 정확히 확인하려면 이 컬럼이 꼭 있어야 함).
    try:
        cur.execute("PRAGMA table_info(merged_orders)")
        cols_now = {row[1] for row in cur.fetchall()}
        if 'option_name' not in cols_now:
            cur.execute("ALTER TABLE merged_orders ADD COLUMN option_name TEXT DEFAULT ''")
        # 오너클랜 발주내역에 원장주문코드 흔적이 아예 없는 합배송 건을
        # 수령인+배송지+상품+날짜로 찾아내려면(사용자 지시) 이 두 컬럼이 필요.
        if 'recipient' not in cols_now:
            cur.execute("ALTER TABLE merged_orders ADD COLUMN recipient TEXT DEFAULT ''")
        if 'ship_address' not in cols_now:
            cur.execute("ALTER TABLE merged_orders ADD COLUMN ship_address TEXT DEFAULT ''")
        # 쿠폰 할인 여부를 금액 임계값으로 추측하던 걸 버리고 다팔자가 주는
        # 실제 할인액/정산가를 그대로 쓰기 위한 컬럼, 그리고 오너클랜 자동발주
        # 배송정보 입력에 쓸 연락처/우편번호, 나중에 필요해질 원본 데이터
        # 전체(raw_json) - 전부 사용자 지시로 추가.
        if 'discount_amt' not in cols_now:
            cur.execute("ALTER TABLE merged_orders ADD COLUMN discount_amt TEXT DEFAULT '0'")
        if 'settle_amt_dpj' not in cols_now:
            cur.execute("ALTER TABLE merged_orders ADD COLUMN settle_amt_dpj TEXT DEFAULT ''")
        if 'recipient_phone' not in cols_now:
            cur.execute("ALTER TABLE merged_orders ADD COLUMN recipient_phone TEXT DEFAULT ''")
        if 'zipcode' not in cols_now:
            cur.execute("ALTER TABLE merged_orders ADD COLUMN zipcode TEXT DEFAULT ''")
        if 'raw_json' not in cols_now:
            cur.execute("ALTER TABLE merged_orders ADD COLUMN raw_json TEXT DEFAULT ''")
        # 오너클랜 자동발주 대상으로 사용자가 직접 체크한 건만 표시(사용자
        # 지시: "신규건 다 할 필요없어 내가 지정한것만").
        if 'order_chk' not in cols_now:
            cur.execute("ALTER TABLE merged_orders ADD COLUMN order_chk TEXT DEFAULT 'N'")
    except Exception:
        pass
    cur.execute("""CREATE TABLE IF NOT EXISTS purchase_ledger (
        order_id TEXT, vendor_prod_id TEXT, buy_cost TEXT, buy_ship_fee TEXT, buy_total TEXT,
        buy_status TEXT, buy_time TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS ownerclan_raw (
        원장주문코드 TEXT, 주문코드 TEXT, 주문일자 TEXT, 상품코드 TEXT, 상품명 TEXT, 배송상태 TEXT,
        주문수량 TEXT, 택배회사 TEXT, 송장번호 TEXT, 받는사람 TEXT, 보내는사람 TEXT, 총결제금액 TEXT,
        상품가격 TEXT, 배송비 TEXT, 택배송장메모 TEXT, 주문관리메모 TEXT)""")
    # 오너클랜 발주내역을 재업로드할 때마다 같은 주문이 중복 행으로 계속
    # 쌓이던 버그가 있었다 - 원장주문코드+주문코드 기준 유니크 제약이 없어서
    # parsers.py의 INSERT OR REPLACE가 실제로는 그냥 INSERT처럼 동작했고,
    # 그래서 오래된 상태(신규주문)가 나중에 조회될 때 최신 상태(결제완료)를
    # 밀어내는 일이 생겼다. 기존에 이미 쌓인 중복은 가장 나중에 들어온
    # (rowid가 큰) 행만 남기고 정리한 뒤, 다시는 중복이 안 쌓이게 유니크
    # 인덱스를 건다.
    #
    # [치명적 버그 재발견/수정] 위 유니크 인덱스가 원장주문코드+주문코드
    # 전체에 걸려있었는데, 스펙 5.2의 합배송 3단계 구조(대표행 아래
    # 하위행/합산행들은 원장주문코드가 비어있고, 주문코드도 '-'라서 clean_id를
    # 거치면 둘 다 빈 문자열('')이 됨)에서는 서로 다른 합배송 그룹의
    # 하위행/합산행들이 전부 똑같은 ('','') 키를 갖게 된다. 그 결과 매
    # 업로드/앱 재시작마다 이 DELETE와 parsers.py의 INSERT OR REPLACE가
    # ('','') 행들을 서로 밀어내서 실제로는 딱 1개 그룹의 합산행만 살아남고
    # 나머지 모든 합배송 그룹의 매입가 정보가 통째로 사라지고 있었다 -
    # "합배송은 잡히는데 매입은 하나만 잡힌다"는 증상의 진짜 원인. 원장주문코드가
    # 실제로 있는 행(단건/대표행)에만 유니크 제약을 걸어서, 정체성이 없는
    # 하위행/합산행은 절대 서로 덮어쓰지 않고 매번 그대로 쌓이게 고친다.
    try:
        cur.execute("""DELETE FROM ownerclan_raw WHERE 원장주문코드 != '' AND rowid NOT IN (
            SELECT MAX(rowid) FROM ownerclan_raw WHERE 원장주문코드 != '' GROUP BY 원장주문코드, 주문코드
        )""")
        cur.execute("DROP INDEX IF EXISTS idx_ownerclan_raw_key")
        cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_ownerclan_raw_key
            ON ownerclan_raw(원장주문코드, 주문코드) WHERE 원장주문코드 != ''""")
    except Exception:
        pass
    cur.execute("""CREATE TABLE IF NOT EXISTS vat_summary (
        market TEXT, year INTEGER, month INTEGER, category TEXT, amount INTEGER,
        PRIMARY KEY (market, year, month, category))""")
    # 판매사상품코드+주문된 옵션 단위 오너클랜 재고상태('정상'/'품절'/'확인실패')
    # 캐시 (STOCK 버튼). 처음엔 상품코드 단위로만 확인해서 옵션이 여러 개인
    # 상품은 하나라도 살아있으면 무조건 '정상'으로 잘못 나오는 문제가 있었다
    # - 실제 주문에 찍힌 그 옵션 하나만 정확히 봐야 해서 키를 (상품코드,
    # 옵션명) 조합으로 바꿨다. 예전 스키마(상품코드만 PK)로 이미 만들어져
    # 있으면 캐시일 뿐이라 그냥 지우고 새로 만든다.
    try:
        cur.execute("PRAGMA table_info(stock_check)")
        cols = {row[1] for row in cur.fetchall()}
        if cols and 'option_name' not in cols:
            cur.execute("DROP TABLE stock_check")
    except Exception:
        pass
    cur.execute("""CREATE TABLE IF NOT EXISTS stock_check (
        vendor_prod_id TEXT, option_name TEXT, status TEXT, checked_at TEXT,
        est_buy_price TEXT DEFAULT '', est_ship_fee TEXT DEFAULT '',
        PRIMARY KEY (vendor_prod_id, option_name))""")
    try:
        cur.execute("PRAGMA table_info(stock_check)")
        cols = {row[1] for row in cur.fetchall()}
        if 'est_buy_price' not in cols:
            cur.execute("ALTER TABLE stock_check ADD COLUMN est_buy_price TEXT DEFAULT ''")
        # 상품마다 배송비가 다른데(사용자 지적) 예전엔 배송비를 상품페이지에서
        # 안 읽고 과거 매입이력에서 재사용하는 추정치를 썼다 - 이제 STOCK
        # 확인 때 이 상품의 실제 배송비를 직접 읽어와서 여기 저장한다.
        if 'est_ship_fee' not in cols:
            cur.execute("ALTER TABLE stock_check ADD COLUMN est_ship_fee TEXT DEFAULT ''")
    except Exception:
        pass
    conn.commit()
    conn.close()
    if not os.path.exists(FEES_PATH):
        ce.save_fees_config(FEES_PATH, ce.DEFAULT_FEES)


def load_settings():
    default = {'dapalza_url': '', 'ownerclan_url': ''}
    try:
        with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            default.update(data)
    except Exception:
        pass
    return default


init_db()


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def _read_build_version():
    try:
        with open(os.path.join(BASE_DIR, 'BUILD_VERSION.txt'), 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return '(버전 정보 없음)'


@app.route('/')
def index():
    return render_template('index.html', build_version=_read_build_version())


# ---------------------------------------------------------------------------
# Orders / Dashboard
# ---------------------------------------------------------------------------

def _filters_from_request(args):
    return dict(
        year=args.get('year', '전체'),
        month=args.get('month', '전체'),
        market=args.get('market', '전체'),
        search=args.get('search', ''),
        hl_only=args.get('hl_only') == '1',
        unpurchased_only=args.get('unpurchased_only') == '1',
        bundle_only=args.get('bundle_only') == '1',
        ready_only=args.get('ready_only') == '1',
        issue_only=args.get('issue_only') == '1',
    )


@app.route('/api/orders')
def api_orders():
    rows = ce.compute_dataset(DB_PATH, FEES_PATH)
    filters = _filters_from_request(request.args)
    filtered = ce.apply_filters(rows, **filters)
    included_summary = ce.summarize(filtered)
    return jsonify({
        'rows': filtered,
        'live_summary': {
            'sales': included_summary['sales'], 'fees': included_summary['fees'],
            'costs': included_summary['costs'], 'margin': included_summary['margin'],
            'rate': included_summary['rate'],
        },
        'total_count': len(filtered),
    })


@app.route('/api/orders/<order_id>/toggle', methods=['POST'])
def api_toggle_order(order_id):
    body = request.get_json(force=True) or {}
    field = body.get('field')
    value = body.get('value')
    if field not in ('margin_chk', 'ad_chk', 'order_chk'):
        return jsonify({'error': 'invalid field'}), 400
    if field == 'margin_chk' and value not in ('Y', 'N', 'AUTO'):
        return jsonify({'error': 'invalid value'}), 400
    if field in ('ad_chk', 'order_chk') and value not in ('Y', 'N'):
        return jsonify({'error': 'invalid value'}), 400

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(f"UPDATE merged_orders SET {field} = ? WHERE order_id = ?", (value, order_id))
    conn.commit()
    conn.close()

    rows = ce.compute_dataset(DB_PATH, FEES_PATH)
    row = next((r for r in rows if r['order_id'] == order_id), None)

    filters = _filters_from_request(request.args)
    filtered = ce.apply_filters(rows, **filters)
    live = ce.summarize(filtered)
    all_summary = ce.summarize(rows)

    return jsonify({
        'row': row,
        'live_summary': {
            'sales': live['sales'], 'fees': live['fees'], 'costs': live['costs'],
            'margin': live['margin'], 'rate': live['rate'],
        },
        'all_summary': all_summary,
    })


# ---------------------------------------------------------------------------
# Summary / Calendar (요약 리포트 탭)
# ---------------------------------------------------------------------------

@app.route('/api/summary/all')
def api_summary_all():
    rows = ce.compute_dataset(DB_PATH, FEES_PATH)
    return jsonify(ce.summarize(rows))


@app.route('/api/summary/filtered')
def api_summary_filtered():
    rows = ce.compute_dataset(DB_PATH, FEES_PATH)
    filters = dict(year=request.args.get('year', '전체'), month=request.args.get('month', '전체'),
                    market=request.args.get('market', '전체'))
    filtered = ce.apply_filters(rows, **filters)
    summary = ce.summarize(filtered)
    daily = ce.daily_aggregate(filtered)
    return jsonify({
        'sales': summary['sales'], 'fees': summary['fees'], 'costs': summary['costs'],
        'margin': summary['margin'], 'rate': summary['rate'], 'daily': daily,
    })


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

@app.route('/api/upload', methods=['POST'])
def api_upload():
    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': '파일이 없습니다.'}), 400
    results = []
    for f in files:
        try:
            # 디스크에 임시파일로 저장하지 않고 메모리에서 바로 읽는다 - 윈도우
            # 백신이 방금 생성된 임시파일을 순간적으로 잠가서 나던 PermissionError를
            # 아예 원천 차단한다.
            buf = io.BytesIO(f.read())
            res = parsers.process_upload(DB_PATH, buf, f.filename)
        except Exception as e:
            res = {'error': _friendly_error(e)}
        res['filename'] = f.filename
        results.append(res)
    return jsonify({'results': results})


@app.route('/api/dapalza/progress')
def api_dapalza_progress():
    # DPJ 버튼을 누르면 몇 분씩 걸릴 수 있는데, 지금까지는 요청이 끝나야만
    # 로그를 통째로 돌려주는 구조라 그동안 화면이 하나도 안 바뀌어서 "멈췄다"는
    # 오해를 계속 샀다(사용자 반복 확인) - 진행 중인 로그를 언제든 즉시 조회할
    # 수 있게 해서, 화면이 이걸 주기적으로 물어봐서 실시간으로 보여줄 수 있다.
    return jsonify({'log': dapalza_auto.get_progress()})


@app.route('/api/dapalza/collect', methods=['POST'])
def api_dapalza_collect():
    # 오너클랜 창이 로그인 등으로 화면에 떠서 다팔자 창을 가리고 있으면,
    # 가려진 Electron 창의 접근성 트리 갱신이 멈춰서 다팔자 자동화가 탭/버튼을
    # 하나도 못 찾는 사고로 이어질 수 있다 - 다팔자를 건드리기 전에 오너클랜
    # 창부터 항상 최소화해서 이 위험을 없앤다.
    try:
        ownerclan_auto.ensure_background()
    except Exception:
        pass
    result = dapalza_auto.collect_and_upload(
        save_folder=SETTLEMENT_UPLOAD_DIR,
        save_filename='다팔자.xlsx',
    )
    if not result.get('ok'):
        return jsonify(result)
    try:
        # 다팔자가 직접 디스크에 저장한 파일이라 메모리로 못 우회함 - 읽기만 재시도.
        buf = io.BytesIO(_read_with_retry(result['file_path']))
        upload_res = parsers.process_upload(DB_PATH, buf, os.path.basename(result['file_path']))
    except Exception as e:
        upload_res = {'error': _friendly_error(e)}
    result['upload'] = upload_res
    # 할인액/정산가/연락처/우편번호가 화면에서 계속 0/빈값으로 보이는 게
    # 우리 파싱 문제인지, 다팔자 자동수집이 받아온 이번 파일에 애초에 그
    # 컬럼이 없는 건지 바로 구분하려고, 파일 안에 그 컬럼이 실제로
    # 있었는지를 항상 로그로 남긴다(성공/실패와 무관하게).
    cols_found = upload_res.get('raw_columns_found')
    if cols_found is not None:
        missing = [k for k, v in cols_found.items() if not v]
        result.setdefault('log', [])
        if missing:
            result['log'].append(
                f"진단정보 - 이번에 자동수집한 파일에 다음 컬럼이 없습니다: {', '.join(missing)} "
                f"(할인액/실정산가/연락처/우편번호가 0 또는 빈값으로 보이는 원인일 수 있음)."
            )
        else:
            result['log'].append("진단정보 - 할인금액/정산가/수령인연락처/우편번호 컬럼 전부 이번 파일에서 확인됨.")
    # '전체 다운로드' 파일의 배송지엔 동/호 같은 상세주소가 자주 빠져있어서
    # (실제 데이터 비교로 확인됨, 사용자 지적) '오너클랜 다운로드' 파일을
    # 추가로 받아왔다면 그걸로 ship_address를 보강한다. 실패해도 '전체'
    # 업로드 결과 자체엔 영향 없다.
    oc_path = result.get('file_path_oc')
    if oc_path:
        try:
            oc_buf = io.BytesIO(_read_with_retry(oc_path))
            oc_res = parsers.merge_ownerclan_addresses(DB_PATH, oc_buf, os.path.basename(oc_path))
            result.setdefault('log', []).append(
                f"오너클랜 다운로드 파일로 상세주소 보강 완료: {oc_res.get('updated', 0)}건 갱신"
                f"(파일 {oc_res.get('rows', 0)}행 중)."
                if not oc_res.get('skipped_reason') else
                f"오너클랜 다운로드 파일 상세주소 보강 건너뜀: {oc_res.get('skipped_reason')}"
            )
        except Exception as e:
            result.setdefault('log', []).append(f"오너클랜 다운로드 파일로 상세주소 보강 실패(무시): {_friendly_error(e)}")
        try:
            os.remove(oc_path)
        except Exception:
            pass
    # 예전 프로그램(PyQt5)은 업로드가 끝나면 파일을 지우는 구조였는데, 이번
    # 자동화는 그걸 안 해서 정산_업로드_대기 폴더에 같은 이름(다팔자.xlsx)의
    # 파일이 계속 남아있었다 - 그게 다음 저장 시도 때 윈도우 저장창에 '이미
    # 있는 파일'로 걸려서 자동화가 엉뚱한 걸 건드리는 사고로 이어졌다. 반영이
    # 실제로 성공했을 때만(실패했으면 재시도/확인용으로 남겨둠) 지운다.
    if not upload_res.get('error'):
        try:
            os.remove(result['file_path'])
        except Exception:
            pass
        # 자동화가 끝나면 다팔자 창이 화면 맨 앞에 그대로 남아서, 성공했는지
        # 아닌지 바로 알기 어렵다는 피드백을 받았다 - 성공했을 때는 마진보드
        # 브라우저 창을 다시 앞으로 가져와서 눈으로 바로 확인할 수 있게 한다.
        try:
            dapalza_auto.bring_marginboard_to_front()
        except Exception:
            pass
        # 방금 수집으로 재고상태 칸이 비어있는 건이 새로 생겼으면, 프론트에서
        # 이어서 STOCK(재고확인) 자동화를 바로 이어 실행할 수 있게 알려준다 -
        # check_stock 자체가 대상으로 삼는 조건(취소/반품이 아니고 아직 칸이
        # 비어있음)과 똑같은 기준으로 판단해야 "확인할 게 없는데도 돌린다"는
        # 불일치가 안 생긴다.
        try:
            rows = ce.compute_dataset(DB_PATH, FEES_PATH)
            result['has_new_orders'] = any(
                r.get('vendor_prod_id') and not r.get('is_cancelled') and not r.get('stock_status')
                and r.get('sell_status') not in FINISHED_SELL_STATUSES
                and '[HL]' not in (r.get('buy_status') or '') for r in rows)
        except Exception:
            result['has_new_orders'] = False
    return jsonify(result)


@app.route('/api/ownerclan/setup_login', methods=['POST'])
def api_ownerclan_setup_login():
    settings = load_settings()
    result = ownerclan_auto.setup_login(settings.get('ownerclan_url', ''))
    return jsonify(result)


@app.route('/api/ownerclan/collect', methods=['POST'])
def api_ownerclan_collect():
    settings = load_settings()
    result = ownerclan_auto.collect_and_upload(
        settings.get('ownerclan_url', ''),
        save_folder=SETTLEMENT_UPLOAD_DIR,
        save_filename='oc.xlsx',
    )
    if not result.get('ok'):
        return jsonify(result)
    try:
        buf = io.BytesIO(_read_with_retry(result['file_path']))
        upload_res = parsers.process_upload(DB_PATH, buf, os.path.basename(result['file_path']))
    except Exception as e:
        upload_res = {'error': _friendly_error(e)}
    result['upload'] = upload_res
    if not upload_res.get('error') and 'blank_ledger_rows' in upload_res:
        # 합배송 매입상태 매칭 스펙(5.2)이 가정하는 '원장주문코드 빈 행' 구조가
        # 지금 실제로 내려오는지 확인용 - 0이면 그 구조 자체가 지금 안 내려온단
        # 뜻이라, 합배송 매입상태가 이상하게 보이는 문제의 단서가 될 수 있다.
        result['log'].append(
            f"진단정보 - 이번 발주내역 {upload_res.get('inserted', 0)}건 중 "
            f"원장주문코드가 빈 행(합배송 하위/합산행) {upload_res['blank_ledger_rows']}건."
        )
    if not upload_res.get('error'):
        try:
            os.remove(result['file_path'])
        except Exception:
            pass
    return jsonify(result)


@app.route('/api/ownerclan/progress')
def api_ownerclan_progress():
    # STOCK도 DPJ와 같은 이유로(요청이 끝나야만 로그를 통째로 돌려주는
    # 구조라 진행 중엔 화면이 안 바뀜) 실시간 진행상황 조회가 필요하다.
    return jsonify({'log': ownerclan_auto.get_progress()})


@app.route('/api/ownerclan/check_stock', methods=['POST'])
def api_ownerclan_check_stock():
    settings = load_settings()
    # '확인실패'를 저장 안 하게 막은 수정(위 INSERT 루프)은 이 실행 이후부터만
    # 효과가 있다 - 그전에 이미 stock_check 테이블에 저장돼버린 확인실패
    # 건들은 여전히 "재고상태 칸이 채워짐"으로 남아서 계속 재시도가 안 됐다
    # (사용자 지적: "아직도 확인실패잖아"). 매번 실행 시작할 때 이미 저장된
    # 확인실패 건들을 지워서, 그 즉시 다시 확인 대상에 포함되게 한다.
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM stock_check WHERE status='확인실패'")
    conn.commit()
    conn.close()
    rows = ce.compute_dataset(DB_PATH, FEES_PATH)
    # 예전엔 '신규주문' 상태인 건만 확인 대상으로 삼았는데, 그러면 신규주문일
    # 때 확인을 못 받고 배송준비/배송중으로 넘어간 건은 재고상태/매입예상가
    # 칸이 영원히 공란으로 남았다(사용자 지적). 이제는 상태와 무관하게
    # "재고상태 칸이 아직 비어있는" (판매사상품코드, 옵션) 조합이면 전부
    # 확인 대상에 넣는다 - 한 번 채워지면(stock_status가 채워짐) 다음부턴
    # 다시 확인하지 않는다. 다만 취소/반품 건은 재고를 확인할 이유가 없으니
    # 그대로 제외하고, 배송완료/구매확정처럼 이미 다 끝난 주문도 마찬가지로
    # 제외한다(사용자 지적: "배송완료 구매확정 반품 취소는 할필요가 없는데" -
    # is_cancelled는 취소/반품 계열만 걸러내고 이미 완료된 정상 배송 건은
    # 안 걸러내서, 이 조건을 처음 넣었을 때 전체 주문을 다 확인해버리는
    # 사고로 이어졌다). 예전처럼 상품코드만 보고 '옵션 중 아무거나 하나라도
    # 살아있으면 정상'으로 판정하면 실제 주문된 옵션이 품절이어도 정상으로
    # 잘못 나오는 문제가 있었으니, 여기서도 반드시 주문에 찍힌 옵션 그대로 매칭한다.
    # HL(수기매입처, CS메모 등으로 사람이 직접 매입 정보를 입력한 건)은 오너클랜
    # 상품이 아닐 수 있으니 재고확인 대상에서 제외한다(사용자 지적: "HL은 STOCK
    # 돌리지말") - has_new_orders 판단 기준과 반드시 동일해야 한다.
    items = sorted({(r['vendor_prod_id'], r.get('option_name') or '') for r in rows
                     if r.get('vendor_prod_id') and not r.get('is_cancelled') and not r.get('stock_status')
                     and r.get('sell_status') not in FINISHED_SELL_STATUSES
                     and '[HL]' not in (r.get('buy_status') or '')})
    result = ownerclan_auto.check_stock(settings.get('ownerclan_url', ''), items)
    if result.get('ok') and result.get('results'):
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        for entry in result['results']:
            # '확인실패'(페이지 이동 실패, 타임아웃 등 일시적 오류)까지 '정상'/
            # '품절'과 똑같이 저장해버리면, 다음부터는 "재고상태 칸이 비어있음"
            # 필터에 안 걸려서 영원히 재시도 대상에서 빠져버렸다(사용자 지적:
            # "확인실패 뜨는 건 왜 안 하냐"). 확인실패는 진짜 재고 판정이
            # 아니니 저장하지 않고 칸을 계속 비워둬서, 다음 STOCK 실행 때
            # 다시 확인 대상에 포함되게 한다.
            if entry.get('status') == '확인실패':
                continue
            price = entry.get('price')
            ship_fee = entry.get('ship_fee')
            cur.execute("""INSERT OR REPLACE INTO stock_check
                (vendor_prod_id, option_name, status, checked_at, est_buy_price, est_ship_fee)
                VALUES (?, ?, ?, ?, ?, ?)""", (entry['vendor_prod_id'], entry['option_name'], entry['status'], now,
                                                str(price) if price is not None else '',
                                                str(ship_fee) if ship_fee is not None else ''))
        conn.commit()
        conn.close()
    result['checked'] = len(result.get('results') or [])
    return jsonify(result)


DELIVERY_NOTE_KEYS = ['배송메시지', '배송시요청사항', '요청사항', '배송요청사항', '배송메모', '배송 메시지', '배송시요청', '요청 사항']


def _extract_delivery_note(raw_json_text):
    """주문자가 실제로 남긴 배송 요청사항(예: "부재시 문앞에 놔주세요")을
    찾는다. 다팔자/TOSS 표준 컬럼엔 이 값이 안 들어있어서(사용자 지적:
    "배송요청사항에 왜 상품명을 넣냐 배송요청을 넣어야지" - 상품명을 넣던
    건 실제 요청사항 데이터가 없어서 대신 채운 임시방편이었다), 업로드
    시점에 원본 행 전체를 그대로 저장해둔 raw_json에서 흔히 쓰이는
    컬럼명 후보들로 찾아본다. 실제 컬럼명이 다르면 못 찾을 수 있다."""
    if not raw_json_text:
        return ''
    try:
        data = json.loads(raw_json_text)
    except Exception:
        return ''
    for key in DELIVERY_NOTE_KEYS:
        v = data.get(key)
        if v is not None and str(v).strip() and str(v).strip().lower() not in ('nan', 'none', '-'):
            return str(v).strip()
    return ''


CS_MEMO_CODE_RE = re.compile(r'\bW[0-9A-Za-z]{4,}\b')


def _extract_cs_override_code(raw_json_text):
    """CS메모에 오너클랜 상품코드(W로 시작)가 적혀있으면 그 코드로 주문하게
    한다(사용자 요청: "CS메모에 W로 시작하는 상품코드가 있을경우 오더할때
    그 코드로 주문하게 해") - 담당자가 실제로 매칭되는 오너클랜 상품이
    표준 매칭과 다르다는 걸 CS메모에 수기로 남겨둔 경우를 위한 것으로
    보인다. raw_json(원본 행 전체)에서 'CS메모' 컬럼을 찾아 그 안에서
    W로 시작하는 코드를 정규식으로 찾는다."""
    if not raw_json_text:
        return ''
    try:
        data = json.loads(raw_json_text)
    except Exception:
        return ''
    memo = str(data.get('CS메모') or '').strip()
    if not memo:
        return ''
    m = CS_MEMO_CODE_RE.search(memo)
    return m.group(0) if m else ''


@app.route('/api/ownerclan/place_orders', methods=['POST'])
def api_ownerclan_place_orders():
    settings = load_settings()
    rows = ce.compute_dataset(DB_PATH, FEES_PATH)
    # 표에서 '발주' 체크박스를 켠 건만 처리한다(사용자 지시: "신규건 다 할
    # 필요없어 내가 지정한것만").
    checked = [r for r in rows if r.get('order_chk') == 'Y']
    order_ids = [r.get('order_id') for r in checked if r.get('order_id')]
    raw_json_map = {}
    if order_ids:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        qmarks = ','.join('?' * len(order_ids))
        cur.execute(f"SELECT order_id, raw_json FROM merged_orders WHERE order_id IN ({qmarks})", order_ids)
        raw_json_map = {row[0]: row[1] for row in cur.fetchall()}
        conn.close()
    # 같은 상품+같은 받는사람+같은 배송지로 여러 건이 체크됐으면(=묶음배송으로
    # 한 박스에 같이 갈 건들) 오너클랜에 따로따로 주문하지 않고 수량을 합쳐서
    # 한 번만 주문한다(사용자 지적: "묶음배송이면 한번에 주문해야지 따로따로
    # 하고 앉아있냐" - 배송비도 한 번만 나가고 카드결제도 한 번만 하면 됨).
    # 판매측 bundle_no(합배송코드)는 신뢰할 수 없어서 그룹 판정에 쓰지 않는다는
    # 원칙(calc_engine.py 주석 참고)이 있으므로, 여기서도 bundle_no는 안 쓰고
    # 실제 배송 단위를 그대로 결정하는 (상품코드, 옵션, 받는사람, 우편번호,
    # 배송지) 조합으로 직접 묶는다.
    groups = {}
    group_order = []
    for r in checked:
        raw = raw_json_map.get(r.get('order_id', ''))
        cs_override = _extract_cs_override_code(raw)
        vendor_prod_id = cs_override or r.get('vendor_prod_id', '')
        key = (vendor_prod_id, r.get('option_name', ''), r.get('recipient', ''),
               r.get('zipcode', ''), r.get('ship_address', ''))
        if key not in groups:
            groups[key] = {
                'vendor_prod_id': vendor_prod_id,
                'option_name': r.get('option_name', ''),
                'qty': 0,
                'order_id': [],
                'recipient': r.get('recipient', ''),
                'recipient_phone': r.get('recipient_phone', ''),
                'zipcode': r.get('zipcode', ''),
                'ship_address': r.get('ship_address', ''),
                'prod_name': r.get('prod_name', ''),
                'delivery_note': _extract_delivery_note(raw),
                'cs_override': cs_override,
            }
            group_order.append(key)
        g = groups[key]
        try:
            g['qty'] += max(1, int(float(r.get('qty') or 1)))
        except Exception:
            g['qty'] += 1
        g['order_id'].append(r.get('order_id', ''))
        if not g['delivery_note']:
            g['delivery_note'] = _extract_delivery_note(raw)

    items = []
    for key in group_order:
        g = groups[key]
        order_ids_in_group = [o for o in g['order_id'] if o]
        g['qty'] = str(g['qty'])
        g['order_id'] = ','.join(order_ids_in_group)
        items.append(g)

    result = ownerclan_auto.place_orders(settings.get('ownerclan_url', ''), items)
    # 시도한 건은 성공/실패와 무관하게 체크를 해제한다 - 재시도하려면 다시
    # 체크해야 하게 만들어서, 뭘 이미 시도했는지 헷갈리지 않게 한다.
    if checked:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        for r in checked:
            cur.execute("UPDATE merged_orders SET order_chk='N' WHERE order_id=?", (r['order_id'],))
        conn.commit()
        conn.close()
    result['attempted'] = len(items)
    return jsonify(result)


# ---------------------------------------------------------------------------
# 부가세 통합
# ---------------------------------------------------------------------------

VAT_MARKETS = ['쿠팡', '네이버', '11번가', '지마켓', '옥션', 'TOSS', '카카오', '기타']


@app.route('/api/vat/markets')
def api_vat_markets():
    return jsonify(VAT_MARKETS)


@app.route('/api/vat/upload', methods=['POST'])
def api_vat_upload():
    market = request.form.get('market', '').strip()
    if not market:
        return jsonify({'error': '마켓을 선택해주세요.'}), 400
    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': '파일이 없습니다.'}), 400
    results = []
    for f in files:
        try:
            buf = io.BytesIO(f.read())
            res = vat.process_vat_upload(DB_PATH, buf, f.filename, market)
        except Exception as e:
            res = {'error': _friendly_error(e)}
        res['filename'] = f.filename
        results.append(res)
    return jsonify({'results': results})


@app.route('/api/vat/half')
def api_vat_half():
    year = request.args.get('year', type=int)
    half = request.args.get('half', type=int)
    if not year or half not in (1, 2):
        return jsonify({'error': 'year, half(1 또는 2) 파라미터가 필요합니다.'}), 400
    return jsonify(vat.get_vat_half_detail(DB_PATH, year, half))


# ---------------------------------------------------------------------------
# HL 수기 매입처 매칭
# ---------------------------------------------------------------------------

@app.route('/api/hl/parse', methods=['POST'])
def api_hl_parse():
    body = request.get_json(force=True) or {}
    text = body.get('text', '')
    rows = parsers.parse_hl_text(text)
    return jsonify({'rows': rows})


@app.route('/api/hl/save', methods=['POST'])
def api_hl_save():
    body = request.get_json(force=True) or {}
    rows = body.get('rows', [])
    updated, errors = parsers.save_hl_matching(DB_PATH, rows)
    return jsonify({'updated': updated, 'errors': errors})


# ---------------------------------------------------------------------------
# Fees config
# ---------------------------------------------------------------------------

@app.route('/api/fees', methods=['GET'])
def api_fees_get():
    return jsonify(ce.load_fees_config(FEES_PATH))


@app.route('/api/fees', methods=['POST'])
def api_fees_save():
    body = request.get_json(force=True) or {}
    ce.save_fees_config(FEES_PATH, body)
    return jsonify({'ok': True})


@app.route('/api/fees/default')
def api_fees_default():
    return jsonify(ce.DEFAULT_FEES)


# ---------------------------------------------------------------------------
# Settings (빠른 이동 링크)
# ---------------------------------------------------------------------------

@app.route('/api/settings', methods=['GET'])
def api_settings_get():
    return jsonify(load_settings())


@app.route('/api/settings', methods=['POST'])
def api_settings_save():
    body = request.get_json(force=True) or {}
    settings = load_settings()
    settings.update({k: v for k, v in body.items() if k in ('dapalza_url', 'ownerclan_url')})
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    return jsonify(settings)


if __name__ == '__main__':
    app.run(debug=True, port=5000)
