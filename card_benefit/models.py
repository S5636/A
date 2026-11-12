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
    short_name TEXT DEFAULT '',   -- 상단 요약줄(점프 버튼)에 쓸 짧은 이름. 비어있으면 자동 생성
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
    calc_mode TEXT NOT NULL DEFAULT 'raw',   -- 'raw' | 'change_under_1000'(더모아형) | 'percent_discount'(결제금액 입력 → 할인율 자동계산)
    discount_percent REAL NOT NULL DEFAULT 0,   -- percent_discount용 할인율(%)
    per_txn_cap REAL NOT NULL DEFAULT 0,        -- percent_discount용 건당 할인적용 대상 결제금액 한도(0=한도 없음)
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
    source_inbox_id INTEGER DEFAULT NULL,  -- 받은 알림에서 배정되어 만들어진 기록이면 그 알림 id
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
    status TEXT NOT NULL DEFAULT 'pending',   -- 'pending' | 'assigned' | 'no_benefit'
    card_id INTEGER REFERENCES cards(id) ON DELETE SET NULL,
    benefit_id INTEGER REFERENCES benefits(id) ON DELETE SET NULL,
    recurring_id INTEGER REFERENCES recurring_payments(id) ON DELETE SET NULL,  -- 고정지출과 매칭됐으면
    insurance_id INTEGER REFERENCES insurances(id) ON DELETE SET NULL,  -- 보험료와 매칭됐으면
    medical_claimed INTEGER NOT NULL DEFAULT 0,  -- 병원비/약국 결제를 실손 등으로 청구했는지
    medical_excluded INTEGER NOT NULL DEFAULT 0,  -- 병원비 목록에서 "이건 아님" 처리한 건 다시 안 보여줌
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

-- 대출 (자산 전체 관리의 첫 항목). 상환 기록은 "상환 후 잔액"을 그대로
-- 입력받아 current_balance를 갱신하는 방식으로 관리한다(사용자가 직접
-- 얼마 갚았는지 계산할 필요 없이, 명세서에 찍힌 "남은 잔액"만 그대로 넣으면 됨).
CREATE TABLE IF NOT EXISTS loans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    loan_type TEXT DEFAULT '',          -- 신용대출/담보대출/학자금대출/카드론 등
    principal REAL NOT NULL DEFAULT 0,       -- 최초 대출원금
    current_balance REAL NOT NULL DEFAULT 0, -- 현재 남은 원금
    interest_rate REAL NOT NULL DEFAULT 0,   -- 연 금리(%)
    repayment_type TEXT DEFAULT '',     -- 원리금균등/원금균등/만기일시상환 등
    start_date TEXT DEFAULT '',
    end_date TEXT DEFAULT '',           -- 만기일
    payment_day INTEGER NOT NULL DEFAULT 0,  -- 매월(또는 해당 월) 상환일(1~31, 0=없음)
    monthly_payment REAL NOT NULL DEFAULT 0,  -- 상환 시 실제로 내는 금액(원금+이자, 직접 입력 - 상환방식별 자동계산은 안 함)
    payment_months TEXT DEFAULT '',  -- 매달이 아니라 특정 달에만 내면 '1,4,7,10'처럼 쉼표구분 월 목록. 비어있으면 매달.
    withdrawal_account TEXT DEFAULT '',  -- 대출금이 빠져나가는 계좌/카드 (뒤 4자리 포함해서 표기)
    paid_check_month TEXT DEFAULT '',  -- 이번 달 상환 체크한 'YYYY-MM' (매달 자동으로 초기화됨)
    repayment_start_date TEXT DEFAULT '',  -- 거치식(거치기간 후 원금균등 등) 대출의 실제 원금상환 시작일. 모르면 빈 값(추측 안 함)
    memo TEXT DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS loan_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_id INTEGER NOT NULL REFERENCES loans(id) ON DELETE CASCADE,
    paid_at TEXT NOT NULL,              -- 'YYYY-MM-DD'
    amount REAL NOT NULL DEFAULT 0,     -- 이번에 줄어든 금액(상환 전 잔액 - 상환 후 잔액). 잔액이 늘었으면 음수.
    balance_after REAL NOT NULL DEFAULT 0,  -- 이 상환 후 남은 잔액
    memo TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 구독/자동이체 등 매달 고정으로 나가는 지출. 대출처럼 잔액 추적이 필요 없고
