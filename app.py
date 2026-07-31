# -*- coding: utf-8 -*-
"""이유상점 Margin Board - 웹 대시보드 (Flask)

실행: python app.py  →  http://127.0.0.1:5000
"""
import os
import json
import sqlite3
import tempfile

from flask import Flask, request, jsonify, render_template, send_from_directory

import calc_engine as ce
import parsers

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'shop_data.db')
FEES_PATH = os.path.join(BASE_DIR, 'fees_config.json')
SETTINGS_PATH = os.path.join(BASE_DIR, 'settings.json')

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS merged_orders (
        order_id TEXT PRIMARY KEY, source TEXT, market TEXT, sell_status TEXT, buy_status TEXT,
        order_date TEXT, prod_id TEXT, prod_name TEXT, qty TEXT, order_amt TEXT, ship_fee TEXT,
        add_ship_fee TEXT, fee_rate TEXT, market_fee TEXT, settle_amt TEXT, vendor_prod_id TEXT,
        buy_cost TEXT, buy_ship_fee TEXT, buy_total TEXT, final_margin TEXT, margin_rate TEXT,
        margin_chk TEXT DEFAULT 'AUTO', bundle_no TEXT DEFAULT '', ad_chk TEXT DEFAULT 'N')""")
    cur.execute("""CREATE TABLE IF NOT EXISTS purchase_ledger (
        order_id TEXT, vendor_prod_id TEXT, buy_cost TEXT, buy_ship_fee TEXT, buy_total TEXT,
        buy_status TEXT, buy_time TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS ownerclan_raw (
        원장주문코드 TEXT, 주문코드 TEXT, 주문일자 TEXT, 상품코드 TEXT, 상품명 TEXT, 배송상태 TEXT,
        주문수량 TEXT, 택배회사 TEXT, 송장번호 TEXT, 받는사람 TEXT, 보내는사람 TEXT, 총결제금액 TEXT,
        상품가격 TEXT, 배송비 TEXT, 택배송장메모 TEXT, 주문관리메모 TEXT)""")
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

@app.route('/')
def index():
    return render_template('index.html')


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
    with tempfile.TemporaryDirectory() as tmp:
        for f in files:
            fp = os.path.join(tmp, f.filename)
            f.save(fp)
            try:
                res = parsers.process_upload(DB_PATH, fp, f.filename)
            except Exception as e:
                res = {'error': str(e)}
            res['filename'] = f.filename
            results.append(res)
    return jsonify({'results': results})


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
