# -*- coding: utf-8 -*-
"""구매 다이어리 - 생필품 재구매 주기/가격 추적 (Flask)

기존 '이유상점 Margin Board'와는 완전히 분리된 별도 앱입니다.
쇼핑몰 로그인/스크래핑 없이, 사용자가 직접 구매 기록을 입력하는 방식으로 동작합니다.

실행: python app.py  →  http://127.0.0.1:5100
"""
import os
import sqlite3
from datetime import date, datetime, timedelta

from flask import Flask, request, jsonify, render_template

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'diary.db')

app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db():
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category TEXT DEFAULT '',
        memo TEXT DEFAULT '',
        created_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS purchases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        purchase_date TEXT NOT NULL,
        price INTEGER NOT NULL,
        quantity TEXT DEFAULT '',
        store TEXT DEFAULT '',
        memo TEXT DEFAULT '',
        created_at TEXT NOT NULL
    )""")
    conn.commit()
    conn.close()


init_db()


# ---------------------------------------------------------------------------
# Calculation helpers
# ---------------------------------------------------------------------------

def _parse_date(s):
    return datetime.strptime(s, '%Y-%m-%d').date()


def build_item_view(item_row, purchase_rows):
    purchases = sorted(purchase_rows, key=lambda r: r['purchase_date'])
    result = {
        'id': item_row['id'],
        'name': item_row['name'],
        'category': item_row['category'],
        'memo': item_row['memo'],
        'purchase_count': len(purchases),
        'purchases': [dict(p) for p in reversed(purchases)],
        'last_date': None,
        'prev_date': None,
        'last_price': None,
        'prev_price': None,
        'interval_days': None,
        'avg_interval_days': None,
        'price_diff': None,
        'price_diff_pct': None,
        'next_expected_date': None,
        'days_until_next': None,
    }
    if not purchases:
        return result

    last = purchases[-1]
    result['last_date'] = last['purchase_date']
    result['last_price'] = last['price']

    if len(purchases) >= 2:
        prev = purchases[-2]
        result['prev_date'] = prev['purchase_date']
        result['prev_price'] = prev['price']
        result['interval_days'] = (_parse_date(last['purchase_date']) - _parse_date(prev['purchase_date'])).days
        result['price_diff'] = last['price'] - prev['price']
        if prev['price']:
            result['price_diff_pct'] = round(result['price_diff'] / prev['price'] * 100, 1)

        intervals = []
        for a, b in zip(purchases, purchases[1:]):
            intervals.append((_parse_date(b['purchase_date']) - _parse_date(a['purchase_date'])).days)
        avg_interval = round(sum(intervals) / len(intervals))
        result['avg_interval_days'] = avg_interval

        next_expected = _parse_date(last['purchase_date']) + timedelta(days=avg_interval)
        result['next_expected_date'] = next_expected.isoformat()
        result['days_until_next'] = (next_expected - date.today()).days

    return result


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')


# ---------------------------------------------------------------------------
# Items API
# ---------------------------------------------------------------------------

@app.route('/api/items', methods=['GET'])
def api_items_list():
    conn = get_db()
    items = conn.execute('SELECT * FROM items ORDER BY id DESC').fetchall()
    views = []
    for item in items:
        purchases = conn.execute(
            'SELECT * FROM purchases WHERE item_id = ? ORDER BY purchase_date', (item['id'],)
        ).fetchall()
        views.append(build_item_view(item, purchases))
    conn.close()
    return jsonify({'items': views})


@app.route('/api/items', methods=['POST'])
def api_items_create():
    body = request.get_json(force=True) or {}
    name = (body.get('name') or '').strip()
    if not name:
        return jsonify({'error': '품목명을 입력해주세요.'}), 400
    conn = get_db()
    cur = conn.execute(
        'INSERT INTO items (name, category, memo, created_at) VALUES (?, ?, ?, ?)',
        (name, (body.get('category') or '').strip(), (body.get('memo') or '').strip(),
         datetime.now().isoformat())
    )
    conn.commit()
    item_id = cur.lastrowid
    conn.close()
    return jsonify({'id': item_id})


@app.route('/api/items/<int:item_id>', methods=['DELETE'])
def api_items_delete(item_id):
    conn = get_db()
    conn.execute('DELETE FROM items WHERE id = ?', (item_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# Purchases API
# ---------------------------------------------------------------------------

@app.route('/api/items/<int:item_id>/purchases', methods=['POST'])
def api_purchase_create(item_id):
    body = request.get_json(force=True) or {}
    purchase_date = (body.get('purchase_date') or '').strip()
    price = body.get('price')
    if not purchase_date:
        return jsonify({'error': '구매일을 입력해주세요.'}), 400
    try:
        _parse_date(purchase_date)
    except ValueError:
        return jsonify({'error': '구매일 형식이 올바르지 않습니다 (YYYY-MM-DD).'}), 400
    try:
        price = int(price)
    except (TypeError, ValueError):
        return jsonify({'error': '가격을 숫자로 입력해주세요.'}), 400

    conn = get_db()
    item = conn.execute('SELECT id FROM items WHERE id = ?', (item_id,)).fetchone()
    if not item:
        conn.close()
        return jsonify({'error': '존재하지 않는 품목입니다.'}), 404

    conn.execute(
        """INSERT INTO purchases (item_id, purchase_date, price, quantity, store, memo, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (item_id, purchase_date, price, (body.get('quantity') or '').strip(),
         (body.get('store') or '').strip(), (body.get('memo') or '').strip(),
         datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/purchases/<int:purchase_id>', methods=['DELETE'])
def api_purchase_delete(purchase_id):
    conn = get_db()
    conn.execute('DELETE FROM purchases WHERE id = ?', (purchase_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


if __name__ == '__main__':
    app.run(debug=True, port=5100)
