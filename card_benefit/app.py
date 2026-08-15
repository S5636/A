# -*- coding: utf-8 -*-
"""카드 남은혜택 체크 - PC/폰 공용 웹앱.

같은 와이파이(공유기)에 연결된 PC와 폰이 브라우저로 같은 서버(0.0.0.0)에
접속해서 카드별 혜택의 이번 달 남은 한도를 같이 보고 같이 기록한다.
"""
import os
import secrets
from datetime import date, datetime
from io import BytesIO

from flask import Flask, jsonify, render_template, request, send_file
from openpyxl import Workbook, load_workbook

from models import (
    cycle_bounds,
    extract_last4,
    find_statement_header,
    get_conn,
    get_setting,
    init_db,
    is_cancelled_status,
    parse_amount_cell,
    parse_date_cell,
    parse_notification,
    set_setting,
)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

app = Flask(__name__)


def serialize_inbox(conn):
    rows = conn.execute(
        "SELECT * FROM inbox_items WHERE status = 'pending' ORDER BY occurred_at DESC, id DESC"
    ).fetchall()
    items = []
    for r in rows:
        card = None
        if r["last4"]:
            card = conn.execute(
                "SELECT id, name FROM cards WHERE last4 = ? AND last4 != ''", (r["last4"],)
            ).fetchone()
        items.append({
            "id": r["id"],
            "raw_text": r["raw_text"],
            "amount": r["amount"],
            "last4": r["last4"],
            "issuer": r["issuer"],
            "merchant": r["merchant"],
            "occurred_at": r["occurred_at"],
            "matched_card_id": card["id"] if card else None,
            "matched_card_name": card["name"] if card else None,
        })
    return items


def serialize_full():
    conn = get_conn()
    try:
        return {"cards": _serialize_cards(conn), "inbox": serialize_inbox(conn)}
    finally:
        conn.close()


def _serialize_cards(conn):
    cards = conn.execute(
        "SELECT * FROM cards ORDER BY sort_order ASC, id ASC"
    ).fetchall()

    result = []
    for card in cards:
        start, end = cycle_bounds(card["reset_day"])
        benefits = conn.execute(
            "SELECT * FROM benefits WHERE card_id = ? ORDER BY sort_order ASC, id ASC",
            (card["id"],),
        ).fetchall()

        benefit_list = []
        for b in benefits:
            used = conn.execute(
                """SELECT COALESCE(SUM(used_value), 0) AS total FROM usage_logs
                   WHERE benefit_id = ? AND used_at >= ? AND used_at < ?""",
                (b["id"], start.isoformat(), end.isoformat()),
            ).fetchone()["total"]

            logs = conn.execute(
                """SELECT * FROM usage_logs WHERE benefit_id = ?
                   AND used_at >= ? AND used_at < ?
                   ORDER BY used_at DESC, id DESC""",
                (b["id"], start.isoformat(), end.isoformat()),
            ).fetchall()

            limit_value = b["limit_value"] or 0
            remaining = limit_value - used
            percent = 0
            if limit_value > 0:
                percent = max(0, min(100, round(used / limit_value * 100)))

            benefit_list.append({
                "id": b["id"],
                "card_id": b["card_id"],
                "name": b["name"],
                "limit_type": b["limit_type"],
                "limit_value": limit_value,
                "memo": b["memo"],
                "used": used,
                "remaining": remaining,
                "percent": percent,
                "over_limit": remaining < 0,
                "logs": [dict(l) for l in logs],
            })

        days_left = (end - date.today()).days
        result.append({
            "id": card["id"],
            "name": card["name"],
            "issuer": card["issuer"],
            "last4": card["last4"],
            "reset_day": card["reset_day"],
            "memo": card["memo"],
            "cycle_start": start.isoformat(),
            "cycle_end": end.isoformat(),
            "days_left": days_left,
            "benefits": benefit_list,
        })
    return result


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    return jsonify(serialize_full())


