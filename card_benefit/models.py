# -*- coding: utf-8 -*-
"""SQLite 스키마 + 혜택 사이클(초기화 주기) 계산.

카드 하나(cards)는 여러 혜택 항목(benefits)을 가지고, 각 혜택 항목은
사용할 때마다 사용내역(usage_logs)이 쌓인다. 혜택은 카드마다 정한
"초기화 기준일"(reset_day, 예: 매월 1일 / 매월 15일 결제일 등)마다
사용량이 새로 리셋된다고 보고, 이번 사이클 동안 쓴 금액을 한도에서 빼서
남은 혜택을 계산한다.
"""
import calendar
import json
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
    perf_threshold REAL NOT NULL DEFAULT 0,   -- 전월실적 기준금액(원). 0이면 조건 없음
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
    merchant_keywords TEXT DEFAULT '',   -- 쉼표로 구분된 가맹점명 키워드 - 매칭되면 배정 화면에서 이 혜택을 자동 선택
    tier_table TEXT DEFAULT '',          -- [[전월실적기준금액, 한도], ...] JSON - 있으면 카드의 이번달 실적에 따라 한도 자동 계산
    calc_mode TEXT NOT NULL DEFAULT 'raw',   -- 'raw'(입력값 그대로) 또는 'change_under_1000'(결제금액 입력 → 1000원 미만 자투리 자동계산, 더모아형)
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS usage_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    benefit_id INTEGER NOT NULL REFERENCES benefits(id) ON DELETE CASCADE,
    used_value REAL NOT NULL DEFAULT 0,
    used_at TEXT NOT NULL,          -- 'YYYY-MM-DD'
    merchant TEXT DEFAULT '',       -- 당일 동일가맹점 중복 적립 체크용
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

