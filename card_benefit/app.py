# -*- coding: utf-8 -*-
"""카드 남은혜택 체크 - PC/폰 공용 웹앱.

같은 와이파이(공유기)에 연결된 PC와 폰이 브라우저로 같은 서버(0.0.0.0)에
접속해서 카드별 혜택의 이번 달 남은 한도를 같이 보고 같이 기록한다.
"""
import json
import os
import re
import secrets
from collections import defaultdict
from datetime import date, datetime, timedelta
from io import BytesIO

from flask import Flask, jsonify, make_response, render_template, request, send_file
from openpyxl import Workbook, load_workbook

from models import (
    annotate_merchant,
    card_codes_match,
    compute_change_earned,
    compute_percent_discount,
    current_year_month,
    cycle_bounds,
    extract_last4,
    find_statement_header,
    get_conn,
    get_setting,
    init_db,
    is_cancelled_status,
    is_medical_merchant,
    is_performance_excluded,
    match_benefit_keyword,
    match_rate_table,
    parse_rate_table_categories,
    rate_table_entry_by_label,
    parse_amount_cell,
    parse_date_cell,
    parse_datetime_cell,
    parse_notification,
    previous_year_month,
    set_setting,
    tier_limit_for_spend,
)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

app = Flask(__name__)


def serialize_inbox(conn):
    rows = conn.execute(
        "SELECT * FROM inbox_items WHERE status = 'pending' ORDER BY occurred_at DESC, id DESC"
    ).fetchall()
    cards_with_code = conn.execute("SELECT id, name, last4 FROM cards WHERE last4 != ''").fetchall()
    benefits_by_card = {}
    for b in conn.execute(
        "SELECT id, card_id, name, merchant_keywords, calc_mode, always_doubled FROM benefits"
    ):
        benefits_by_card.setdefault(b["card_id"], []).append(b)

    items = []
    for r in rows:
        card = None
        if r["last4"]:
            for c in cards_with_code:
                if card_codes_match(c["last4"], r["last4"]):
                    card = c
                    break

        matched_benefit = None
        matched_doubled = False

        if card and r["merchant"]:
            for b in benefits_by_card.get(card["id"], []):
                if match_benefit_keyword(r["merchant"], b["merchant_keywords"]):
                    matched_benefit = b
                    matched_doubled = b["calc_mode"] == "change_under_1000"
                    break

        if not matched_benefit and card:
            # 가맹점 키워드로 못 찾았어도, 이 카드에 "잔돈 자동계산"형 혜택이
            # 있으면(더모아처럼 거의 모든 결제가 적립 대상인 카드) 기본으로
            # 그 혜택을 제안한다. 키워드가 비어있는(catch-all) 혜택이 있으면
            # 그걸 우선 쓰고("특별적립 전용" 혜택을 따로 등록해둔 경우 대비),
            # 없으면(대부분의 경우 - 혜택이 하나뿐인 경우) 아무 change_under_1000
            # 혜택이나 후보로 삼는다 - 혜택을 하나만 등록한 사람도 자동매칭돼야 하므로.
            change_benefits = [
                b for b in benefits_by_card.get(card["id"], []) if b["calc_mode"] == "change_under_1000"
            ]
            catch_all = [b for b in change_benefits if not b["merchant_keywords"]]
            fallback_pool = catch_all or change_benefits
            if fallback_pool:
                matched_benefit = fallback_pool[0]
                # merchant_keywords가 있는 혜택은 "매칭되면 2배, 안 되면 1배"를
                # 구분하려고 키워드를 등록해둔 것이므로, 여기(키워드 매칭 실패)로
                # 온 이상 always_doubled를 적용하면 안 된다. always_doubled는
                # 키워드가 아예 없는(=구분할 필요 없이 통째로 2배인) 혜택에만 쓴다.
                matched_doubled = bool(matched_benefit["always_doubled"]) and not matched_benefit["merchant_keywords"]

        # 카드사 엑셀의 "이용구분"에 해외/할부라고 명시돼 있으면, 가맹점명으로
        # 유추할 필요 없이 그 자체로 확실한 신호이므로 더모아형(잔돈 적립) 혜택은
        # 무조건 특별적립(2배) 대상으로 본다(안내장 기준: 해외이용, 할부거래는
        # 항상 2배).
        if matched_benefit and matched_benefit["calc_mode"] == "change_under_1000":
            usage_type = r["usage_type"] or ""
            if "해외" in usage_type or "할부" in usage_type:
                matched_doubled = True

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
            "matched_benefit_id": matched_benefit["id"] if matched_benefit else None,
            "matched_benefit_name": matched_benefit["name"] if matched_benefit else None,
            "matched_benefit_doubled": matched_doubled,
        })
    return items


def serialize_no_benefit(conn):
    rows = conn.execute(
        """SELECT inbox_items.*, cards.name AS card_name
           FROM inbox_items LEFT JOIN cards ON cards.id = inbox_items.card_id
           WHERE inbox_items.status = 'no_benefit'
           ORDER BY inbox_items.occurred_at DESC, inbox_items.id DESC"""
    ).fetchall()
    return [
        {
            "id": r["id"],
            "raw_text": r["raw_text"],
            "amount": r["amount"],
            "merchant": r["merchant"],
            "occurred_at": r["occurred_at"],
            "card_name": r["card_name"],
        }
        for r in rows
    ]


def serialize_full():
    conn = get_conn()
    try:
        return {
            "cards": _serialize_cards(conn),
            "inbox": serialize_inbox(conn),
            "no_benefit": serialize_no_benefit(conn),
            "loans": serialize_loans(conn),
            "recurring_payments": serialize_recurring_payments(conn),
            "insurances": serialize_insurances(conn),
            "medical_expenses": serialize_medical_expenses(conn),
        }
    finally:
        conn.close()


def serialize_recurring_payments(conn):
    rows = conn.execute(
        "SELECT * FROM recurring_payments ORDER BY payment_day ASC, sort_order ASC, id ASC"
    ).fetchall()
    return _attach_match_info(conn, rows, "recurring_id")


def serialize_insurances(conn):
    rows = conn.execute(
        "SELECT * FROM insurances ORDER BY payment_day ASC, sort_order ASC, id ASC"
    ).fetchall()
    return _attach_match_info(conn, rows, "insurance_id")


def serialize_medical_expenses(conn):
    """받은 알림 중 가맹점명으로 봤을 때 병원/약국 등 의료기관 결제로 보이는 것들을
    모아서, 실손 등으로 청구했는지(medical_claimed) 체크할 수 있게 보여준다.
    "이건 아님" 처리(medical_excluded)한 건 다시 안 보여준다."""
    rows = conn.execute(
        "SELECT * FROM inbox_items WHERE merchant != '' AND medical_excluded = 0 "
        "ORDER BY occurred_at DESC, id DESC"
    ).fetchall()
    return [
        {
            "id": r["id"],
            "amount": r["amount"],
            "merchant": r["merchant"],
            "occurred_at": r["occurred_at"],
            "claimed": bool(r["medical_claimed"]),
        }
        for r in rows
        if is_medical_merchant(r["merchant"])
    ]


