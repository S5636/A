# -*- coding: utf-8 -*-
"""카드 남은혜택 체크 - PC/폰 공용 웹앱.

같은 와이파이(공유기)에 연결된 PC와 폰이 브라우저로 같은 서버(0.0.0.0)에
접속해서 카드별 혜택의 이번 달 남은 한도를 같이 보고 같이 기록한다.
"""
import os
from datetime import date, datetime

from flask import Flask, jsonify, render_template, request

from models import cycle_bounds, get_conn, init_db

app = Flask(__name__)


def serialize_state():
    conn = get_conn()
    try:
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
                "reset_day": card["reset_day"],
                "memo": card["memo"],
                "cycle_start": start.isoformat(),
                "cycle_end": end.isoformat(),
                "days_left": days_left,
                "benefits": benefit_list,
            })
        return result
    finally:
        conn.close()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    return jsonify(serialize_state())


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
            "INSERT INTO cards (name, issuer, reset_day, memo, sort_order) VALUES (?, ?, ?, ?, ?)",
            (name, (data.get("issuer") or "").strip(), reset_day, (data.get("memo") or "").strip(), max_order + 1),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_state())


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
            "UPDATE cards SET name = ?, issuer = ?, reset_day = ?, memo = ? WHERE id = ?",
            (
                name,
                (data.get("issuer", card["issuer"]) or "").strip(),
                reset_day,
                (data.get("memo", card["memo"]) or "").strip(),
                card_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_state())


@app.route("/api/cards/<int:card_id>", methods=["DELETE"])
def delete_card(card_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_state())


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
    return jsonify(serialize_state())


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
    return jsonify(serialize_state())


@app.route("/api/benefits/<int:benefit_id>", methods=["DELETE"])
def delete_benefit(benefit_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM benefits WHERE id = ?", (benefit_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_state())


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
    return jsonify(serialize_state())


@app.route("/api/logs/<int:log_id>", methods=["DELETE"])
def delete_log(log_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM usage_logs WHERE id = ?", (log_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_state())


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)