-- 카드별 월(전월실적 계산용) 총 사용액. 카드사 "전월실적"은 보통 혜택과
-- 무관하게 그 달에 쓴 전체 금액을 기준으로 하므로, 특정 혜택에 묶이지 않는
-- 별도의 월별 합계로 관리한다.
CREATE TABLE IF NOT EXISTS performance (
    card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    year_month TEXT NOT NULL,        -- 'YYYY-MM'
    total_spend REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (card_id, year_month)
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
        _ensure_column(conn, "cards", "perf_threshold", "perf_threshold REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "benefits", "merchant_keywords", "merchant_keywords TEXT DEFAULT ''")
        _ensure_column(conn, "benefits", "tier_table", "tier_table TEXT DEFAULT ''")
        _ensure_column(conn, "benefits", "calc_mode", "calc_mode TEXT NOT NULL DEFAULT 'raw'")
        _ensure_column(conn, "usage_logs", "merchant", "merchant TEXT DEFAULT ''")
        conn.commit()
    finally:
        conn.close()


def current_year_month(today: date = None) -> str:
    today = today or date.today()
    return f"{today.year:04d}-{today.month:02d}"


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

    return {"amount": amount, "last4": last4, "issuer": issuer, "merchant": annotate_merchant(merchant)}


# 카드 이용내역서의 "가맹점명"은 실제 서비스명이 아니라 결제대행(PG)/운영사
# 법인명으로 찍히는 경우가 흔하다(예: 배달의민족 결제가 "(주)우아한형제들"로
# 표시). 확실히 확인된 것만 최소한으로 매핑해서 옆에 실제 서비스명을 덧붙인다
# - 잘못된 매핑을 넣느니 모르는 상호는 그대로 두는 쪽이 안전하다.
_MERCHANT_ALIASES = {
    "비바리퍼블리카": "토스",
    "우아한형제들": "배달의민족",
}


def compute_change_earned(payment_amount: float, doubled: bool = False) -> float:
    """더모아형 "1,000원 미만 자투리(잔돈)" 적립액을 계산한다.

    건당 5,000원 이상 결제에서만 적립되고, 1,000원 미만 나머지 금액이
    포인트로 적립된다(특별가맹점은 2배). 예: 5,900원 결제 → 900원 적립.
    """
    payment_amount = payment_amount or 0
    if payment_amount < 5000:
        return 0
    remainder = payment_amount % 1000
    return remainder * (2 if doubled else 1)


def tier_limit_for_spend(tier_table_json: str, spend: float):
    """[[기준금액, 한도], ...] JSON과 이번달 실적(spend)을 받아서 해당하는 한도를
    돌려준다. 기준금액을 만족하는 구간 중 가장 높은 걸 적용(구간 오름차순
    가정 안 함 - 정렬 후 계산). tier_table_json이 비어있거나 파싱 실패하면 None.
    """
    if not tier_table_json:
        return None
    try:
        tiers = json.loads(tier_table_json)
    except (ValueError, TypeError):
        return None
    if not isinstance(tiers, list) or not tiers:
        return None

    applicable = 0
    for threshold, limit in sorted(tiers, key=lambda t: t[0]):
        if spend >= threshold:
            applicable = limit
    return applicable


def match_benefit_keyword(merchant: str, merchant_keywords: str) -> bool:
    """가맹점명이 혜택에 등록된 자동매칭 키워드 중 하나라도 포함하는지 확인."""
    if not merchant or not merchant_keywords:
        return False
    merchant_lower = merchant.lower()
    keywords = [k.strip().lower() for k in merchant_keywords.split(",") if k.strip()]
    return any(k in merchant_lower for k in keywords)


def annotate_merchant(name: str) -> str:
    if not name:
        return name
    for key, brand in _MERCHANT_ALIASES.items():
        if key in name:
            return f"{name} ({brand})"
    return name


# ---- 카드사에서 다운로드한 엑셀(이용내역서) 자동인식 ----
# 카드사마다 열 이름이 제각각이라, 흔히 쓰이는 헤더 표현들을 넓게 잡아서
# "이 표에서 어느 열이 날짜/금액/가맹점/카드번호인지" 추측한다.

_DATE_HEADERS = ["이용일자", "이용일", "승인일자", "거래일자", "거래일", "일자", "날짜", "결제일자", "결제일"]
_AMOUNT_HEADERS = ["이용금액", "승인금액", "결제금액", "청구금액", "이용대금", "금액"]
_MERCHANT_HEADERS = ["가맹점명", "가맹점", "사용처", "이용가맹점", "적요", "내용", "상호"]
_CARDNO_HEADERS = ["카드번호", "카드no", "카드 no", "이용카드", "카드"]
_CANCEL_STATUS_HEADERS = ["취소상태", "매입구분", "승인상태", "거래상태", "상태"]
_CANCEL_KEYWORDS = ["취소", "실패", "거절"]


def _norm_cell(cell):
    return str(cell).strip() if cell is not None else ""


def _match_col(header_cells, candidates):
    normed = [_norm_cell(c) for c in header_cells]

    # 1) 정확히 일치하는 헤더를 최우선으로 찾는다. 부분 일치만으로 고르면
    # "해외이용금액"처럼 다른 열 이름 안에 "이용금액"이 우연히 들어있는
    # 경우에 엉뚱한 열(진짜 "금액" 열이 아니라 "해외이용금액")을 집어버리는
    # 문제가 있어서, 정확히 같은 이름의 열을 항상 부분 일치보다 우선한다.
    for cand in candidates:
        for idx, c in enumerate(normed):
            if c == cand:
                return idx

    # 2) 정확히 일치하는 열이 없으면 부분 일치로 찾는다 (구체적인 표현부터).
    for cand in candidates:
        for idx, c in enumerate(normed):
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
                "status_col": _match_col(cells, _CANCEL_STATUS_HEADERS),
            }
    return None


def is_cancelled_status(value):
    text = _norm_cell(value)
    return bool(text) and any(k in text for k in _CANCEL_KEYWORDS)


def extract_last4(value):
    """카드번호(또는 마스킹된 카드번호) 셀에서 끝자리 식별코드를 뽑는다.

    카드사마다 실적표엔 뒤 4자리를 다 보여주기도 하고("1234-56**-****-8390"),
    일부만 보여주기도 한다(예: "본인839*"처럼 마지막 한 자리가 마스킹).
    그래서 최소 3자리만 있어도 인정하고, 몇 자리인지가 다른 경우의 비교는
    card_codes_match()에서 접두(prefix) 매칭으로 처리한다.
    """
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < 3:
        return ""
    return digits[-4:] if len(digits) >= 4 else digits[-3:]


def card_codes_match(a, b):
    """두 카드 식별코드가 같은 카드를 가리키는지 비교한다.

    한쪽이 다른 쪽의 접두(prefix)면 같은 카드로 본다 - 마스킹 방식에 따라
    자릿수가 다를 수 있어서다(예: 카드 등록 시 "839", 알림에서는 "8390").
    """
    if not a or not b:
        return False
    return a == b or a.startswith(b) or b.startswith(a)


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

    # "2026.08.15 23:11"처럼 날짜 뒤에 시간이 붙어있는 경우 날짜 부분만 쓴다.
    date_part = s.split()[0] if s.split() else s
    s_norm = date_part.replace(".", "-").replace("/", "-").strip("-")
    for fmt in ("%Y-%m-%d", "%y-%m-%d"):
        try:
            return datetime.strptime(s_norm, fmt).date().isoformat()
        except ValueError:
            pass

    digits = re.sub(r"\D", "", date_part)
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
