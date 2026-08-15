# -*- coding: utf-8 -*-
"""SQLite 스키마 + 혜택 사이클(초기화 주기) 계산.

카드 하나(cards)는 여러 혜택 항목(benefits)을 가지고, 각 혜택 항목은
사용할 때마다 사용내역(usage_logs)이 쌓인다. 혜택은 카드마다 정한
"초기화 기준일"(reset_day, 예: 매월 1일 / 매월 15일 결제일 등)마다
사용량이 새로 리셋된다고 보고, 이번 사이클 동안 쓴 금액을 한도에서 빼서
남은 혜택을 계산한다.
"""
import calendar
import os
import re
import sqlite3
from datetime import date, datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "card_benefit.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    issuer TEXT DEFAULT '',
    last4 TEXT DEFAULT '',
    reset_day INTEGER NOT NULL DEFAULT 1,
    memo TEXT DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS benefits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    limit_type TEXT NOT NULL DEFAULT 'amount',   -- 'amount'(원) 또는 'count'(횟수)
    limit_value REAL NOT NULL DEFAULT 0,
    memo TEXT DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS usage_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    benefit_id INTEGER NOT NULL REFERENCES benefits(id) ON DELETE CASCADE,
    used_value REAL NOT NULL DEFAULT 0,
    used_at TEXT NOT NULL,          -- 'YYYY-MM-DD'
    memo TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