-- "뭐가, 얼마씩, 뭘로, 며칠에 나가는지"만 등록해두고 한 달 합계를 보는 용도.
CREATE TABLE IF NOT EXISTS recurring_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT DEFAULT '',           -- 구독/자동이체/기타 등 자유 입력
    amount REAL NOT NULL DEFAULT 0,
    payment_method TEXT DEFAULT '',     -- 카드가 아닌 결제수단('현금', '국민은행 자동이체' 등) 또는
                                         -- card_id가 없는 옛날 데이터의 카드 표기(과거 호환용)
    card_id INTEGER REFERENCES cards(id) ON DELETE SET NULL,  -- 등록된 카드로 낼 때는 여기로 연결
                                         -- (카드탭에서 이름을 바꿔도 자동으로 같이 바뀜)
    payment_day INTEGER NOT NULL DEFAULT 0,  -- 매월 며칠(1~31, 0=미정)
    billing_cycle TEXT DEFAULT '월간',   -- 구독 결제 주기('월간'/'연간')
    active INTEGER NOT NULL DEFAULT 1,  -- 0이면 해지/중단했지만 기록은 남겨둔 것
    memo TEXT DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 보험(매달 내는 보험료 + 가입 내용). 고정지출과 구조는 비슷하지만 피보험자/계약일/
