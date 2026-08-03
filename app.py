# -*- coding: utf-8 -*-
"""이유상점 Margin Board - 웹 대시보드 (Flask)

실행: python app.py  →  http://127.0.0.1:5000
"""
import io
import os
import json
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

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB
# 로컬 1인용 앱이라 static 파일(CSS/JS) 캐시를 꺼서, 업데이트 zip으로 갈아끼운 뒤
# 브라우저가 옛날 버전을 계속 보여주는 문제를 원천 차단한다.
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0


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
        option_name TEXT DEFAULT '')""")
    # 이미 만들어져 있던(예전 버전) merged_orders에는 option_name 컬럼이
    # 없으므로, CREATE TABLE IF NOT EXISTS로는 안 생긴다 - 있는지 확인해서
    # 없으면 추가한다 (재고상태를 상품코드 단위가 아니라 실제 주문된 옵션
    # 단위로 정확히 확인하려면 이 컬럼이 꼭 있어야 함).
    try:
        cur.execute("PRAGMA table_info(merged_orders)")
        if 'option_name' not in {row[1] for row in cur.fetchall()}:
            cur.execute("ALTER TABLE merged_orders ADD COLUMN option_name TEXT DEFAULT ''")
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
    try:
        cur.execute("""DELETE FROM ownerclan_raw WHERE rowid NOT IN (
            SELECT MAX(rowid) FROM ownerclan_raw GROUP BY 원장주문코드, 주문코드
        )""")
        cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_ownerclan_raw_key
            ON ownerclan_raw(원장주문코드, 주문코드)""")
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
        PRIMARY KEY (vendor_prod_id, option_name))""")
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
    if field not in ('margin_chk', 'ad_chk'):
        return jsonify({'error': 'invalid field'}), 400
    if field == 'margin_chk' and value not in ('Y', 'N', 'AUTO'):
        return jsonify({'error': 'invalid value'}), 400
    if field == 'ad_chk' and value not in ('Y', 'N'):
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


@app.route('/api/ownerclan/check_stock', methods=['POST'])
def api_ownerclan_check_stock():
    settings = load_settings()
    rows = ce.compute_dataset(DB_PATH, FEES_PATH)
    # '신규주문' 건들의 (판매사상품코드, 실제 주문된 옵션) 조합을 확인 대상으로
    # 삼는다 (사용자 요청: 이미 확정/배송된 건은 재고 확인이 필요 없음).
    # 예전엔 상품코드만 보고 '옵션 중 아무거나 하나라도 살아있으면 정상'으로
    # 판정해서, 정작 주문된 그 옵션이 품절이어도 정상으로 잘못 나오는 문제가
    # 있었다 - 반드시 주문에 찍힌 옵션 그대로 매칭해야 한다.
    items = sorted({(r['vendor_prod_id'], r.get('option_name') or '') for r in rows
                     if r.get('sell_status') == '신규주문' and r.get('vendor_prod_id')})
    result = ownerclan_auto.check_stock(settings.get('ownerclan_url', ''), items)
    if result.get('ok') and result.get('results'):
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        for entry in result['results']:
            cur.execute("""INSERT OR REPLACE INTO stock_check (vendor_prod_id, option_name, status, checked_at)
                VALUES (?, ?, ?, ?)""", (entry['vendor_prod_id'], entry['option_name'], entry['status'], now))
        conn.commit()
        conn.close()
    result['checked'] = len(result.get('results') or [])
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