@app.route("/api/cards", methods=["POST"])
def create_card():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "카드 이름을 입력하세요."}), 400
    try:
        reset_day = int(data.get("reset_day") or 1)
    except (TypeError, ValueError):
        reset_day = 1

    conn = get_conn()
    try:
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) AS m FROM cards").fetchone()["m"]
        conn.execute(
            "INSERT INTO cards (name, issuer, last4, reset_day, memo, sort_order) VALUES (?, ?, ?, ?, ?, ?)",
            (
                name,
                (data.get("issuer") or "").strip(),
                (data.get("last4") or "").strip(),
                reset_day,
                (data.get("memo") or "").strip(),
                max_order + 1,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/cards/<int:card_id>", methods=["PUT"])
def update_card(card_id):
    data = request.get_json(force=True) or {}
    conn = get_conn()
    try:
        card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not card:
            return jsonify({"error": "카드를 찾을 수 없습니다."}), 404

        name = (data.get("name") or card["name"]).strip()
        try:
            reset_day = int(data.get("reset_day", card["reset_day"]))
        except (TypeError, ValueError):
            reset_day = card["reset_day"]

        conn.execute(
            "UPDATE cards SET name = ?, issuer = ?, last4 = ?, reset_day = ?, memo = ? WHERE id = ?",
            (
                name,
                (data.get("issuer", card["issuer"]) or "").strip(),
                (data.get("last4", card["last4"]) or "").strip(),
                reset_day,
                (data.get("memo", card["memo"]) or "").strip(),
                card_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/cards/<int:card_id>", methods=["DELETE"])
def delete_card(card_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/cards/<int:card_id>/benefits", methods=["POST"])
def create_benefit(card_id):
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "혜택 이름을 입력하세요."}), 400
    try:
        limit_value = float(data.get("limit_value") or 0)
    except (TypeError, ValueError):
        limit_value = 0
    limit_type = data.get("limit_type") if data.get("limit_type") in ("amount", "count") else "amount"

    conn = get_conn()
    try:
        card = conn.execute("SELECT id FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not card:
            return jsonify({"error": "카드를 찾을 수 없습니다."}), 404
        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS m FROM benefits WHERE card_id = ?", (card_id,)
        ).fetchone()["m"]
        conn.execute(
            """INSERT INTO benefits (card_id, name, limit_type, limit_value, memo, sort_order)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (card_id, name, limit_type, limit_value, (data.get("memo") or "").strip(), max_order + 1),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/benefits/<int:benefit_id>", methods=["PUT"])
def update_benefit(benefit_id):
    data = request.get_json(force=True) or {}
    conn = get_conn()
    try:
        b = conn.execute("SELECT * FROM benefits WHERE id = ?", (benefit_id,)).fetchone()
        if not b:
            return jsonify({"error": "혜택을 찾을 수 없습니다."}), 404

        name = (data.get("name") or b["name"]).strip()
        try:
            limit_value = float(data.get("limit_value", b["limit_value"]))
        except (TypeError, ValueError):
            limit_value = b["limit_value"]
        limit_type = data.get("limit_type") if data.get("limit_type") in ("amount", "count") else b["limit_type"]

        conn.execute(
            "UPDATE benefits SET name = ?, limit_type = ?, limit_value = ?, memo = ? WHERE id = ?",
            (name, limit_type, limit_value, (data.get("memo", b["memo"]) or "").strip(), benefit_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/benefits/<int:benefit_id>", methods=["DELETE"])
def delete_benefit(benefit_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM benefits WHERE id = ?", (benefit_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/benefits/<int:benefit_id>/use", methods=["POST"])
def log_usage(benefit_id):
    data = request.get_json(force=True) or {}
    conn = get_conn()
    try:
        b = conn.execute("SELECT * FROM benefits WHERE id = ?", (benefit_id,)).fetchone()
        if not b:
            return jsonify({"error": "혜택을 찾을 수 없습니다."}), 404

        try:
            used_value = float(data.get("used_value"))
        except (TypeError, ValueError):
            used_value = 1 if b["limit_type"] == "count" else 0
        if used_value == 0:
            return jsonify({"error": "사용 금액(또는 횟수)을 입력하세요."}), 400

        used_at = (data.get("used_at") or "").strip()
        try:
            datetime.strptime(used_at, "%Y-%m-%d")
        except ValueError:
            used_at = date.today().isoformat()

        conn.execute(
            "INSERT INTO usage_logs (benefit_id, used_value, used_at, memo) VALUES (?, ?, ?, ?)",
            (benefit_id, used_value, used_at, (data.get("memo") or "").strip()),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/logs/<int:log_id>", methods=["DELETE"])
def delete_log(log_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM usage_logs WHERE id = ?", (log_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


# ---- 폰 결제 알림 자동수집 (MacroDroid 등에서 웹훅으로 호출) ----


def _get_or_create_inbox_token(conn):
    token = get_setting(conn, "inbox_token")
    if not token:
        token = secrets.token_hex(4)
        set_setting(conn, "inbox_token", token)
        conn.commit()
    return token


def _inbox_authorized(req, expected_token):
    supplied = req.args.get("token") or req.headers.get("X-Inbox-Token")
    if not supplied:
        supplied = (req.get_json(silent=True) or {}).get("token")
    return supplied == expected_token


@app.route("/api/settings/inbox", methods=["GET"])
def get_inbox_settings():
    conn = get_conn()
    try:
        token = _get_or_create_inbox_token(conn)
    finally:
        conn.close()
    return jsonify({"token": token, "webhook_path": "/api/inbox"})


@app.route("/api/settings/inbox/regenerate", methods=["POST"])
def regenerate_inbox_token():
    conn = get_conn()
    try:
        token = secrets.token_hex(4)
        set_setting(conn, "inbox_token", token)
        conn.commit()
    finally:
        conn.close()
    return jsonify({"token": token, "webhook_path": "/api/inbox"})


@app.route("/api/inbox", methods=["POST"])
def create_inbox_item():
    data = request.get_json(force=True, silent=True) or {}

    conn = get_conn()
    try:
        expected_token = _get_or_create_inbox_token(conn)
        if not _inbox_authorized(request, expected_token):
            return jsonify({"error": "인증 토큰이 올바르지 않습니다."}), 401

        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"error": "text가 비어 있습니다."}), 400

        parsed = parse_notification(text)
        occurred_at = (data.get("occurred_at") or "").strip() or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn.execute(
            """INSERT INTO inbox_items (raw_text, amount, last4, issuer, merchant, occurred_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (text, parsed["amount"], parsed["last4"], parsed["issuer"], parsed["merchant"], occurred_at),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/inbox/<int:item_id>/assign", methods=["POST"])
def assign_inbox_item(item_id):
    data = request.get_json(force=True) or {}
    try:
        benefit_id = int(data.get("benefit_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "혜택을 선택하세요."}), 400

    conn = get_conn()
    try:
        item = conn.execute("SELECT * FROM inbox_items WHERE id = ?", (item_id,)).fetchone()
        if not item or item["status"] != "pending":
            return jsonify({"error": "이미 처리되었거나 존재하지 않는 알림입니다."}), 404

        benefit = conn.execute("SELECT * FROM benefits WHERE id = ?", (benefit_id,)).fetchone()
        if not benefit:
            return jsonify({"error": "혜택을 찾을 수 없습니다."}), 404

        try:
            used_value = float(data.get("amount") if data.get("amount") not in (None, "") else item["amount"])
        except (TypeError, ValueError):
            used_value = 0
        if not used_value:
            return jsonify({"error": "금액을 확인할 수 없습니다. 직접 입력해주세요."}), 400

        used_at = (data.get("used_at") or "").strip() or (item["occurred_at"] or "")[:10]
        try:
            datetime.strptime(used_at, "%Y-%m-%d")
        except ValueError:
            used_at = date.today().isoformat()

        memo = (data.get("memo") or item["merchant"] or "").strip()

        conn.execute(
            "INSERT INTO usage_logs (benefit_id, used_value, used_at, memo) VALUES (?, ?, ?, ?)",
            (benefit_id, used_value, used_at, memo),
        )
        conn.execute(
            "UPDATE inbox_items SET status = 'assigned', card_id = ?, benefit_id = ? WHERE id = ?",
            (benefit["card_id"], benefit_id, item_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/inbox/<int:item_id>", methods=["DELETE"])
def discard_inbox_item(item_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM inbox_items WHERE id = ?", (item_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


# ---- 지난/누락 내역 백업(엑셀 다운로드) · 일괄 업로드 ----


def _xlsx_response(wb, filename):
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=filename, mimetype=XLSX_MIME)


def _set_column_widths(ws, widths):
    for col, width in zip("ABCDEFGH", widths):
        ws.column_dimensions[col].width = width


@app.route("/api/export/usage.xlsx")
def export_usage():
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT c.name AS card_name, b.name AS benefit_name, l.used_at, l.used_value, l.memo
               FROM usage_logs l
               JOIN benefits b ON b.id = l.benefit_id
               JOIN cards c ON c.id = b.card_id
               ORDER BY l.used_at DESC, l.id DESC"""
        ).fetchall()
    finally:
        conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "사용내역"
    ws.append(["카드명", "혜택명", "날짜", "금액(또는 횟수)", "메모"])
    for r in rows:
        ws.append([r["card_name"], r["benefit_name"], r["used_at"], r["used_value"], r["memo"]])
    _set_column_widths(ws, [18, 22, 12, 16, 26])

    return _xlsx_response(wb, "카드혜택_사용내역.xlsx")


@app.route("/api/export/template.xlsx")
def export_template():
    conn = get_conn()
    try:
        combos = conn.execute(
            """SELECT c.name AS card_name, b.name AS benefit_name
               FROM benefits b JOIN cards c ON c.id = b.card_id
               ORDER BY c.sort_order, b.sort_order"""
        ).fetchall()
    finally:
        conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "업로드양식"
    ws.append(["카드명", "혜택명", "날짜(YYYY-MM-DD)", "금액(또는 횟수)", "메모(선택)"])
    for c in combos:
        ws.append([c["card_name"], c["benefit_name"], "", "", ""])
    _set_column_widths(ws, [18, 22, 18, 16, 26])

    return _xlsx_response(wb, "카드혜택_업로드양식.xlsx")


def _import_own_template(ws):
    """이 앱이 만든 "빈 양식"(카드명/혜택명 헤더)을 그대로 채워 올린 경우.

    카드명·혜택명이 정확히 일치하는 행은 바로 usage_logs로 들어간다(혜택이
    이미 정해져 있으므로 사람이 다시 고를 필요가 없음).
    """
    conn = get_conn()
    try:
        benefit_lookup = {}
        for row in conn.execute(
            """SELECT b.id AS benefit_id, c.name AS card_name, b.name AS benefit_name
               FROM benefits b JOIN cards c ON c.id = b.card_id"""
        ):
            benefit_lookup[(row["card_name"].strip(), row["benefit_name"].strip())] = row["benefit_id"]

        added = 0
        skipped = []
        for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if row is None or all(v in (None, "") for v in row):
                continue
            card_name, benefit_name, used_at, used_value = (list(row) + [None] * 4)[:4]
            memo = row[4] if len(row) > 4 else None

            key = ((str(card_name).strip() if card_name else ""), (str(benefit_name).strip() if benefit_name else ""))
            if key not in benefit_lookup:
                skipped.append(f"{i}행: 카드/혜택 이름을 찾을 수 없음 ({key[0]} / {key[1]})")
                continue

            used_value = parse_amount_cell(used_value)
            if not used_value:
                skipped.append(f"{i}행: 금액(또는 횟수)이 올바르지 않음")
                continue

            used_at_str = parse_date_cell(used_at)
            if not used_at_str:
                skipped.append(f"{i}행: 날짜가 올바르지 않음 (YYYY-MM-DD)")
                continue

            conn.execute(
                "INSERT INTO usage_logs (benefit_id, used_value, used_at, memo) VALUES (?, ?, ?, ?)",
                (benefit_lookup[key], used_value, used_at_str, (str(memo).strip() if memo else "")),
            )
            added += 1
        conn.commit()
    finally:
        conn.close()

    result = serialize_full()
    result["import_result"] = {"mode": "template", "added": added, "skipped": skipped}
    return jsonify(result)


def _import_statement(ws, header_info):
    """카드사에서 그대로 다운로드한 엑셀(이용일자/승인금액 등)을 인식해서
    "받은 결제 알림"(inbox_items) 목록에 대량으로 쌓는다.

    이 표에는 어떤 혜택인지에 대한 정보가 없으므로, 자동수집 알림과 똑같이
    사람이 앱에서 "혜택 선택"으로 하나씩 배정해야 한다.
    """
    date_col = header_info["date_col"]
    amount_col = header_info["amount_col"]
    merchant_col = header_info["merchant_col"]
    cardno_col = header_info["cardno_col"]

    conn = get_conn()
    try:
        queued = 0
        skipped = []
        for i, row in enumerate(
            ws.iter_rows(min_row=header_info["header_row"] + 1, values_only=True),
            start=header_info["header_row"] + 1,
        ):
            if row is None or all(v in (None, "") for v in row):
                continue

            status_col = header_info.get("status_col")
            if status_col is not None and status_col < len(row) and is_cancelled_status(row[status_col]):
                skipped.append(f"{i}행: 취소/실패 거래로 보여 건너뜀")
                continue

            amount = parse_amount_cell(row[amount_col] if amount_col < len(row) else None)
            if amount is None or amount <= 0:
                skipped.append(f"{i}행: 취소·환불로 보이는 금액이라 건너뜀" if amount is not None and amount < 0
                                else f"{i}행: 금액을 확인할 수 없음")
                continue

            used_at = parse_date_cell(row[date_col] if date_col < len(row) else None)
            if not used_at:
                skipped.append(f"{i}행: 날짜를 확인할 수 없음")
                continue

            merchant = ""
            if merchant_col is not None and merchant_col < len(row) and row[merchant_col] is not None:
                merchant = str(row[merchant_col]).strip()

            last4 = ""
            if cardno_col is not None and cardno_col < len(row):
                last4 = extract_last4(row[cardno_col])

            raw_parts = [p for p in [merchant, f"{amount:,.0f}원", used_at] if p]
            raw_text = " · ".join(raw_parts)

            conn.execute(
                """INSERT INTO inbox_items (raw_text, amount, last4, issuer, merchant, occurred_at)
                   VALUES (?, ?, ?, '', ?, ?)""",
                (raw_text, amount, last4, merchant, used_at),
            )
            queued += 1
        conn.commit()
    finally:
        conn.close()

    result = serialize_full()
    result["import_result"] = {"mode": "statement", "queued": queued, "skipped": skipped}
    return jsonify(result)


@app.route("/api/import/usage", methods=["POST"])
def import_usage():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "파일을 선택하세요."}), 400

    try:
        wb = load_workbook(file, data_only=True)
    except Exception:
        return jsonify({"error": "엑셀 파일을 읽을 수 없습니다. .xlsx 파일인지 확인해주세요."}), 400
    ws = wb.active

    first_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None) or []
    header_cells = [str(c).strip() if c is not None else "" for c in first_row]
    is_own_template = any("카드명" in c for c in header_cells) and any("혜택명" in c for c in header_cells)

    if is_own_template:
        return _import_own_template(ws)

    header_info = find_statement_header(ws)
    if not header_info:
        return jsonify({
            "error": "엑셀에서 날짜·금액 열을 찾지 못했습니다. 카드사에서 받은 파일 원본 그대로 올려주세요 "
                     "(헤더에 '이용일자'나 '승인금액' 같은 표현이 있어야 자동으로 인식됩니다)."
        }), 400

    return _import_statement(ws, header_info)


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)