-- 만기일처럼 보험 고유 정보가 있어서 별도 표로 관리한다.
CREATE TABLE IF NOT EXISTS insurances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL DEFAULT '',        -- 보험사
    product_name TEXT NOT NULL DEFAULT '',   -- 상품명
    insured_name TEXT DEFAULT '본인',         -- 피보험자
    monthly_premium REAL NOT NULL DEFAULT 0,
    payment_day INTEGER NOT NULL DEFAULT 0,  -- 매월 며칠 출금(1~31, 0=미정)
    payment_method TEXT DEFAULT '',          -- 카드가 아닌 결제수단 또는 과거 호환용 표기
    card_id INTEGER REFERENCES cards(id) ON DELETE SET NULL,  -- 등록된 카드로 낼 때는 여기로 연결
    start_date TEXT DEFAULT '',              -- 계약일
    payment_end_date TEXT DEFAULT '',        -- 납입완료일
    maturity_date TEXT DEFAULT '',           -- 보험만기일(날짜 또는 '종신' 등 텍스트 그대로)
    current_balance REAL NOT NULL DEFAULT 0,  -- 적금/적립식 예금처럼 같이 관리할 때: 현재까지 적립된 금액(원금 기준). 순수 보험은 0으로 둠
    interest_rate REAL NOT NULL DEFAULT 0,    -- 적금/적립식 예금의 적용이율(연%). 순수 보험은 0으로 둠
    active INTEGER NOT NULL DEFAULT 1,       -- 0이면 해지
    memo TEXT DEFAULT '',                    -- 보장내용/약관/증권번호 등 자유 기록
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 주식 보유 종목. 매수가 대비 평가손익이 마이너스에서 플러스로 바뀌는
-- 순간을 잡아서 윈도우 토스트 알림을 보내기 위함(백그라운드 스레드가
-- 주기적으로 네이버 금융 비공식 시세 API로 현재가를 갱신함). KRX 종목
-- 코드(6자리 숫자)만 지원 - 해외주식은 아직 안 됨.
CREATE TABLE IF NOT EXISTS stocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL DEFAULT '',            -- 종목명
    code TEXT NOT NULL DEFAULT '',            -- 종목코드(KRX 6자리)
    buy_price REAL NOT NULL DEFAULT 0,        -- 매수 평균단가
    quantity REAL NOT NULL DEFAULT 0,         -- 보유 수량
    last_price REAL NOT NULL DEFAULT 0,       -- 마지막으로 확인된 현재가(백그라운드 갱신)
    last_checked_at TEXT DEFAULT '',          -- 마지막 시세 확인 시각
    last_known_positive INTEGER NOT NULL DEFAULT 0,  -- 마지막으로 확인한 평가손익 부호(전환 감지용)
    broker TEXT DEFAULT '',                   -- 어느 증권사 계좌인지(예: 대신증권)
    sell_price REAL NOT NULL DEFAULT 0,       -- 매도가(매도 후엔 시세 대신 이 가격으로 손익 고정)
    active INTEGER NOT NULL DEFAULT 1,
    memo TEXT DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
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
        _ensure_column(conn, "cards", "short_name", "short_name TEXT DEFAULT ''")
        _ensure_column(conn, "cards", "last4", "last4 TEXT DEFAULT ''")
        _ensure_column(conn, "cards", "perf_threshold", "perf_threshold REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "cards", "perf_excluded_keywords", "perf_excluded_keywords TEXT DEFAULT ''")
        _ensure_column(conn, "benefits", "merchant_keywords", "merchant_keywords TEXT DEFAULT ''")
        _ensure_column(conn, "benefits", "tier_table", "tier_table TEXT DEFAULT ''")
        _ensure_column(conn, "benefits", "calc_mode", "calc_mode TEXT NOT NULL DEFAULT 'raw'")
        _ensure_column(conn, "usage_logs", "merchant", "merchant TEXT DEFAULT ''")
        _ensure_column(conn, "usage_logs", "source_inbox_id", "source_inbox_id INTEGER DEFAULT NULL")
        _ensure_column(conn, "benefits", "discount_percent", "discount_percent REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "benefits", "per_txn_cap", "per_txn_cap REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "benefits", "rate_table", "rate_table TEXT DEFAULT ''")
        _ensure_column(conn, "usage_logs", "rate_category", "rate_category TEXT DEFAULT ''")
        _ensure_column(conn, "benefits", "always_doubled", "always_doubled INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "benefits", "memo_edited_by_user", "memo_edited_by_user INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "benefits", "percent_tier_table", "percent_tier_table TEXT DEFAULT ''")
        # 혜택 전체에 거는 시간대 조건(업종별 요율표 없이 단일 할인율인 혜택용 -
        # 예: LADY CLASSIC "브런치 할인"은 오전 11시~오후 2시만 적용). 업종별로
        # 시간대가 다르면(Time Plan DAY/NIGHT처럼) rate_table의 entry[6]/[7]을 쓴다.
        _ensure_column(conn, "benefits", "start_hour", "start_hour INTEGER")
        _ensure_column(conn, "benefits", "end_hour", "end_hour INTEGER")
        # used_value는 "실제로 한도에서 깎인 금액"(할인/적립액)이라 결제금액 자체와
        # 다르다(예: 10% 할인이면 결제 10만원 → used_value 1만원). 결제금액 자체의
        # 합계를 따로 보여주려면 변환 전 원래 입력값도 같이 저장해둬야 한다.
        _ensure_column(conn, "usage_logs", "raw_value", "raw_value REAL DEFAULT NULL")
        _ensure_column(conn, "inbox_items", "approval_no", "approval_no TEXT DEFAULT ''")
        _ensure_column(conn, "inbox_items", "usage_type", "usage_type TEXT DEFAULT ''")
        _ensure_column(conn, "inbox_items", "source", "source TEXT DEFAULT ''")
        _ensure_column(conn, "loans", "monthly_payment", "monthly_payment REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "loans", "payment_months", "payment_months TEXT DEFAULT ''")
        _ensure_column(conn, "loans", "withdrawal_account", "withdrawal_account TEXT DEFAULT ''")
        _ensure_column(conn, "loans", "paid_check_month", "paid_check_month TEXT DEFAULT ''")
        _ensure_column(conn, "inbox_items", "recurring_id", "recurring_id INTEGER DEFAULT NULL")
        _ensure_column(conn, "inbox_items", "insurance_id", "insurance_id INTEGER DEFAULT NULL")
        _ensure_column(conn, "inbox_items", "medical_claimed", "medical_claimed INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "inbox_items", "medical_excluded", "medical_excluded INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "recurring_payments", "card_id", "card_id INTEGER DEFAULT NULL")
        _ensure_column(conn, "insurances", "card_id", "card_id INTEGER DEFAULT NULL")
        _ensure_column(conn, "recurring_payments", "billing_alias", "billing_alias TEXT DEFAULT ''")
        _ensure_column(conn, "insurances", "billing_alias", "billing_alias TEXT DEFAULT ''")
        _ensure_column(
            conn, "inbox_items", "automatch_excluded", "automatch_excluded INTEGER NOT NULL DEFAULT 0"
        )
        _ensure_column(conn, "recurring_payments", "excluded_merchants", "excluded_merchants TEXT DEFAULT ''")
        _ensure_column(conn, "insurances", "excluded_merchants", "excluded_merchants TEXT DEFAULT ''")
        _ensure_column(conn, "inbox_items", "dup_ignored", "dup_ignored INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "benefits", "plan_day_double", "plan_day_double INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "loans", "repayment_start_date", "repayment_start_date TEXT DEFAULT ''")
        _ensure_column(conn, "insurances", "current_balance", "current_balance REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "insurances", "interest_rate", "interest_rate REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "stocks", "broker", "broker TEXT DEFAULT ''")
        _ensure_column(conn, "stocks", "sell_price", "sell_price REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "recurring_payments", "billing_cycle", "billing_cycle TEXT DEFAULT '월간'")

        # recurring_id/insurance_id는 _ensure_column으로 나중에 추가된 컬럼이라
        # (예전 DB엔 처음부터 있던 게 아니라서) ON DELETE SET NULL 외래키 제약이
        # 안 걸려있다 - ALTER TABLE ADD COLUMN으로는 외래키를 못 붙인다. 그래서
        # 고정지출/보험 항목을 지우면, 그걸 가리키던 알림들의 recurring_id/
        # insurance_id가 안 지워지고 존재하지 않는 id로 그대로 남는다(고아
        # 참조). 이러면 그 알림은 recurring_id IS NOT NULL이라 다른 어떤 것과도
        # 다시 매칭이 안 되는데, 화면 어디에도 안 보여서(참조하는 행 자체가
        # 없으니) 티도 안 나고 영원히 막혀있게 된다. 매번 시작할 때 이런
        # 고아 참조를 찾아서 지워준다.
        conn.execute(
            "UPDATE inbox_items SET recurring_id = NULL "
            "WHERE recurring_id IS NOT NULL AND recurring_id NOT IN (SELECT id FROM recurring_payments)"
        )
        conn.execute(
            "UPDATE inbox_items SET insurance_id = NULL "
            "WHERE insurance_id IS NOT NULL AND insurance_id NOT IN (SELECT id FROM insurances)"
        )
        conn.commit()
    finally:
        conn.close()


def current_year_month(today: date = None) -> str:
    today = today or date.today()
    return f"{today.year:04d}-{today.month:02d}"


def previous_year_month(today: date = None) -> str:
    today = today or date.today()
    y, m = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    return f"{y:04d}-{m:02d}"


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
_LAST4_RE = re.compile(r"\(([\d*]{4})\)")
_ISSUER_HINTS = [
    "신한카드", "KB국민카드", "국민카드", "삼성카드", "현대카드", "롯데카드", "하나카드",
    "우리카드", "NH농협카드", "농협카드", "비씨카드", "BC카드", "씨티카드",
    "카카오뱅크", "토스뱅크", "케이뱅크",
]
_NOISE_KEYWORDS = ["승인", "일시불", "할부", "카드", "누적", "이용", "잔액", "한도", "포인트"]
_MERCHANT_LABEL_RE = re.compile(r"^가맹점(?:명)?\s*[:：]\s*(.+)$")


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

    lines = [ln.strip(" -\t") for ln in text.splitlines() if ln.strip()]

    # 1순위: "가맹점명: OOO"처럼 라벨이 붙은 줄이 있으면 그 값을 그대로 쓴다 -
    # 노이즈 키워드 필터를 여기엔 적용하면 안 된다. 상호명 자체에 "포인트"/"카드"
    # 같은 단어가 들어있으면(예: "포인트병원") 노이즈 필터에 걸려서 진짜 가맹점명
    # 줄까지 통째로 버려지기 때문이다.
    merchant = ""
    for line in lines:
        m = _MERCHANT_LABEL_RE.match(line)
        if m:
            merchant = m.group(1).strip()
            break

    # 2순위: 라벨이 없는 알림 형식이면, 노이즈 키워드가 없는 마지막 줄을 추정한다.
    if not merchant:
        for line in reversed(lines):
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
    # 알림 문구가 "주식회사 우아한형제"처럼 끝 글자가 잘려서 오는 경우가 있어서
    # "들" 없이도 잡히게 짧게 잡는다.
    "우아한형제": "배달의민족",
    "anthropic": "Claude 구독",
    "aliexpress": "알리익스프레스",
}

# 카드마다 "전월(전년도) 이용금액 제외 대상"이 안내장 기준으로 서로
# 다르다(예: Discount Plan+는 지방세/공과금/상품권까지 폭넓게 제외되지만,
# The LADY CLASSIC은 학원·Gift카드·현금서비스·카드론·수수료이자·취소금액
# 정도만 제외됨). 그래서 카드별 제외 키워드는 cards.perf_excluded_keywords에
# 저장해두고 그걸 기준으로 판단한다 - 카드 종류를 몰라서 아직 안 채워진
# 카드는 모든 카드사·상품이 공통으로 제외하는 최소 항목만 기본값으로 쓴다.
DEFAULT_PERFORMANCE_EXCLUDED_KEYWORDS = "현금서비스,카드론,연회비,수수료,이자,거래취소"


def is_performance_excluded(merchant: str, excluded_keywords: str = "") -> bool:
    if not merchant:
        return False
    keywords = [k.strip() for k in (excluded_keywords or DEFAULT_PERFORMANCE_EXCLUDED_KEYWORDS).split(",") if k.strip()]
    return any(k in merchant for k in keywords)


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


def compute_percent_discount(payment_amount: float, percent: float, per_txn_cap: float = 0) -> float:
    """결제금액을 넣으면 실제 "받은 할인/캐시백 금액"을 계산한다.

    카드사 안내장의 "할인받기 전 이용금액 기준 1회 X까지 할인" 문구는
    결제금액 중 X까지만 할인율이 적용된다는 뜻이라, 할인액 = min(결제금액, 한도) × 할인율.
    """
    payment_amount = payment_amount or 0
    percent = percent or 0
    base = payment_amount
    if per_txn_cap and per_txn_cap > 0:
        base = min(base, per_txn_cap)
    # round()가 아니라 버림(절삭)이어야 한다 - 실제 카드사 적립내역과 대조해보니
    # (예: 8,300원×0.5% = 41.5 → 실제 적립 41P, 15,530원×0.5% = 77.65 → 실제
    # 77P) round면 42/78로 1P 더 쳐서 실제보다 과대계산됨. 소수점 이하는 그냥 버림.
    return int(base * percent / 100)


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
    """가맹점명이 혜택에 등록된 자동매칭 키워드 중 하나라도 포함하는지 확인.

    카드사 알림은 한글 발음과 영문 약어를 괄호로 묶어서 같이 찍는 경우가
    많다("지에스(GS)25 중산고봉점"). 괄호를 그대로 두고 비교하면 "GS25"
    키워드가 "(GS)25"에서 괄호에 걸려 끊겨서 매칭이 안 된다. 괄호만 지우고
    (안의 글자는 그대로 남겨서) 비교하면 이런 경우도 잡힌다.

    키워드 앞에 "-"를 붙이면 제외 키워드다("이마트"는 매칭시키고 싶지만
    "이마트24"는 완전히 다른 업종(편의점)이라 빼고 싶을 때 "-이마트24"로
    등록) - 제외 키워드 중 하나라도 가맹점명에 들어있으면, 다른 키워드가
    맞아도 매칭시키지 않는다."""
    if not merchant or not merchant_keywords:
        return False
    merchant_lower = re.sub(r"[()]", "", merchant.lower())
    positive, negative = [], []
    for k in merchant_keywords.split(","):
        k = k.strip().lower()
        if not k:
            continue
        (negative if k.startswith("-") else positive).append(k[1:] if k.startswith("-") else k)
    if any(k in merchant_lower for k in negative):
        return False
    return any(k in merchant_lower for k in positive)


_MEDICAL_KEYWORDS = [
    "병원", "의원", "클리닉", "한의원", "한방", "치과", "약국", "정형외과",
    "피부과", "이비인후과", "소아과", "산부인과", "안과", "내과", "외과",
    "비뇨기과", "정신건강의학과", "신경과", "재활의학과", "성형외과",
]


def is_medical_merchant(merchant: str) -> bool:
    """가맹점명으로 병원/약국 등 의료기관 결제인지 대략 판단한다(실손 청구 체크용).
    완벽한 판별은 불가능하니 흔한 명칭 키워드로 넓게 잡고, 아니면 사용자가
    목록에서 직접 걸러낸다."""
    if not merchant:
        return False
    merchant_lower = merchant.lower()
    return any(k in merchant for k in _MEDICAL_KEYWORDS) or "pharmacy" in merchant_lower


TIME_BLOCKED_LABEL = "__TIME_BLOCKED__"


def match_rate_table(merchant: str, rate_table_json: str, default_percent: float = 0, default_cap: float = 0, hour=None):
    """가맹점명과 rate_table(JSON [["라벨:키워드1,키워드2", 할인율], ...] 또는
    [[..., 할인율, 건당한도], ...] 또는 [[..., 할인율, 건당한도, 일한도, 월한도], ...] 또는
    [[..., 할인율, 건당한도, 일한도, 월한도, 업종별월적립한도], ...] 또는
    [[..., ..., 업종별월적립한도, 시작시, 끝시], ...])을 받아서 해당 업종의
    (할인율, 건당한도, 일 횟수한도, 월 횟수한도, 업종별 월 적립(포인트) 한도, 라벨)을 돌려준다.
    첫 값에 "쇼핑:이마트,쿠팡"처럼 ":"로 라벨을 붙이면 그 라벨이 화면 표시/한도 구분에
    쓰이고, ":"가 없으면 키워드 문자열 자체가 라벨로 쓰인다. Daily Plan/Monthly Plan처럼
    하나의 혜택 안에서 업종별로 할인율(과 한도)이 다른 경우에 쓴다. 마지막 값(업종별 월
    적립한도)은 "이 업종은 이번 달 최대 1만 포인트까지만 적립"처럼 횟수가 아니라 적립액
    자체를 업종별로 깎는 카드(씨티 리워드 등)에 쓴다 - 횟수 한도와는 별개로 같이 걸 수 있다.
    일곱·여덟 번째 값(시작시/끝시, 0~23)은 Time Plan의 DAY/NIGHT처럼 "이 시간대
    결제만" 적용되는 업종에 쓴다 - hour(결제 시각의 시, 0~23)를 넘기면 그 시간대
    밖일 때 TIME_BLOCKED_LABEL을 라벨로 돌려줘서(할인율 0) 호출하는 쪽이 "이
    시간대는 할인 대상이 아님"을 구분해서 안내할 수 있게 한다.
    매칭 안 되면 (default_percent, default_cap, 0, 0, 0, None).
    """
    if rate_table_json and merchant:
        try:
            rows = json.loads(rate_table_json)
        except (ValueError, TypeError):
            rows = None
        if isinstance(rows, list):
            merchant_lower = merchant.lower()
            for entry in rows:
                if not isinstance(entry, (list, tuple)) or len(entry) not in (2, 3, 4, 5, 6, 7, 8):
                    continue
                raw_keywords = str(entry[0])
                if ":" in raw_keywords:
                    label, kw_part = raw_keywords.split(":", 1)
                else:
                    label, kw_part = raw_keywords, raw_keywords
                percent = entry[1]
                cap = entry[2] if len(entry) >= 3 else default_cap
                daily_limit = entry[3] if len(entry) >= 4 else 0
                monthly_limit = entry[4] if len(entry) >= 5 else 0
                monthly_point_cap = entry[5] if len(entry) >= 6 else 0
                start_hour = entry[6] if len(entry) >= 7 else None
                end_hour = entry[7] if len(entry) >= 8 else None
                pos_kws, neg_kws = [], []
                for kw in kw_part.split(","):
                    kw = kw.strip().lower()
                    if not kw:
                        continue
                    (neg_kws if kw.startswith("-") else pos_kws).append(kw[1:] if kw.startswith("-") else kw)
                if any(kw in merchant_lower for kw in neg_kws):
                    continue
                if any(kw in merchant_lower for kw in pos_kws):
                    if start_hour is not None and end_hour is not None and hour is not None:
                        if not (start_hour <= hour < end_hour):
                            return 0, 0, 0, 0, 0, TIME_BLOCKED_LABEL
                    return percent, cap, daily_limit, monthly_limit, monthly_point_cap, label
    return default_percent, default_cap, 0, 0, 0, None


def parse_rate_table_categories(rate_table_json: str):
    """rate_table JSON에 정의된 업종(카테고리) 전체를 [{"label","percent","daily_limit",
    "monthly_limit","monthly_point_cap"}, ...] 목록으로 돌려준다. 가맹점 매칭과 무관하게,
    혜택 카드에 "쇼핑 2/5회 · 이동 1/5회"처럼 업종별 사용 현황을 보여줄 때 쓴다.
    """
    if not rate_table_json:
        return []
    try:
        rows = json.loads(rate_table_json)
    except (ValueError, TypeError):
        return []
    if not isinstance(rows, list):
        return []

    categories = []
    for entry in rows:
        if not isinstance(entry, (list, tuple)) or len(entry) not in (2, 3, 4, 5, 6, 7, 8):
            continue
        raw_keywords = str(entry[0])
        label = raw_keywords.split(":", 1)[0] if ":" in raw_keywords else raw_keywords
        percent = entry[1]
        cap = entry[2] if len(entry) >= 3 else 0
        daily_limit = entry[3] if len(entry) >= 4 else 0
        monthly_limit = entry[4] if len(entry) >= 5 else 0
        monthly_point_cap = entry[5] if len(entry) >= 6 else 0
        start_hour = entry[6] if len(entry) >= 7 else None
        end_hour = entry[7] if len(entry) >= 8 else None
        categories.append({
            "label": label,
            "percent": percent,
            "cap": cap,
            "daily_limit": daily_limit,
            "monthly_limit": monthly_limit,
            "monthly_point_cap": monthly_point_cap,
            "start_hour": start_hour,
            "end_hour": end_hour,
        })
    return categories


def rate_table_entry_by_label(rate_table_json: str, label: str, default_percent: float = 0, default_cap: float = 0, hour=None):
    """사용자가 가맹점 자동인식 대신 업종을 직접 골랐을 때 쓴다. label과 정확히
    일치하는 rate_table 항목의 (할인율, 건당한도, 일한도, 월한도, 업종별월적립한도,
    시간대밖여부)를 돌려준다. 그 업종에 시간대 조건(start_hour/end_hour)이 있고
    hour가 그 밖이면 마지막 값을 True로 돌려준다 - 업종을 사람이 직접 골랐어도
    실제 카드사 할인은 시간대를 가리므로 그대로 적용해야 한다."""
    for cat in parse_rate_table_categories(rate_table_json):
        if cat["label"] == label:
            if (
                cat["start_hour"] is not None and cat["end_hour"] is not None and hour is not None
                and not (cat["start_hour"] <= hour < cat["end_hour"])
            ):
                return 0, 0, cat["daily_limit"], cat["monthly_limit"], cat["monthly_point_cap"], True
            cap = cat["cap"] or default_cap
            return cat["percent"], cap, cat["daily_limit"], cat["monthly_limit"], cat["monthly_point_cap"], False
    return default_percent, default_cap, 0, 0, 0, False


def annotate_merchant(name: str) -> str:
    if not name:
        return name
    # 카드사 엑셀에 "LG  U+통신요금  즉시결제"처럼 단어 사이 공백이 여러 개
    # 섞여 나오는 경우가 있어서, 가맹점 키워드 매칭("LG U+")이 깨지지
    # 않도록 저장 시점에 공백을 하나로 정리한다.
    name = re.sub(r"\s+", " ", str(name).strip())
    name_lower = name.lower()
    for key, brand in _MERCHANT_ALIASES.items():
        if key.lower() in name_lower:
            return f"{name} ({brand})"
    return name


# ---- 카드사에서 다운로드한 엑셀(이용내역서) 자동인식 ----
# 카드사마다 열 이름이 제각각이라, 흔히 쓰이는 헤더 표현들을 넓게 잡아서
# "이 표에서 어느 열이 날짜/금액/가맹점/카드번호인지" 추측한다.

_DATE_HEADERS = [
    "이용일시", "이용일자", "이용일", "승인일자", "거래일자", "거래일", "일자", "날짜",
    "결제일자", "결제일",
]
# "결제일"은 실제 결제(청구)일이지 거래가 일어난 날짜가 아니다. 그런데
# _match_col이 "정확히 일치"부터 모든 후보를 먼저 훑기 때문에, 헤더에
# "이용일시"(="이용일"의 부분일치로만 걸리던 것)와 "결제일"이 같이 있으면
# "결제일"이 정확히 일치한다는 이유로 먼저 뽑혀버렸다(씨티카드 엑셀에서
# 실제로 발생 - 거래일 대신 청구예정일이 잡힘). "이용일시"를 후보 목록
# 맨 앞에 정확히 추가해서, 있으면 무조건 "결제일"보다 먼저 집도록 한다.
_AMOUNT_HEADERS = ["이용금액", "승인금액", "결제금액", "청구금액", "이용대금", "금액"]
_MERCHANT_HEADERS = ["가맹점명", "가맹점", "사용처", "이용가맹점", "적요", "내용", "상호"]
_CARDNO_HEADERS = ["카드번호", "카드no", "카드 no", "이용카드", "카드"]
_CANCEL_STATUS_HEADERS = ["취소상태", "매입구분", "승인상태", "거래상태", "상태"]
_CANCEL_KEYWORDS = ["취소", "실패", "거절"]
_APPROVAL_HEADERS = ["승인번호", "승인번호(취소포함)", "거래번호", "승인no", "승인 no", "전표번호"]
_USAGE_TYPE_HEADERS = ["이용구분", "거래구분", "결제구분", "국내외구분", "상품구분"]

# 엑셀 포맷에 따라 "이용구분" 같은 국내/해외 정보가 아예 없는 경우가 있다
# (예: "상품구분"이 할부/일시불만 구분하고 국내외 구분이 없는 명세서). 그럴
# 땐 가맹점명으로 보조 판단한다 - 카드사마다 표기가 달라 완벽친 않지만,
# PayPal·AliExpress처럼 누가 봐도 해외 결제대행사인 것만 최소로 등록해둔다
# (확인 안 된 상호를 짐작으로 추가하진 않는다).
FOREIGN_MERCHANT_KEYWORDS = ["PAYPAL", "ALIEXPRESS", "ANTHROPIC"]


def is_foreign_merchant(merchant: str) -> bool:
    if not merchant:
        return False
    merchant_upper = merchant.upper()
    return any(k in merchant_upper for k in FOREIGN_MERCHANT_KEYWORDS)


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
                "approval_col": _match_col(cells, _APPROVAL_HEADERS),
                "usage_type_col": _match_col(cells, _USAGE_TYPE_HEADERS),
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
    씨티카드 폰 알림처럼 "0*0*"식으로 숫자 일부를 *로 가리는 마스킹도 있다
    - 이땐 자릿수가 같다는 전제로 자리마다 비교해서, *가 있는 자리는
    아무 숫자나 매치된 걸로 본다(예: "0*0*"는 "0104"와 같은 카드).
    """
    if not a or not b:
        return False
    if a == b:
        return True
    if "*" in a or "*" in b:
        return len(a) == len(b) and all(x == "*" or y == "*" or x == y for x, y in zip(a, b))
    return a.startswith(b) or b.startswith(a)


def parse_amount_cell(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace(",", "").replace("원", "").replace("￦", "").replace("₩", "").strip()
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


def parse_datetime_cell(value):
    """parse_date_cell과 같이 날짜를 인식하되, 셀에 시간까지 있으면
    "YYYY-MM-DD HH:MM:SS"로 시간도 살려서 돌려준다. 카드사 이용내역서 엑셀에
    "2026.08.15 23:11"처럼 시간이 같이 찍혀있는 경우, 결제 알림 시간표시에 쓴다.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        # openpyxl(.xlsx)은 날짜만 있는 셀도 항상 datetime으로 돌려준다(시간은
        # 무조건 00:00:00) - 진짜 자정 결제인지 그냥 날짜만 있던 셀인지 구분이
        # 안 되는데, 실제로는 거의 다 후자다(자정 결제는 극히 드묾). 그대로
        # "시간 있음"으로 처리하면 모든 .xlsx 날짜 열이 가짜 00:00:00 시간을
        # 달고 들어와서, 중복판정의 "비슷한 시간" 비교나 시간대 조건 매칭을
        # 다 망가뜨린다. 그래서 정각 자정이면 날짜만 있던 것으로 본다(.xls의
        # 기존 xlrd 처리 방식과 동일한 규칙).
        if value.hour == 0 and value.minute == 0 and value.second == 0:
            return value.date().isoformat()
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()

    s = str(value).strip()
    if not s:
        return None

    parts = s.split()
    date_part = parts[0] if parts else s
    time_part = parts[1] if len(parts) > 1 else ""

    s_norm = date_part.replace(".", "-").replace("/", "-").strip("-")
    parsed_date = None
    for fmt in ("%Y-%m-%d", "%y-%m-%d"):
        try:
            parsed_date = datetime.strptime(s_norm, fmt).date()
            break
        except ValueError:
            pass
    if parsed_date is None:
        digits = re.sub(r"\D", "", date_part)
        if len(digits) == 8:
            try:
                parsed_date = datetime.strptime(digits, "%Y%m%d").date()
            except ValueError:
                return None
        else:
            return None

    m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", time_part)
    if m:
        h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        if 0 <= h < 24 and 0 <= mi < 60 and 0 <= se < 60:
            return f"{parsed_date.isoformat()} {h:02d}:{mi:02d}:{se:02d}"
    return parsed_date.isoformat()


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