def _parse_card_id(value):
    """결제수단으로 등록된 카드를 고른 경우 그 카드 id, 아니면 None(현금/계좌이체 등
    자유입력이거나 선택 안 함)으로 정리한다."""
    if value in (None, "", "null"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_payment_method(conn, card_id, fallback_text):
    """card_id가 있으면(등록된 카드를 결제수단으로 고른 경우) 카드탭의 현재
    이름+뒷자리를 그 자리에서 조합해서 돌려준다 - 카드탭에서 이름을 바꾸면
    고정지출/보험 쪽도 다음 조회 때 바로 반영되고, 따로 동기화할 필요가 없다.
    card_id가 없으면(현금/계좌이체 등) 저장된 텍스트를 그대로 쓴다."""
    if card_id:
        card = conn.execute("SELECT name, last4 FROM cards WHERE id = ?", (card_id,)).fetchone()
        if card:
            return f"{card['name']}({card['last4']})" if card["last4"] else card["name"]
    return fallback_text or ""


def _attach_match_info(conn, rows, fk_column):
    """고정지출/보험 공용: 이번 달 받은 알림 중 이 항목과 매칭된 게 있는지 붙여주고,
    결제수단이 등록된 카드(card_id)면 카드탭 기준으로 실시간 표기를 만들어 넣는다.
    과거 달 매칭 내역도 match_history로 전부 붙여서(몇 달치를 한 번에 엑셀로
    올려도) "내역" 버튼으로 지난 달 것까지 다 보이게 한다."""
    current_ym = current_year_month()
    result = []
    for r in rows:
        item = dict(r)
        item["payment_method"] = _resolve_payment_method(conn, r["card_id"], r["payment_method"])
        history = conn.execute(
            f"""SELECT id, amount, merchant, occurred_at FROM inbox_items
               WHERE {fk_column} = ? ORDER BY occurred_at DESC""",
            (r["id"],),
        ).fetchall()
        item["match_history"] = [
            {"id": h["id"], "amount": h["amount"], "merchant": h["merchant"], "occurred_at": h["occurred_at"]}
            for h in history
        ]
        match = next((h for h in history if h["occurred_at"][:7] == current_ym), None)
        item["matched_this_month"] = bool(match)
        item["matched_amount"] = match["amount"] if match else None
        item["matched_merchant"] = match["merchant"] if match else None
        item["matched_occurred_at"] = match["occurred_at"] if match else None
        result.append(item)
    return result


def _extract_method_last4(payment_method):
    """고정지출/보험의 결제수단 문자열 끝에 '(2087)'처럼 붙은 카드 뒷자리를 뽑는다.
    현금/계좌이체처럼 괄호가 없으면 빈 문자열(= 카드 뒷자리로는 거를 수 없음)."""
    m = re.search(r"\((\d{3,4})\)\s*$", payment_method or "")
    return m.group(1) if m else ""


_NAME_TOKEN_STOPWORDS = {"hl", "주식회사", "(주)"}


def _name_tokens(name):
    """고정지출/보험 이름에서 가맹점명 매칭에 쓸만한 의미있는 단어만 뽑는다
    (너무 짧거나 흔한 접두어는 제외 - 도시가스/통신사처럼 공급사 이름이
    앞에 붙는 경우가 많아서, 'HL 삼천리 도시가스'에서 'HL'은 걸러낸다)."""
    tokens = re.split(r"[\s/()·\-]+", name or "")
    return [t for t in tokens if len(t) >= 2 and t.lower() not in _NAME_TOKEN_STOPWORDS]


def _try_auto_match(conn, inbox_id, fk_column, table, amount_column, name_column):
    """받은 알림 하나를 활성 항목(고정지출 또는 보험)에 자동으로 묶는다.

    1차: 금액이 정확히 같고(결제수단에 카드 뒷자리가 있으면 그것까지 일치하는)
    항목이 '정확히 하나'일 때 매칭 - 구독료처럼 매달 같은 금액인 경우.
    2차(1차에서 금액이 맞는 후보가 하나도 없을 때만): 가맹점명에 항목 이름의
    의미있는 단어가 포함되는 항목이 '정확히 하나'일 때 매칭 - 도시가스/전기세처럼
    매달 청구 금액이 바뀌는 고정지출은 금액으로는 절대 못 잡으므로.
    두 경우 다 후보가 여러 개면 애매한 거라 손대지 않고 사용자가 직접 고르게 둔다.

    주의: 여기선 inbox_items.status를 절대 바꾸지 않는다(recurring_id/insurance_id만
    추가로 달아둠). status를 건드리면 "받은 알림" 혜택배정 큐에서 사라지고, 전월실적
    자동집계(status가 assigned/no_benefit인 것만 합산)에서도 빠지는 문제가
    생기기 때문 - 카드로 결제한 고정지출/보험료도 전월실적엔 그대로 잡혀야 한다."""
    item = conn.execute(
        "SELECT * FROM inbox_items WHERE id = ? AND recurring_id IS NULL AND insurance_id IS NULL",
        (inbox_id,),
    ).fetchone()
    if not item or not item["amount"]:
        return False

    active_rows = conn.execute(f"SELECT * FROM {table} WHERE active = 1").fetchall()

    def last4_ok(rp):
        method_last4 = _extract_method_last4(rp["payment_method"])
        return not (method_last4 and item["last4"] and not card_codes_match(method_last4, item["last4"]))

    amount_matches = [rp for rp in active_rows if rp[amount_column] == item["amount"] and last4_ok(rp)]
    if len(amount_matches) == 1:
        conn.execute(f"UPDATE inbox_items SET {fk_column} = ? WHERE id = ?", (amount_matches[0]["id"], inbox_id))
        return True
    if amount_matches:
        return False

    if not item["merchant"]:
        return False
    merchant_lower = item["merchant"].lower()
    name_matches = [
        rp for rp in active_rows
        if last4_ok(rp) and any(tok.lower() in merchant_lower for tok in _name_tokens(rp[name_column]))
    ]
    if len(name_matches) != 1:
        return False

    conn.execute(f"UPDATE inbox_items SET {fk_column} = ? WHERE id = ?", (name_matches[0]["id"], inbox_id))
    return True


def _try_auto_match_recurring(conn, inbox_id):
    return _try_auto_match(conn, inbox_id, "recurring_id", "recurring_payments", "amount", "name")


def _try_auto_match_insurance(conn, inbox_id):
    return _try_auto_match(conn, inbox_id, "insurance_id", "insurances", "monthly_premium", "product_name")


def serialize_loans(conn):
    rows = conn.execute("SELECT * FROM loans ORDER BY sort_order ASC, id ASC").fetchall()
    result = []
    for loan in rows:
        payments = conn.execute(
            "SELECT * FROM loan_payments WHERE loan_id = ? ORDER BY paid_at DESC, id DESC",
            (loan["id"],),
        ).fetchall()

        principal = loan["principal"] or 0
        balance = loan["current_balance"] or 0
        percent_paid = None
        if principal > 0:
            percent_paid = max(0, min(100, round((principal - balance) / principal * 100)))

        days_left = None
        if loan["end_date"]:
            try:
                days_left = (date.fromisoformat(loan["end_date"]) - date.today()).days
            except ValueError:
                days_left = None

        # 매달이 아니라 특정 달에만 내는 대출(예: 분기납 1,4,7,10월)은
        # payment_months에 그 달들이 들어있다. 비어있으면 매달 내는 걸로 본다.
        payment_months = [m.strip() for m in (loan["payment_months"] or "").split(",") if m.strip()]
        due_this_month = (not payment_months) or (str(date.today().month) in payment_months)
        current_ym = date.today().strftime("%Y-%m")
        paid_this_month = loan["paid_check_month"] == current_ym

        result.append({
            "id": loan["id"],
            "name": loan["name"],
            "loan_type": loan["loan_type"],
            "principal": principal,
            "current_balance": balance,
            "interest_rate": loan["interest_rate"],
            "repayment_type": loan["repayment_type"],
            "start_date": loan["start_date"],
            "end_date": loan["end_date"],
            "payment_day": loan["payment_day"],
            "monthly_payment": loan["monthly_payment"],
            "payment_months": loan["payment_months"],
            "due_this_month": due_this_month,
            "withdrawal_account": loan["withdrawal_account"],
            "paid_this_month": paid_this_month,
            "memo": loan["memo"],
            "percent_paid": percent_paid,
            "days_left": days_left,
            "sort_order": loan["sort_order"],
            "payments": [dict(p) for p in payments],
        })
    return result


def _serialize_cards(conn):
    cards = conn.execute(
        "SELECT * FROM cards ORDER BY sort_order ASC, id ASC"
    ).fetchall()

    result = []
    for card in cards:
        start, end = cycle_bounds(card["reset_day"])

        this_month = current_year_month()
        prev_month = previous_year_month()
        perf_threshold = card["perf_threshold"] or 0

        # 실적은 손으로 입력할 필요 없이, 그동안 받은 결제 알림(배정됐거나
        # "혜택 없음" 처리된 것 - 즉 실제로 확인된 결제) 금액을 합산해서 자동
        # 계산한다. 카드사 실적은 "지난달(전월)" 완결된 금액을 기준으로 이번 달
        # 혜택 구간이 정해지므로, 지난달 알림 합계를 그 기준으로 쓴다.
        # 안내장의 "전월 이용금액 제외 대상"(공과금·세금·상품권 등)은 혜택
        # 대상이어도 실적 합계에서는 뺀다.
        def _auto_spend(year_month):
            rows = conn.execute(
                """SELECT amount, merchant FROM inbox_items
                   WHERE card_id = ? AND status IN ('assigned', 'no_benefit')
                   AND substr(occurred_at, 1, 7) = ?""",
                (card["id"], year_month),
            ).fetchall()
            return sum(
                r["amount"] for r in rows
                if not is_performance_excluded(r["merchant"], card["perf_excluded_keywords"])
            )

        auto_prev_spend = _auto_spend(prev_month)
        auto_this_month_spend = _auto_spend(this_month)

        manual_row = conn.execute(
            "SELECT total_spend FROM performance WHERE card_id = ? AND year_month = ?",
            (card["id"], prev_month),
        ).fetchone()
        perf_spend = manual_row["total_spend"] if manual_row else auto_prev_spend

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
                """SELECT usage_logs.*, inbox_items.occurred_at AS notif_occurred_at
                   FROM usage_logs
                   LEFT JOIN inbox_items ON inbox_items.id = usage_logs.source_inbox_id
                   WHERE usage_logs.benefit_id = ?
                   AND usage_logs.used_at >= ? AND usage_logs.used_at < ?
                   ORDER BY usage_logs.used_at DESC, usage_logs.id DESC""",
                (b["id"], start.isoformat(), end.isoformat()),
            ).fetchall()

            tiered_limit = tier_limit_for_spend(b["tier_table"], perf_spend)
            # 구간표가 있는 혜택은 전월실적이 최저 구간에도 못 미치면 tiered_limit이
            # 0으로 나온다 - 이건 "한도 없음(무제한)"이 아니라 "이번 달 한도 0원
            # (혜택 대상 아님)"이라는 뜻이므로, 구간표가 아예 없는 혜택(limit_value가
            # 0=무제한이라는 뜻)과 구분해서 처리해야 한다. 안 그러면 실적 미달인데도
            # 화면에 "무제한 적립 가능"으로 잘못 보인다.
            if tiered_limit is not None:
                limit_value = tiered_limit
                unlimited = False
            else:
                limit_value = b["limit_value"] or 0
                unlimited = limit_value <= 0
            remaining = None if unlimited else limit_value - used
            if unlimited:
                percent = None
            elif limit_value > 0:
                percent = max(0, min(100, round(used / limit_value * 100)))
            else:
                percent = 100 if used > 0 else 0

            category_usage = None
            if b["calc_mode"] == "percent_discount" and b["rate_table"]:
                categories = parse_rate_table_categories(b["rate_table"])
                if categories:
                    today_str = date.today().isoformat()
                    this_month_str = today_str[:7]
                    counts = {}
                    today_counts = {}
                    month_earned = {}
                    for l in logs:
                        cat = l["rate_category"]
                        if cat:
                            counts[cat] = counts.get(cat, 0) + 1
                            if l["used_at"] == today_str:
                                today_counts[cat] = today_counts.get(cat, 0) + 1
                            if l["used_at"][:7] == this_month_str:
                                month_earned[cat] = month_earned.get(cat, 0) + (l["used_value"] or 0)
                    category_usage = [
                        {
                            "label": c["label"],
                            "count": counts.get(c["label"], 0),
                            "today_count": today_counts.get(c["label"], 0),
                            "daily_limit": c["daily_limit"],
                            "monthly_limit": c["monthly_limit"],
                            "monthly_point_cap": c["monthly_point_cap"],
                            "month_earned": month_earned.get(c["label"], 0),
                        }
                        for c in categories
                    ]

            benefit_list.append({
                "id": b["id"],
                "card_id": b["card_id"],
                "name": b["name"],
                "limit_type": b["limit_type"],
                "limit_value": limit_value,
                "unlimited": unlimited,
                "tiered": tiered_limit is not None,
                "tier_table": b["tier_table"],
                "memo": b["memo"],
                "merchant_keywords": b["merchant_keywords"],
                "calc_mode": b["calc_mode"],
                "discount_percent": b["discount_percent"],
                "per_txn_cap": b["per_txn_cap"],
                "rate_table": b["rate_table"],
                "category_usage": category_usage,
                "always_doubled": bool(b["always_doubled"]),
                "used": used,
                "remaining": remaining,
                "percent": percent,
                "over_limit": (not unlimited) and remaining < 0,
                "logs": [dict(l) for l in logs],
            })

        days_left = (end - date.today()).days

        result.append({
            "id": card["id"],
            "name": card["name"],
            "short_name": card["short_name"],
            "issuer": card["issuer"],
            "last4": card["last4"],
            "reset_day": card["reset_day"],
            "perf_threshold": perf_threshold,
            "perf_spend": perf_spend,
            "perf_month": prev_month,
            "perf_auto": manual_row is None,
            "perf_met": (auto_this_month_spend >= perf_threshold) if perf_threshold > 0 else None,
            "this_month_spend": auto_this_month_spend,
            "memo": card["memo"],
            "cycle_start": start.isoformat(),
            "cycle_end": (end - timedelta(days=1)).isoformat(),
            "days_left": days_left,
            "benefits": benefit_list,
            "sort_order": card["sort_order"],
        })
    return result