-- 폰 알림 자동수집(MacroDroid 등)으로 들어온, 아직 어떤 혜택인지 정해지지 않은 결제내역
CREATE TABLE IF NOT EXISTS inbox_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_text TEXT NOT NULL,
    amount REAL,
    last4 TEXT DEFAULT '',
    issuer TEXT DEFAULT '',
    merchant TEXT DEFAULT '',
    occurred_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    status TEXT NOT NULL DEFAULT 'pending',   -- 'pending' | 'assigned'
    card_id INTEGER REFERENCES cards(id) ON DELETE SET NULL,
    benefit_id INTEGER REFERENCES benefits(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_column(conn, table, column, ddl):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db():
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        _ensure_column(conn, "cards", "last4", "last4 TEXT DEFAULT ''")
        conn.commit()
    finally:
        conn.close()


def get_setting(conn, key, default=""):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


_AMOUNT_RE = re.compile(r"([\d][\d,]*)\s*원")
_LAST4_RE = re.compile(r"\((\d{4})\)")
_ISSUER_HINTS = [
    "신한카드", "KB국민카드", "국민카드", "삼성카드", "현대카드", "롯데카드", "하나카드",
    "우리카드", "NH농협카드", "농협카드", "비씨카드", "BC카드", "씨티카드",
    "카카오뱅크", "토스뱅크", "케이뱅크",
]
_NOISE_KEYWORDS = ["승인", "일시불", "할부", "카드", "누적", "이용", "잔액", "한도", "포인트"]


def parse_notification(text: str) -> dict:
    """카드 결제승인 알림 텍스트에서 금액/카드 뒤4자리/카드사/가맹점(추정)을 뽑아낸다.

    카드사마다 알림 문구 형식이 제각각이라 완벽한 파싱은 어렵고, 여기서는
    "일단 자동으로 채워주고 사람이 확인/보정"하는 걸 목표로 최대한 관대하게 뽑는다.
    """
    text = text or ""

    amount = None
    m = _AMOUNT_RE.search(text)
    if m:
        try:
            amount = float(m.group(1).replace(",", ""))
        except ValueError:
            amount = None

    last4_match = _LAST4_RE.search(text)
    last4 = last4_match.group(1) if last4_match else ""

    issuer = ""
    for hint in _ISSUER_HINTS:
        if hint in text:
            issuer = hint
            break

    merchant = ""
    for line in reversed([ln.strip(" -\t") for ln in text.splitlines() if ln.strip()]):
        if not line or any(k in line for k in _NOISE_KEYWORDS):
            continue
        merchant = line
        break

    return {"amount": amount, "last4": last4, "issuer": issuer, "merchant": merchant}


# ---- 카드사에서 다운로드한 엑셀(이용내역서) 자동인식 ----
# 카드사마다 열 이름이 제각각이라, 흔히 쓰이는 헤더 표현들을 넓게 잡아서
# "이 표에서 어느 열이 날짜/금액/가맹점/카드번호인지" 추측한다.

_DATE_HEADERS = ["이용일자", "이용일", "승인일자", "거래일자", "거래일", "일자", "날짜", "결제일자", "결제일"]
_AMOUNT_HEADERS = ["이용금액", "승인금액", "결제금액", "청구금액", "이용대금", "금액"]
_MERCHANT_HEADERS = ["가맹점명", "가맹점", "사용처", "이용가맹점", "적요", "내용", "상호"]
_CARDNO_HEADERS = ["카드번호", "카드no", "카드 no", "카드"]


def _norm_cell(cell):
    return str(cell).strip() if cell is not None else ""


def _match_col(header_cells, candidates):
    # candidates는 구체적인 표현부터 순서대로 나열되어 있다고 보고, 후보를
    # 바깥 루프로 돌려서 구체적인 헤더명이 더 넓은(포괄적인) 헤더명보다
    # 먼저 매칭되게 한다 (예: "카드번호"가 그냥 "카드"보다 우선).
    for cand in candidates:
        for idx, cell in enumerate(header_cells):
            c = _norm_cell(cell)
            if c and cand in c:
                return idx
    return None


def find_statement_header(ws, max_scan=15):
    """카드사 엑셀에서 '이용일자·승인금액' 같은 헤더가 있는 행을 찾는다.

    카드사 엑셀은 표 위에 안내문구가 몇 줄 섞여 있는 경우가 많아서, 1행이
    무조건 헤더라고 가정하지 않고 위에서부터 몇 줄을 훑어서 날짜/금액 헤더가
    둘 다 있는 행을 찾는다.
    """
    for r in range(1, max_scan + 1):
        row = next(ws.iter_rows(min_row=r, max_row=r, values_only=True), None)
        if row is None:
            break
        cells = list(row)
        date_col = _match_col(cells, _DATE_HEADERS)
        amount_col = _match_col(cells, _AMOUNT_HEADERS)
        if date_col is not None and amount_col is not None:
            return {
                "header_row": r,
                "date_col": date_col,
                "amount_col": amount_col,
                "merchant_col": _match_col(cells, _MERCHANT_HEADERS),
                "cardno_col": _match_col(cells, _CARDNO_HEADERS),
            }
    return None


def extract_last4(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-4:] if len(digits) >= 4 else ""


def parse_amount_cell(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace(",", "").replace("원", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_date_cell(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    s = str(value).strip()
    if not s:
        return None
    s_norm = s.replace(".", "-").replace("/", "-").strip("-")
    for fmt in ("%Y-%m-%d", "%y-%m-%d"):
        try:
            return datetime.strptime(s_norm, fmt).date().isoformat()
        except ValueError:
            pass

    digits = re.sub(r"\D", "", s)
    if len(digits) == 8:
        try:
            return datetime.strptime(digits, "%Y%m%d").date().isoformat()
        except ValueError:
            return None
    return None


def _safe_date(year: int, month: int, day: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last_day))


def cycle_bounds(reset_day: int, today: date = None):
    """오늘이 속한 혜택 사이클의 [시작일, 종료일) 을 돌려준다."""
    today = today or date.today()
    reset_day = max(1, min(31, int(reset_day or 1)))
    this_month_reset = _safe_date(today.year, today.month, reset_day)

    if today >= this_month_reset:
        start = this_month_reset
        ny, nm = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        end = _safe_date(ny, nm, reset_day)
    else:
        py, pm = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        start = _safe_date(py, pm, reset_day)
        end = this_month_reset

    return start, end