@app.route("/")
def index():
    # 업데이트할 때마다 폰 브라우저가 예전에 캐시해둔 js/css를 계속
    # 쓰는 바람에 화면이 안 바뀐 것처럼 보이는 문제가 있어서, 파일이
    # 바뀔 때마다 값이 달라지는 버전 문자열을 붙여 강제로 새로 받게 한다.
    js_path = os.path.join(app.static_folder, "js", "app.js")
    css_path = os.path.join(app.static_folder, "css", "style.css")
    asset_version = str(int(max(os.path.getmtime(js_path), os.path.getmtime(css_path))))
    resp = make_response(render_template("index.html", asset_version=asset_version))
    # 이 HTML 자체를 폰 브라우저가 캐시해버리면 새 버전 번호가 붙은
    # 링크조차 못 받아보게 되므로, 페이지 본문은 절대 캐시하지 않게 한다.
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


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
    try:
        perf_threshold = float(data.get("perf_threshold") or 0)
    except (TypeError, ValueError):
        perf_threshold = 0

    conn = get_conn()
    try:
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) AS m FROM cards").fetchone()["m"]
        conn.execute(
            """INSERT INTO cards (name, short_name, issuer, last4, reset_day, perf_threshold, memo, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                (data.get("short_name") or "").strip(),
                (data.get("issuer") or "").strip(),
                (data.get("last4") or "").strip(),
                reset_day,
                perf_threshold,
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
        try:
            perf_threshold = float(data.get("perf_threshold", card["perf_threshold"]))
        except (TypeError, ValueError):
            perf_threshold = card["perf_threshold"]
        try:
            sort_order = int(data.get("sort_order", card["sort_order"]))
        except (TypeError, ValueError):
            sort_order = card["sort_order"]

        conn.execute(
            """UPDATE cards SET name = ?, short_name = ?, issuer = ?, last4 = ?, reset_day = ?,
               perf_threshold = ?, memo = ?, sort_order = ? WHERE id = ?""",
            (
                name,
                (data.get("short_name", card["short_name"]) or "").strip(),
                (data.get("issuer", card["issuer"]) or "").strip(),
                (data.get("last4", card["last4"]) or "").strip(),
                reset_day,
                perf_threshold,
                (data.get("memo", card["memo"]) or "").strip(),
                sort_order,
                card_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/cards/<int:card_id>/performance", methods=["POST"])
def update_performance(card_id):
    """전월실적 계산용 "이번 달 총 사용액"을 직접 입력/수정한다.

    혜택별 사용기록과 달리, 전월실적은 보통 그 카드로 쓴 전체 금액을 보므로
    (스타벅스 할인 같은 특정 혜택에 해당 안 하는 결제도 포함) 카드사 앱/문자로
    확인한 "이번 달 실적" 숫자를 그대로 입력하는 방식으로 관리한다.
    """
    data = request.get_json(force=True) or {}
    try:
        total_spend = float(data.get("total_spend"))
    except (TypeError, ValueError):
        return jsonify({"error": "사용액을 입력하세요."}), 400
    if total_spend < 0:
        return jsonify({"error": "사용액은 0 이상이어야 합니다."}), 400

    year_month = (data.get("year_month") or "").strip() or previous_year_month()

    conn = get_conn()
    try:
        card = conn.execute("SELECT id FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not card:
            return jsonify({"error": "카드를 찾을 수 없습니다."}), 404
        conn.execute(
            """INSERT INTO performance (card_id, year_month, total_spend, updated_at)
               VALUES (?, ?, ?, datetime('now', 'localtime'))
               ON CONFLICT(card_id, year_month)
               DO UPDATE SET total_spend = excluded.total_spend, updated_at = excluded.updated_at""",
            (card_id, year_month, total_spend),
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


# ---- 대출 ----


@app.route("/api/loans", methods=["POST"])
def create_loan():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "대출 이름을 입력하세요."}), 400
    try:
        principal = float(data.get("principal") or 0)
    except (TypeError, ValueError):
        principal = 0
    try:
        current_balance = float(
            data.get("current_balance") if data.get("current_balance") not in (None, "") else principal
        )
    except (TypeError, ValueError):
        current_balance = principal
    try:
        interest_rate = float(data.get("interest_rate") or 0)
    except (TypeError, ValueError):
        interest_rate = 0
    try:
        payment_day = int(data.get("payment_day") or 0)
    except (TypeError, ValueError):
        payment_day = 0
    try:
        monthly_payment = float(data.get("monthly_payment") or 0)
    except (TypeError, ValueError):
        monthly_payment = 0

    conn = get_conn()
    try:
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) AS m FROM loans").fetchone()["m"]
        conn.execute(
            """INSERT INTO loans (name, loan_type, principal, current_balance, interest_rate,
               repayment_type, start_date, end_date, payment_day, monthly_payment, payment_months,
               withdrawal_account, memo, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                (data.get("loan_type") or "").strip(),
                principal,
                current_balance,
                interest_rate,
                (data.get("repayment_type") or "").strip(),
                (data.get("start_date") or "").strip(),
                (data.get("end_date") or "").strip(),
                payment_day,
                monthly_payment,
                (data.get("payment_months") or "").strip(),
                (data.get("withdrawal_account") or "").strip(),
                (data.get("memo") or "").strip(),
                max_order + 1,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/loans/<int:loan_id>", methods=["PUT"])
def update_loan(loan_id):
    data = request.get_json(force=True) or {}
    conn = get_conn()
    try:
        loan = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
        if not loan:
            return jsonify({"error": "대출을 찾을 수 없습니다."}), 404

        name = (data.get("name") or loan["name"]).strip()
        try:
            principal = float(data.get("principal", loan["principal"]))
        except (TypeError, ValueError):
            principal = loan["principal"]
        try:
            current_balance = float(data.get("current_balance", loan["current_balance"]))
        except (TypeError, ValueError):
            current_balance = loan["current_balance"]
        try:
            interest_rate = float(data.get("interest_rate", loan["interest_rate"]))
        except (TypeError, ValueError):
            interest_rate = loan["interest_rate"]
        try:
            payment_day = int(data.get("payment_day", loan["payment_day"]))
        except (TypeError, ValueError):
            payment_day = loan["payment_day"]
        try:
            monthly_payment = float(data.get("monthly_payment", loan["monthly_payment"]))
        except (TypeError, ValueError):
            monthly_payment = loan["monthly_payment"]
        try:
            sort_order = int(data.get("sort_order", loan["sort_order"]))
        except (TypeError, ValueError):
            sort_order = loan["sort_order"]

        conn.execute(
            """UPDATE loans SET name=?, loan_type=?, principal=?, current_balance=?, interest_rate=?,
               repayment_type=?, start_date=?, end_date=?, payment_day=?, monthly_payment=?,
               payment_months=?, withdrawal_account=?, memo=?, sort_order=? WHERE id=?""",
            (
                name,
                (data.get("loan_type", loan["loan_type"]) or "").strip(),
                principal,
                current_balance,
                interest_rate,
                (data.get("repayment_type", loan["repayment_type"]) or "").strip(),
                (data.get("start_date", loan["start_date"]) or "").strip(),
                (data.get("end_date", loan["end_date"]) or "").strip(),
                payment_day,
                monthly_payment,
                (data.get("payment_months", loan["payment_months"]) or "").strip(),
                (data.get("withdrawal_account", loan["withdrawal_account"]) or "").strip(),
                (data.get("memo", loan["memo"]) or "").strip(),
                sort_order,
                loan_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/loans/<int:loan_id>/paid-check", methods=["PUT"])
def toggle_loan_paid_check(loan_id):
    """이번 달 상환했는지 체크/해제. 월이 바뀌면 자동으로 다시 미체크 상태가 된다."""
    data = request.get_json(force=True) or {}
    checked = bool(data.get("checked"))
    conn = get_conn()
    try:
        value = date.today().strftime("%Y-%m") if checked else ""
        conn.execute("UPDATE loans SET paid_check_month=? WHERE id=?", (value, loan_id))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/loans/<int:loan_id>", methods=["DELETE"])
def delete_loan(loan_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM loans WHERE id = ?", (loan_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/loans/<int:loan_id>/payments", methods=["POST"])
def log_loan_payment(loan_id):
    """상환 기록을 "이번에 얼마 갚았나"가 아니라 "상환 후 잔액이 얼마 남았나"로
    입력받는다 - 명세서에 찍힌 숫자를 그대로 넣으면 되고, 계산해서 뺄 필요가 없다."""
    data = request.get_json(force=True) or {}
    conn = get_conn()
    try:
        loan = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
        if not loan:
            return jsonify({"error": "대출을 찾을 수 없습니다."}), 404

        try:
            balance_after = float(data.get("balance_after"))
        except (TypeError, ValueError):
            return jsonify({"error": "상환 후 잔액을 입력하세요."}), 400
        if balance_after < 0:
            return jsonify({"error": "잔액은 0 이상이어야 합니다."}), 400

        paid_at = (data.get("paid_at") or "").strip()
        try:
            datetime.strptime(paid_at, "%Y-%m-%d")
        except ValueError:
            paid_at = date.today().isoformat()

        memo = (data.get("memo") or "").strip()
        amount = (loan["current_balance"] or 0) - balance_after

        conn.execute(
            "INSERT INTO loan_payments (loan_id, paid_at, amount, balance_after, memo) VALUES (?, ?, ?, ?, ?)",
            (loan_id, paid_at, amount, balance_after, memo),
        )
        conn.execute("UPDATE loans SET current_balance = ? WHERE id = ?", (balance_after, loan_id))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/loan-payments/<int:payment_id>", methods=["DELETE"])
def delete_loan_payment(payment_id):
    """상환 기록을 지우면, 그 대출의 현재 잔액을 "남아있는 기록 중 가장 최근 것의
    잔액"(기록이 하나도 없으면 원금)으로 다시 계산한다 - 중간 기록을 지워도
    최신 기록 기준으로 항상 맞게 유지하기 위해(삭제 순서와 무관하게 안전)."""
    conn = get_conn()
    try:
        payment = conn.execute("SELECT loan_id FROM loan_payments WHERE id = ?", (payment_id,)).fetchone()
        if payment:
            loan_id = payment["loan_id"]
            conn.execute("DELETE FROM loan_payments WHERE id = ?", (payment_id,))
            latest = conn.execute(
                "SELECT balance_after FROM loan_payments WHERE loan_id = ? ORDER BY paid_at DESC, id DESC LIMIT 1",
                (loan_id,),
            ).fetchone()
            if latest:
                new_balance = latest["balance_after"]
            else:
                principal_row = conn.execute("SELECT principal FROM loans WHERE id = ?", (loan_id,)).fetchone()
                new_balance = principal_row["principal"] if principal_row else 0
            conn.execute("UPDATE loans SET current_balance = ? WHERE id = ?", (new_balance, loan_id))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


# ---- 고정지출(구독/자동이체) ----


@app.route("/api/recurring", methods=["POST"])
def create_recurring_payment():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "이름을 입력하세요."}), 400
    try:
        amount = float(data.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0
    try:
        payment_day = int(data.get("payment_day") or 0)
    except (TypeError, ValueError):
        payment_day = 0
    card_id = _parse_card_id(data.get("card_id"))

    conn = get_conn()
    try:
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) AS m FROM recurring_payments").fetchone()["m"]
        conn.execute(
            """INSERT INTO recurring_payments (name, category, amount, payment_method, card_id,
               payment_day, active, memo, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                (data.get("category") or "").strip(),
                amount,
                (data.get("payment_method") or "").strip(),
                card_id,
                payment_day,
                1 if data.get("active", True) else 0,
                (data.get("memo") or "").strip(),
                max_order + 1,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/recurring/<int:item_id>", methods=["PUT"])
def update_recurring_payment(item_id):
    data = request.get_json(force=True) or {}
    conn = get_conn()
    try:
        item = conn.execute("SELECT * FROM recurring_payments WHERE id = ?", (item_id,)).fetchone()
        if not item:
            return jsonify({"error": "항목을 찾을 수 없습니다."}), 404

        name = (data.get("name") or item["name"]).strip()
        try:
            amount = float(data.get("amount", item["amount"]))
        except (TypeError, ValueError):
            amount = item["amount"]
        try:
            payment_day = int(data.get("payment_day", item["payment_day"]))
        except (TypeError, ValueError):
            payment_day = item["payment_day"]
        active = 1 if data.get("active", bool(item["active"])) else 0
        card_id = _parse_card_id(data["card_id"]) if "card_id" in data else item["card_id"]

        conn.execute(
            """UPDATE recurring_payments SET name=?, category=?, amount=?, payment_method=?,
               card_id=?, payment_day=?, active=?, memo=? WHERE id=?""",
            (
                name,
                (data.get("category", item["category"]) or "").strip(),
                amount,
                (data.get("payment_method", item["payment_method"]) or "").strip(),
                card_id,
                payment_day,
                active,
                (data.get("memo", item["memo"]) or "").strip(),
                item_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/recurring/<int:item_id>", methods=["DELETE"])
def delete_recurring_payment(item_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM recurring_payments WHERE id = ?", (item_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/recurring/<int:item_id>/match-candidates", methods=["GET"])
def recurring_match_candidates(item_id):
    """이번 달 결제 알림 중 아직 다른 고정지출에 안 묶인 것들을 후보로 보여준다.
    혜택배정 큐(status)는 전혀 건드리지 않으므로, 이미 혜택에 배정됐거나
    "혜택 없음" 처리된 알림도 후보에 포함된다(고정지출 확인은 별개 작업)."""
    conn = get_conn()
    try:
        current_ym = current_year_month()
        rows = conn.execute(
            """SELECT * FROM inbox_items
               WHERE recurring_id IS NULL AND substr(occurred_at, 1, 7) = ?
               ORDER BY occurred_at DESC""",
            (current_ym,),
        ).fetchall()
        return jsonify([
            {
                "id": r["id"],
                "amount": r["amount"],
                "merchant": r["merchant"],
                "last4": r["last4"],
                "occurred_at": r["occurred_at"],
                "status": r["status"],
            }
            for r in rows
        ])
    finally:
        conn.close()


@app.route("/api/recurring/<int:item_id>/confirm-match", methods=["POST"])
def confirm_recurring_match(item_id):
    data = request.get_json(force=True) or {}
    try:
        inbox_id = int(data.get("inbox_item_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "결제 내역을 선택하세요."}), 400

    conn = get_conn()
    try:
        item = conn.execute("SELECT * FROM inbox_items WHERE id = ?", (inbox_id,)).fetchone()
        if not item or item["recurring_id"] is not None:
            return jsonify({"error": "이미 처리되었거나 존재하지 않는 내역입니다."}), 404
        conn.execute(
            "UPDATE inbox_items SET recurring_id = ? WHERE id = ?",
            (item_id, inbox_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/recurring/<int:item_id>/unmatch", methods=["POST"])
def unmatch_recurring(item_id):
    """이번 달 매칭만 취소한다(알림의 status는 원래 손댄 적이 없으므로 그대로 둠)."""
    conn = get_conn()
    try:
        current_ym = current_year_month()
        conn.execute(
            """UPDATE inbox_items SET recurring_id = NULL
               WHERE recurring_id = ? AND substr(occurred_at, 1, 7) = ?""",
            (item_id, current_ym),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/match/<int:inbox_id>/unlink", methods=["POST"])
def unlink_match(inbox_id):
    """고정지출/보험 "내역" 패널에서 특정 건(이번 달이든 지난 달이든) 매칭만
    하나 풀어준다. 알림 자체(inbox_items 행)나 status는 그대로 둔다."""
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE inbox_items SET recurring_id = NULL, insurance_id = NULL WHERE id = ?",
            (inbox_id,),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/recurring/auto-match", methods=["POST"])
def auto_match_all_recurring():
    """쌓여있는 과거 알림까지 포함해서, 금액+결제수단(카드 뒷자리)이 애매하지
    않게 정확히 일치하는 것만 한 번에 자동으로 매칭한다."""
    conn = get_conn()
    try:
        pending_ids = [
            r["id"] for r in conn.execute(
                "SELECT id FROM inbox_items WHERE recurring_id IS NULL"
            )
        ]
        matched = 0
        for iid in pending_ids:
            if _try_auto_match_recurring(conn, iid):
                matched += 1
        conn.commit()
    finally:
        conn.close()
    result = serialize_full()
    result["auto_match_count"] = matched
    return jsonify(result)


@app.route("/api/insurances", methods=["POST"])
def create_insurance():
    data = request.get_json(force=True) or {}
    company = (data.get("company") or "").strip()
    product_name = (data.get("product_name") or "").strip()
    if not product_name:
        return jsonify({"error": "상품명을 입력하세요."}), 400
    try:
        monthly_premium = float(data.get("monthly_premium") or 0)
    except (TypeError, ValueError):
        monthly_premium = 0
    try:
        payment_day = int(data.get("payment_day") or 0)
    except (TypeError, ValueError):
        payment_day = 0
    card_id = _parse_card_id(data.get("card_id"))

    conn = get_conn()
    try:
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) AS m FROM insurances").fetchone()["m"]
        conn.execute(
            """INSERT INTO insurances (company, product_name, insured_name, monthly_premium,
               payment_day, payment_method, card_id, start_date, payment_end_date, maturity_date,
               active, memo, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                company,
                product_name,
                (data.get("insured_name") or "본인").strip(),
                monthly_premium,
                payment_day,
                (data.get("payment_method") or "").strip(),
                card_id,
                (data.get("start_date") or "").strip(),
                (data.get("payment_end_date") or "").strip(),
                (data.get("maturity_date") or "").strip(),
                1 if data.get("active", True) else 0,
                (data.get("memo") or "").strip(),
                max_order + 1,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/insurances/<int:item_id>", methods=["PUT"])
def update_insurance(item_id):
    data = request.get_json(force=True) or {}
    conn = get_conn()
    try:
        item = conn.execute("SELECT * FROM insurances WHERE id = ?", (item_id,)).fetchone()
        if not item:
            return jsonify({"error": "항목을 찾을 수 없습니다."}), 404

        product_name = (data.get("product_name") or item["product_name"]).strip()
        try:
            monthly_premium = float(data.get("monthly_premium", item["monthly_premium"]))
        except (TypeError, ValueError):
            monthly_premium = item["monthly_premium"]
        try:
            payment_day = int(data.get("payment_day", item["payment_day"]))
        except (TypeError, ValueError):
            payment_day = item["payment_day"]
        try:
            sort_order = int(data.get("sort_order", item["sort_order"]))
        except (TypeError, ValueError):
            sort_order = item["sort_order"]
        active = 1 if data.get("active", bool(item["active"])) else 0
        card_id = _parse_card_id(data["card_id"]) if "card_id" in data else item["card_id"]

        conn.execute(
            """UPDATE insurances SET company=?, product_name=?, insured_name=?, monthly_premium=?,
               payment_day=?, payment_method=?, card_id=?, start_date=?, payment_end_date=?,
               maturity_date=?, active=?, memo=?, sort_order=? WHERE id=?""",
            (
                (data.get("company", item["company"]) or "").strip(),
                product_name,
                (data.get("insured_name", item["insured_name"]) or "본인").strip(),
                monthly_premium,
                payment_day,
                (data.get("payment_method", item["payment_method"]) or "").strip(),
                card_id,
                (data.get("start_date", item["start_date"]) or "").strip(),
                (data.get("payment_end_date", item["payment_end_date"]) or "").strip(),
                (data.get("maturity_date", item["maturity_date"]) or "").strip(),
                active,
                (data.get("memo", item["memo"]) or "").strip(),
                sort_order,
                item_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/insurances/<int:item_id>", methods=["DELETE"])
def delete_insurance(item_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM insurances WHERE id = ?", (item_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/insurances/<int:item_id>/match-candidates", methods=["GET"])
def insurance_match_candidates(item_id):
    """이번 달 결제 알림 중 아직 다른 보험/고정지출에 안 묶인 것들을 후보로 보여준다."""
    conn = get_conn()
    try:
        current_ym = current_year_month()
        rows = conn.execute(
            """SELECT * FROM inbox_items
               WHERE insurance_id IS NULL AND recurring_id IS NULL AND substr(occurred_at, 1, 7) = ?
               ORDER BY occurred_at DESC""",
            (current_ym,),
        ).fetchall()
        return jsonify([
            {
                "id": r["id"],
                "amount": r["amount"],
                "merchant": r["merchant"],
                "last4": r["last4"],
                "occurred_at": r["occurred_at"],
                "status": r["status"],
            }
            for r in rows
        ])
    finally:
        conn.close()


@app.route("/api/insurances/<int:item_id>/confirm-match", methods=["POST"])
def confirm_insurance_match(item_id):
    data = request.get_json(force=True) or {}
    try:
        inbox_id = int(data.get("inbox_item_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "결제 내역을 선택하세요."}), 400

    conn = get_conn()
    try:
        item = conn.execute("SELECT * FROM inbox_items WHERE id = ?", (inbox_id,)).fetchone()
        if not item or item["insurance_id"] is not None:
            return jsonify({"error": "이미 처리되었거나 존재하지 않는 내역입니다."}), 404
        conn.execute(
            "UPDATE inbox_items SET insurance_id = ? WHERE id = ?",
            (item_id, inbox_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/insurances/<int:item_id>/unmatch", methods=["POST"])
def unmatch_insurance(item_id):
    conn = get_conn()
    try:
        current_ym = current_year_month()
        conn.execute(
            """UPDATE inbox_items SET insurance_id = NULL
               WHERE insurance_id = ? AND substr(occurred_at, 1, 7) = ?""",
            (item_id, current_ym),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/insurances/auto-match", methods=["POST"])
def auto_match_all_insurances():
    conn = get_conn()
    try:
        pending_ids = [
            r["id"] for r in conn.execute(
                "SELECT id FROM inbox_items WHERE insurance_id IS NULL AND recurring_id IS NULL"
            )
        ]
        matched = 0
        for iid in pending_ids:
            if _try_auto_match_insurance(conn, iid):
                matched += 1
        conn.commit()
    finally:
        conn.close()
    result = serialize_full()
    result["auto_match_count"] = matched
    return jsonify(result)


@app.route("/api/medical-expenses/<int:item_id>/claim", methods=["POST"])
def toggle_medical_claim(item_id):
    """병원비/약국 결제 하나를 실손 등으로 청구했는지 체크/해제한다."""
    data = request.get_json(force=True, silent=True) or {}
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE inbox_items SET medical_claimed = ? WHERE id = ?",
            (1 if data.get("claimed") else 0, item_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/medical-expenses/bulk-exclude", methods=["POST"])
def bulk_exclude_medical_expenses():
    """병원비 목록에서 여러 건을 한 번에 "이건 병원비 아님"으로 제외한다.
    inbox_items 자체는 안 지우고 medical_excluded만 켜서, 이 목록에서만 다시
    안 보이게 한다(혜택배정/고정지출·보험 매칭에는 영향 없음)."""
    data = request.get_json(force=True) or {}
    ids = data.get("ids") or []
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return jsonify({"error": "제외할 항목을 선택하세요."}), 400
    if not ids:
        return jsonify({"error": "제외할 항목을 선택하세요."}), 400

    conn = get_conn()
    try:
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE inbox_items SET medical_excluded = 1 WHERE id IN ({placeholders})", ids
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


def _clean_tier_table(raw):
    """[[기준금액,한도], ...] 형태인지 검증하고 정돈된 JSON 문자열(또는 '')을 돌려준다."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        tiers = json.loads(raw)
        if not isinstance(tiers, list):
            raise ValueError
        cleaned = [[float(t[0]), float(t[1])] for t in tiers]
    except (ValueError, TypeError, IndexError, KeyError):
        raise ValueError("구간표 형식이 올바르지 않습니다. 예: [[400000,5000],[800000,10000]]")
    return json.dumps(cleaned)


def _clean_rate_table(raw):
    """[["키워드1,키워드2",할인율], ...] 또는 [["키워드",할인율,건당한도], ...] 또는
    [["키워드",할인율,건당한도,일한도,월한도], ...] 또는
    [["키워드",할인율,건당한도,일한도,월한도,업종별월적립한도], ...] 형태인지 검증하고
    정돈된 JSON 문자열(또는 '')을 돌려준다."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        rows = json.loads(raw)
        if not isinstance(rows, list):
            raise ValueError
        cleaned = []
        for r in rows:
            if len(r) not in (2, 3, 4, 5, 6):
                raise ValueError
            row = [str(r[0]), float(r[1])]
            for extra in r[2:]:
                row.append(float(extra))
            cleaned.append(row)
    except (ValueError, TypeError, IndexError, KeyError):
        raise ValueError(
            "업종별 할인율 형식이 올바르지 않습니다. "
            '예: [["쇼핑몰,백화점,이커머스",10],["버스,지하철,택시",5]] '
            '(건당한도는 3번째 값, 일/월 횟수한도는 4·5번째 값, 업종별 월 적립(포인트) '
            '한도는 6번째 값으로 추가 가능: [["쇼핑",10,50000,1,5,10000]])'
        )
    return json.dumps(cleaned, ensure_ascii=False)


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
    calc_mode = data.get("calc_mode") if data.get("calc_mode") in ("raw", "change_under_1000", "percent_discount") else "raw"
    try:
        discount_percent = float(data.get("discount_percent") or 0)
    except (TypeError, ValueError):
        discount_percent = 0
    try:
        per_txn_cap = float(data.get("per_txn_cap") or 0)
    except (TypeError, ValueError):
        per_txn_cap = 0
    try:
        tier_table = _clean_tier_table(data.get("tier_table"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    try:
        rate_table = _clean_rate_table(data.get("rate_table"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    always_doubled = 1 if data.get("always_doubled") else 0

    conn = get_conn()
    try:
        card = conn.execute("SELECT id FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not card:
            return jsonify({"error": "카드를 찾을 수 없습니다."}), 404
        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS m FROM benefits WHERE card_id = ?", (card_id,)
        ).fetchone()["m"]
        conn.execute(
            """INSERT INTO benefits (card_id, name, limit_type, limit_value, memo, merchant_keywords, tier_table,
               calc_mode, discount_percent, per_txn_cap, rate_table, always_doubled, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                card_id, name, limit_type, limit_value,
                (data.get("memo") or "").strip(),
                (data.get("merchant_keywords") or "").strip(),
                tier_table,
                calc_mode,
                discount_percent,
                per_txn_cap,
                rate_table,
                always_doubled,
                max_order + 1,
            ),
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
        calc_mode = data.get("calc_mode") if data.get("calc_mode") in ("raw", "change_under_1000", "percent_discount") else b["calc_mode"]
        try:
            discount_percent = float(data.get("discount_percent", b["discount_percent"]))
        except (TypeError, ValueError):
            discount_percent = b["discount_percent"]
        try:
            per_txn_cap = float(data.get("per_txn_cap", b["per_txn_cap"]))
        except (TypeError, ValueError):
            per_txn_cap = b["per_txn_cap"]
        try:
            tier_table = _clean_tier_table(data.get("tier_table", b["tier_table"]))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        try:
            rate_table = _clean_rate_table(data.get("rate_table", b["rate_table"]))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        always_doubled = 1 if data.get("always_doubled", b["always_doubled"]) else 0
        new_memo = (data.get("memo", b["memo"]) or "").strip()
        # 사용자가 설명(memo)을 실제로 바꾼 경우에만 "직접 수정함" 표시를 남긴다.
        # 이 표시가 있어야 update.py가 나중에 안내장 기준 설명으로 덮어쓰지 않고
        # 사용자가 쓴 내용을 지켜준다.
        memo_edited_by_user = 1 if new_memo != (b["memo"] or "") else b["memo_edited_by_user"]

        conn.execute(
            """UPDATE benefits SET name = ?, limit_type = ?, limit_value = ?, memo = ?, merchant_keywords = ?,
               tier_table = ?, calc_mode = ?, discount_percent = ?, per_txn_cap = ?, rate_table = ?,
               always_doubled = ?, memo_edited_by_user = ? WHERE id = ?""",
            (
                name, limit_type, limit_value,
                new_memo,
                (data.get("merchant_keywords", b["merchant_keywords"]) or "").strip(),
                tier_table,
                calc_mode,
                discount_percent,
                per_txn_cap,
                rate_table,
                always_doubled,
                memo_edited_by_user,
                benefit_id,
            ),
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


def _duplicate_merchant_today(conn, benefit_id, used_at, merchant):
    """더모아형 혜택은 "동일 가맹점 1일 1회"만 적립되므로, 같은 날 같은
    가맹점으로 이미 기록이 있으면 그 뒤 결제는 적립 대상에서 제외한다."""
    if not merchant:
        return False
    row = conn.execute(
        """SELECT 1 FROM usage_logs WHERE benefit_id = ? AND used_at = ? AND merchant = ?
           LIMIT 1""",
        (benefit_id, used_at, merchant),
    ).fetchone()
    return row is not None


def _category_usage_count(conn, benefit_id, category, used_at, monthly=False):
    """rate_table 업종(category)별로 오늘(또는 이번 달) 몇 번 사용됐는지 센다.
    Daily Plan처럼 "업종별 일 1회, 월 5회까지"인 경우 한도 초과 여부 확인에 쓴다."""
    if monthly:
        row = conn.execute(
            """SELECT COUNT(*) AS c FROM usage_logs
               WHERE benefit_id = ? AND rate_category = ? AND substr(used_at, 1, 7) = ?""",
            (benefit_id, category, used_at[:7]),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM usage_logs WHERE benefit_id = ? AND rate_category = ? AND used_at = ?",
            (benefit_id, category, used_at),
        ).fetchone()
    return row["c"]


def _category_month_earned(conn, benefit_id, category, used_at):
    """rate_table 업종(category)별로 이번 달 이미 적립된 금액(포인트) 합계를 돌려준다.
    씨티 리워드처럼 "업종별 월 1만 포인트까지만"처럼 횟수가 아니라 적립액 자체를
    업종별로 깎는 카드의 한도 체크에 쓴다."""
    row = conn.execute(
        """SELECT COALESCE(SUM(used_value), 0) AS total FROM usage_logs
           WHERE benefit_id = ? AND rate_category = ? AND substr(used_at, 1, 7) = ?""",
        (benefit_id, category, used_at[:7]),
    ).fetchone()
    return row["total"]


def _perf_spend_for_card(conn, card):
    """카드의 "전월실적" 합계 (_serialize_cards와 같은 기준: 수동 입력이 있으면
    그걸 쓰고, 없으면 지난달 확인된 결제 알림 합계에서 실적 제외 항목을 뺀 자동
    집계). 구간표(tier_table)가 있는 혜택이 전월실적 미달로 이번 달 혜택 대상이
    아닌지 판단할 때 재사용한다."""
    prev_month = previous_year_month()
    manual_row = conn.execute(
        "SELECT total_spend FROM performance WHERE card_id = ? AND year_month = ?",
        (card["id"], prev_month),
    ).fetchone()
    if manual_row:
        return manual_row["total_spend"]
    rows = conn.execute(
        """SELECT amount, merchant FROM inbox_items
           WHERE card_id = ? AND status IN ('assigned', 'no_benefit')
           AND substr(occurred_at, 1, 7) = ?""",
        (card["id"], prev_month),
    ).fetchall()
    return sum(
        r["amount"] for r in rows
        if not is_performance_excluded(r["merchant"], card["perf_excluded_keywords"])
    )


def _apply_calc_mode(conn, benefit_id, benefit, raw_value, doubled, memo, merchant="", used_at="", category_override=""):
    """혜택의 calc_mode에 따라 입력값(결제금액 등)을 실제 한도 소진값으로 변환한다.

    "raw"는 입력값을 그대로 쓰고, 나머지는 결제금액을 자동으로 실제
    혜택금액(잔돈/할인액)으로 환산한다 - 결제금액 총액이 그대로 한도를
    깎아먹지 않도록. (used_value, memo, rate_category, error) 를 돌려준다.
    error가 있으면(업종별 일/월 횟수 한도 초과 등) 기록하지 않고 그 문구를 보여준다.
    category_override가 있으면(네이버페이/토스페이처럼 가맹점명으로 업종을 알 수
    없어서 사용자가 직접 고른 경우) 가맹점명 매칭 대신 그 업종 값을 그대로 쓴다.
    """
    if benefit["tier_table"]:
        card = conn.execute("SELECT * FROM cards WHERE id = ?", (benefit["card_id"],)).fetchone()
        if card and tier_limit_for_spend(benefit["tier_table"], _perf_spend_for_card(conn, card)) == 0:
            return None, None, None, "전월실적이 최저 구간에도 못 미쳐서 이번 달은 이 혜택을 받을 수 없습니다."

    if benefit["calc_mode"] == "change_under_1000":
        doubled = doubled or bool(benefit["always_doubled"])
        earned = compute_change_earned(raw_value, doubled)
        parts = [f"결제 {raw_value:,.0f}원 → 잔돈 {earned:,.0f}원{'(2배)' if doubled else ''}"]
        if memo:
            parts.append(memo)
        return earned, " · ".join(parts), "", None

    if benefit["calc_mode"] == "percent_discount":
        if category_override:
            percent, cap, daily_limit, monthly_limit, monthly_point_cap = rate_table_entry_by_label(
                benefit["rate_table"], category_override, benefit["discount_percent"], benefit["per_txn_cap"]
            )
            category = category_override
        else:
            percent, cap, daily_limit, monthly_limit, monthly_point_cap, category = match_rate_table(
                merchant, benefit["rate_table"], benefit["discount_percent"], benefit["per_txn_cap"]
            )
        capped_note = ""
        if category and used_at:
            if daily_limit and _category_usage_count(conn, benefit_id, category, used_at) >= daily_limit:
                return None, None, None, f"이 업종은 하루 {daily_limit:g}회까지만 할인되는데, 오늘 이미 한도를 채워서 이 결제는 할인 대상이 아닙니다."
            if monthly_limit and _category_usage_count(conn, benefit_id, category, used_at, monthly=True) >= monthly_limit:
                return None, None, None, f"이 업종은 이번 달 {monthly_limit:g}회까지만 할인되는데, 이미 한도를 채워서 이 결제는 할인 대상이 아닙니다."
            if monthly_point_cap:
                already = _category_month_earned(conn, benefit_id, category, used_at)
                remaining = max(0, monthly_point_cap - already)
                if remaining <= 0:
                    return None, None, None, f"이 업종은 이번 달 적립 한도 {monthly_point_cap:,.0f}포인트를 이미 채워서 이 결제는 적립 대상이 아닙니다."
        earned = compute_percent_discount(raw_value, percent, cap)
        if category and used_at and monthly_point_cap:
            already = _category_month_earned(conn, benefit_id, category, used_at)
            remaining = max(0, monthly_point_cap - already)
            if earned > remaining:
                earned = remaining
                capped_note = f"(업종별 월한도 {monthly_point_cap:,.0f}P 초과분 제외)"
        category_label = f"{category} " if category else ""
        parts = [f"결제 {raw_value:,.0f}원 → 할인 {earned:,.0f}원({category_label}{percent:g}%){capped_note}"]
        if memo:
            parts.append(memo)
        return earned, " · ".join(parts), (category or ""), None

    return raw_value, memo, "", None


@app.route("/api/benefits/<int:benefit_id>/use", methods=["POST"])
def log_usage(benefit_id):
    data = request.get_json(force=True) or {}
    conn = get_conn()
    try:
        b = conn.execute("SELECT * FROM benefits WHERE id = ?", (benefit_id,)).fetchone()
        if not b:
            return jsonify({"error": "혜택을 찾을 수 없습니다."}), 404

        try:
            raw_value = float(data.get("used_value"))
        except (TypeError, ValueError):
            raw_value = 1 if b["limit_type"] == "count" else 0
        if raw_value == 0:
            return jsonify({"error": "사용 금액(또는 횟수)을 입력하세요."}), 400

        used_at = (data.get("used_at") or "").strip()
        try:
            datetime.strptime(used_at, "%Y-%m-%d")
        except ValueError:
            used_at = date.today().isoformat()

        merchant = (data.get("merchant") or "").strip()
        memo = (data.get("memo") or "").strip()
        category_override = (data.get("category") or "").strip()

        if b["calc_mode"] == "change_under_1000" and _duplicate_merchant_today(conn, benefit_id, used_at, merchant):
            return jsonify({
                "error": f"같은 날 \"{merchant}\"에 이미 기록이 있습니다. "
                         "동일 가맹점은 1일 1회만 적립되므로(가장 먼저 결제한 건만 인정), "
                         "이 결제는 적립 대상이 아닙니다."
            }), 400

        used_value, memo, rate_category, error = _apply_calc_mode(
            conn, benefit_id, b, raw_value, bool(data.get("doubled")), memo, merchant, used_at, category_override
        )
        if error:
            return jsonify({"error": error}), 400

        conn.execute(
            """INSERT INTO usage_logs (benefit_id, used_value, used_at, merchant, memo, rate_category)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (benefit_id, used_value, used_at, merchant, memo, rate_category),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/logs/<int:log_id>", methods=["DELETE"])
def delete_log(log_id):
    conn = get_conn()
    try:
        log = conn.execute("SELECT source_inbox_id FROM usage_logs WHERE id = ?", (log_id,)).fetchone()
        conn.execute("DELETE FROM usage_logs WHERE id = ?", (log_id,))
        if log and log["source_inbox_id"]:
            # 받은 알림에서 배정되어 만들어진 기록이면, 삭제할 때 알림을
            # 없애버리지 않고 "받은 결제 알림" 목록으로 되돌린다(다시 배정 가능).
            conn.execute(
                "UPDATE inbox_items SET status = 'pending', card_id = NULL, benefit_id = NULL WHERE id = ?",
                (log["source_inbox_id"],),
            )
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
    text = (data.get("text") or "").strip()
    if not text:
        # 유효한 JSON이 아니거나 "text" 필드가 없으면, 본문 전체를 알림 원문으로
        # 취급한다. MacroDroid 등에서 알림 텍스트를 JSON으로 정확히 감싸서
        # 보내기 번거로운 경우, 그냥 알림 텍스트를 body에 그대로 넣어도 되게
        # 하기 위함(줄바꿈/따옴표 이스케이프를 신경 쓸 필요가 없어짐).
        text = (request.get_data(as_text=True) or "").strip()

    conn = get_conn()
    try:
        expected_token = _get_or_create_inbox_token(conn)
        if not _inbox_authorized(request, expected_token):
            return jsonify({"error": "인증 토큰이 올바르지 않습니다."}), 401

        if not text:
            return jsonify({"error": "text가 비어 있습니다."}), 400

        parsed = parse_notification(text)
        occurred_at = (data.get("occurred_at") or "").strip() or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # MacroDroid 등에서 알림 하나에 트리거가 두 번 발동하는 경우가 있어서,
        # 같은 문구가 60초 안에 다시 들어오면 중복으로 보고 건너뛴다.
        recent_dup = conn.execute(
            """SELECT 1 FROM inbox_items WHERE raw_text = ?
               AND created_at >= datetime('now', 'localtime', '-60 seconds') LIMIT 1""",
            (text,),
        ).fetchone()
        if not recent_dup:
            cur = conn.execute(
                """INSERT INTO inbox_items (raw_text, amount, last4, issuer, merchant, occurred_at, source)
                   VALUES (?, ?, ?, ?, ?, ?, 'notification')""",
                (text, parsed["amount"], parsed["last4"], parsed["issuer"], parsed["merchant"], occurred_at),
            )
            _try_auto_match_recurring(conn, cur.lastrowid)
            _try_auto_match_insurance(conn, cur.lastrowid)
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
            raw_value = float(data.get("amount") if data.get("amount") not in (None, "") else item["amount"])
        except (TypeError, ValueError):
            raw_value = 0
        if not raw_value:
            return jsonify({"error": "금액을 확인할 수 없습니다. 직접 입력해주세요."}), 400

        used_at = (data.get("used_at") or "").strip() or (item["occurred_at"] or "")[:10]
        try:
            datetime.strptime(used_at, "%Y-%m-%d")
        except ValueError:
            used_at = date.today().isoformat()

        merchant = (data.get("merchant") or item["merchant"] or "").strip()
        memo = (data.get("memo") or "").strip()
        category_override = (data.get("category") or "").strip()

        if benefit["calc_mode"] == "change_under_1000" and _duplicate_merchant_today(conn, benefit_id, used_at, merchant):
            return jsonify({
                "error": f"같은 날 \"{merchant}\"에 이미 기록이 있습니다. "
                         "동일 가맹점은 1일 1회만 적립되므로(가장 먼저 결제한 건만 인정), "
                         "이 결제는 적립 대상이 아닙니다. 알림을 무시 처리해주세요."
            }), 400

        used_value, memo, rate_category, error = _apply_calc_mode(
            conn, benefit_id, benefit, raw_value, bool(data.get("doubled")), memo, merchant, used_at, category_override
        )
        if error:
            return jsonify({"error": error}), 400

        conn.execute(
            """INSERT INTO usage_logs (benefit_id, used_value, used_at, merchant, memo, source_inbox_id, rate_category)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (benefit_id, used_value, used_at, merchant, memo, item_id, rate_category),
        )
        conn.execute(
            "UPDATE inbox_items SET status = 'assigned', card_id = ?, benefit_id = ? WHERE id = ?",
            (benefit["card_id"], benefit_id, item_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/inbox/<int:item_id>/mark-no-benefit", methods=["POST"])
def mark_no_benefit(item_id):
    """해당하는 혜택이 없는(그러나 실제로 결제는 한) 알림을 "받은 결제 알림"
    목록에서 빼되, 완전히 삭제(=이 알림 무시)하지 않고 별도 목록에 남겨둔다."""
    data = request.get_json(force=True, silent=True) or {}
    card_id = data.get("card_id") or None

    conn = get_conn()
    try:
        item = conn.execute("SELECT * FROM inbox_items WHERE id = ?", (item_id,)).fetchone()
        if not item or item["status"] != "pending":
            return jsonify({"error": "이미 처리되었거나 존재하지 않는 알림입니다."}), 404

        conn.execute(
            "UPDATE inbox_items SET status = 'no_benefit', card_id = ?, benefit_id = NULL WHERE id = ?",
            (card_id, item_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/inbox/<int:item_id>/reopen", methods=["POST"])
def reopen_inbox_item(item_id):
    """"혜택 없음"으로 처리했던 알림을 다시 "받은 결제 알림" 목록으로 되돌린다."""
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE inbox_items SET status = 'pending', card_id = NULL, benefit_id = NULL WHERE id = ?",
            (item_id,),
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


@app.route("/api/inbox/bulk-delete", methods=["POST"])
def bulk_delete_inbox_items():
    """받은 알림/혜택없음 처리 내역 리스트에서 여러 개를 한 번에 완전 삭제한다.
    과거 카드내역을 몇 년치 업로드했을 때 필요없는 알림을 한 번에 치우는 용도 -
    혜택 사용기록(usage_logs) 자체는 지우지 않는다(source_inbox_id만 비워짐)."""
    data = request.get_json(force=True) or {}
    ids = data.get("ids") or []
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return jsonify({"error": "삭제할 항목을 선택하세요."}), 400
    if not ids:
        return jsonify({"error": "삭제할 항목을 선택하세요."}), 400

    conn = get_conn()
    try:
        placeholders = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM inbox_items WHERE id IN ({placeholders})", ids)
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

        # 같은 양식을 다시 올려도 중복으로 안 쌓이도록, 이미 있는 (혜택, 날짜,
        # 금액, 메모) 조합을 미리 읽어둔다.
        seen_combo = {
            (r["benefit_id"], r["used_at"], r["used_value"], r["memo"] or "")
            for r in conn.execute("SELECT benefit_id, used_at, used_value, memo FROM usage_logs")
        }

        added = 0
        skipped = []
        for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if row is None or all(v in (None, "") for v in row):
                continue
            card_name, benefit_name, used_at, used_value = (list(row) + [None] * 4)[:4]
            memo = row[4] if len(row) > 4 else None
            memo_str = str(memo).strip() if memo else ""

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

            benefit_id = benefit_lookup[key]
            combo = (benefit_id, used_at_str, used_value, memo_str)
            if combo in seen_combo:
                skipped.append(f"{i}행: 같은 혜택·날짜·금액 내역이 이미 있어 중복으로 보고 건너뜀")
                continue
            seen_combo.add(combo)

            conn.execute(
                "INSERT INTO usage_logs (benefit_id, used_value, used_at, memo) VALUES (?, ?, ?, ?)",
                (benefit_id, used_value, used_at_str, memo_str),
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
    approval_col = header_info.get("approval_col")
    usage_type_col = header_info.get("usage_type_col")

    conn = get_conn()
    try:
        # 엑셀에 있는 행은 원칙적으로 다 DB에 넣는다(취소거래/혜택 미등록
        # 카드라고 여기서 걸러서 빼지 않는다) - 화면에 뭘 보여줄지는
        # serialize_inbox 등 조회 쪽에서 결정한다. 다만 금액·날짜 자체를
        # 못 읽은 행은 저장할 최소한의 정보가 없어서 어쩔 수 없이 건너뛴다.

        # 중복 판단: 승인번호가 있으면 승인번호로 우선 확인하고(단, 승인번호가
        # 서로 다르면 별개 결제로 본다), 그 다음 금액이 같은 기존 기록들과
        # 카드번호·시간을 비교한다. 카드번호는 폰 알림은 4자리("8390"),
        # 카드사 엑셀은 "본인839*"처럼 3자리("839")로 마스킹돼 나오는 등
        # 자릿수가 다를 수 있어서 card_codes_match(접두 비교)로 확인한다.
        # 시간은 양쪽 다 시간(분초)까지 있으면 5분 이내를 같은 결제로 보고
        # (폰 알림 수신 시각과 카드사 승인 시각이 몇 분 정도는 어긋날 수
        # 있어서), 한쪽이라도 시간이 없으면 날짜만 비교한다. 이렇게 해야
        # 폰 알림으로 이미 들어온 것과 나중에 엑셀로 또 들어오는 같은 결제를,
        # 승인번호·카드번호 자릿수가 다르더라도 서로 잡아낼 수 있다.
        seen_approval = set()
        existing_by_amount = defaultdict(list)
        for r in conn.execute("SELECT last4, amount, occurred_at, approval_no FROM inbox_items"):
            if r["approval_no"]:
                seen_approval.add(r["approval_no"])
            existing_by_amount[r["amount"]].append([r["last4"] or "", r["occurred_at"] or "", r["approval_no"] or ""])

        def _last4_compatible(a, b):
            if not a or not b:
                return a == b
            return card_codes_match(a, b)

        def _parse_ts(s):
            if not s:
                return None
            try:
                return datetime.strptime(s, "%Y-%m-%d %H:%M:%S") if len(s) > 10 else datetime.strptime(s, "%Y-%m-%d")
            except ValueError:
                return None

        def _find_duplicate(last4, amount, occurred_at, approval_no):
            if approval_no and approval_no in seen_approval:
                return f"승인번호({approval_no})가 이미 등록되어 있어 중복으로 보고 건너뜀"
            new_ts = _parse_ts(occurred_at)
            for old_last4, old_occurred_at, old_approval in existing_by_amount.get(amount, []):
                if not _last4_compatible(last4, old_last4):
                    continue
                if approval_no and old_approval and approval_no != old_approval:
                    continue
                if len(occurred_at) > 10 and len(old_occurred_at) > 10:
                    old_ts = _parse_ts(old_occurred_at)
                    if new_ts and old_ts and abs((new_ts - old_ts).total_seconds()) <= 300:
                        return "같은 카드·금액·비슷한 시간 내역이 이미 있어 중복으로 보고 건너뜀"
                elif occurred_at[:10] == old_occurred_at[:10]:
                    return "같은 카드·금액·날짜 내역이 이미 있어 중복으로 보고 건너뜀"
            return None

        queued = 0
        cancelled = 0
        skipped = []
        for i, row in enumerate(
            ws.iter_rows(min_row=header_info["header_row"] + 1, values_only=True),
            start=header_info["header_row"] + 1,
        ):
            if row is None or all(v in (None, "") for v in row):
                continue

            amount = parse_amount_cell(row[amount_col] if amount_col < len(row) else None)
            if amount is None or amount == 0:
                skipped.append(f"{i}행: 금액을 확인할 수 없음")
                continue
            is_refund = amount < 0
            amount = abs(amount)

            used_at = parse_date_cell(row[date_col] if date_col < len(row) else None)
            if not used_at:
                skipped.append(f"{i}행: 날짜를 확인할 수 없음")
                continue
            occurred_at = parse_datetime_cell(row[date_col] if date_col < len(row) else None) or used_at

            merchant = ""
            if merchant_col is not None and merchant_col < len(row) and row[merchant_col] is not None:
                merchant = annotate_merchant(str(row[merchant_col]).strip())

            last4 = ""
            if cardno_col is not None and cardno_col < len(row):
                last4 = extract_last4(row[cardno_col])

            approval_no = ""
            if approval_col is not None and approval_col < len(row) and row[approval_col] not in (None, ""):
                approval_no = str(row[approval_col]).strip()

            usage_type = ""
            if usage_type_col is not None and usage_type_col < len(row) and row[usage_type_col] not in (None, ""):
                usage_type = str(row[usage_type_col]).strip()

            dup_reason = _find_duplicate(last4, amount, occurred_at, approval_no)
            if dup_reason:
                skipped.append(f"{i}행: {dup_reason}")
                continue
            if approval_no:
                seen_approval.add(approval_no)
            existing_by_amount[amount].append([last4, occurred_at, approval_no])

            status_col = header_info.get("status_col")
            is_cancelled = is_refund or (
                status_col is not None and status_col < len(row) and is_cancelled_status(row[status_col])
            )

            raw_parts = [p for p in [merchant, f"{amount:,.0f}원", used_at] if p]
            raw_text = " · ".join(raw_parts)

            cur = conn.execute(
                """INSERT INTO inbox_items
                   (raw_text, amount, last4, issuer, merchant, occurred_at, approval_no, usage_type, source, status)
                   VALUES (?, ?, ?, '', ?, ?, ?, ?, 'excel', ?)""",
                (raw_text, amount, last4, merchant, occurred_at, approval_no, usage_type,
                 "cancelled" if is_cancelled else "pending"),
            )
            if is_cancelled:
                cancelled += 1
            else:
                queued += 1
                # 폰 알림으로 실시간으로 들어올 때와 똑같이, 엑셀로 한번에
                # 올린 지난 내역도 여기서 바로 고정지출/보험 자동매칭을
                # 시도한다. 몇 달치를 한 번에 올려도 각 건이 해당하는 달에
                # 바로 매칭되게 하려는 것.
                _try_auto_match_recurring(conn, cur.lastrowid)
                _try_auto_match_insurance(conn, cur.lastrowid)
        conn.commit()
    finally:
        conn.close()

    if cancelled:
        skipped.append(f"(참고) 취소/환불로 보이는 {cancelled}건은 DB에는 저장했지만 목록에는 안 띄웠습니다.")

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
