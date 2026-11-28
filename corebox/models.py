# -*- coding: utf-8 -*-
"""SQLite 스키마 - 서류 보관함(COREBOX).

자주 쓰는 서류(사업자등록증, 신고증, 통장사본, 연도별 세금 서류 등)를 한
곳에 모아두고 다운받을 수 있게 하고, 소득금액증명처럼 숫자가 있는
서류는 연도별로 자동 집계해서 보여준다."""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "corebox.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")

SCHEMA = """
CREATE TABLE IF NOT EXISTS doc_type_order (
    doc_type TEXT PRIMARY KEY,       -- "소득금액증명" 등 - 연도별 집계 섹션 순서 기억용
    sort_order INTEGER NOT NULL DEFAULT 0
);

-- 서류 보관함과 별개 탭: 매달 급여명세서를 올리면 항목별로 기록해서
-- 1월~12월 표로 보여준다. year/month가 어느 명세서인지는 서류 내용만
-- 보고는 확실히 알 수 없어서(발급/서명일만 있고 "몇 월分"인지가 서식에
-- 따라 다름) 업로드할 때 사람이 직접 고른다.
CREATE TABLE IF NOT EXISTS salary_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,          -- 1~12
    base_pay REAL,                   -- 기본급
    overtime_pay REAL,               -- 연장수당
    position_pay REAL,               -- 직책수당
    night_pay REAL,                  -- 야근수당
    overnight_pay REAL,              -- 철야수당
    special_pay REAL,                -- 특근수당
    aiming_pay REAL,                 -- 에이밍수당(회사 고유 항목명)
    other_pay REAL,                  -- 기타
    gross_pay REAL,                  -- 소계(지급)
    income_tax REAL,                 -- 갑근세
    local_tax REAL,                  -- 주민세
    national_pension REAL,           -- 국민연금
    health_insurance REAL,           -- 건강보험
    employment_insurance REAL,       -- 고용보험
    loan_repayment REAL,             -- 학자금대출상환
    total_deduction REAL,            -- 소계(공제)
    net_pay REAL,                    -- 실지급액
    file_name TEXT DEFAULT '',
    original_name TEXT DEFAULT '',
    file_hash TEXT DEFAULT '',       -- 같은 파일 중복 업로드 감지용
    memo TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,              -- 서류명(예: "사업자 등록증", "2020 소득금액증명")
    category TEXT DEFAULT '',        -- 자유 분류(예: "사업자 서류", "세금", "통장")
    year INTEGER,                    -- 연도(해당 없으면 NULL - 사업자등록증처럼 연도 무관한 서류)
    doc_type TEXT DEFAULT '',        -- 집계용 문서 종류(예: "소득금액증명") - 비어있으면 집계표에 안 잡힘
    income_amount REAL,              -- 수입금액(해당 없으면 NULL)
    taxable_income_amount REAL,      -- 소득금액(해당 없으면 NULL)
    amount REAL,                     -- 소득금액증명 외 서류의 금액(부가세는 과세표준액, 해당 없으면 NULL)
    tax_amount REAL,                 -- 부가세의 실제 납부/환급 세액(환급이면 음수, 해당 없으면 NULL)
    issue_date TEXT DEFAULT '',      -- 발급일(YYYY-MM-DD) - 제출 유효기간(보통 한 달) 확인용
    period TEXT DEFAULT '',          -- 서류가 다루는 기간(과세기간 등, "YYYY-MM-DD~YYYY-MM-DD") - 발급일과 별개
    period_label TEXT DEFAULT '',    -- 목록 버튼에 보여줄 짧은 이름(예: "2025년 1기", "2020년")
    file_name TEXT DEFAULT '',       -- 실제 저장 파일명(uploads/ 안의 이름, 랜덤)
    original_name TEXT DEFAULT '',   -- 업로드할 때 원래 파일명(다운로드 시 이 이름으로 줌)
    file_hash TEXT DEFAULT '',       -- 업로드 파일 내용의 sha256 - 같은 파일 중복 업로드 감지용
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
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        _ensure_column(conn, "documents", "amount", "amount REAL")
        _ensure_column(conn, "documents", "issue_date", "issue_date TEXT DEFAULT ''")
        _ensure_column(conn, "documents", "period", "period TEXT DEFAULT ''")
        _ensure_column(conn, "documents", "period_label", "period_label TEXT DEFAULT ''")
        _ensure_column(conn, "documents", "tax_amount", "tax_amount REAL")
        _ensure_column(conn, "documents", "file_hash", "file_hash TEXT DEFAULT ''")
        # 소득금액증명의 수입금액/소득금액 행에는 사업/근로/기타 소득별
        # 세부 금액도 같이 찍혀 나온다 - 합계만 보여주지 말고 그것도 같이.
        _ensure_column(conn, "documents", "income_business", "income_business REAL")
        _ensure_column(conn, "documents", "income_wage", "income_wage REAL")
        _ensure_column(conn, "documents", "income_other", "income_other REAL")
        _ensure_column(conn, "documents", "taxable_business", "taxable_business REAL")
        _ensure_column(conn, "documents", "taxable_wage", "taxable_wage REAL")
        _ensure_column(conn, "documents", "taxable_other", "taxable_other REAL")
        # 종합소득세 신고서의 근로소득 지급자(회사)별 내역 - 회사 수가
        # 사람마다 다르니 JSON 배열(문자열)로 저장: [{"name":..,"income":..,"taxable":..}, ...]
        _ensure_column(conn, "documents", "income_sources", "income_sources TEXT DEFAULT ''")
        # 스캔본 OCR은 파일 하나당 몇 초~몇십 초씩 걸릴 수 있어서, "서류
        # 분류 진단"이 등록된 서류 전체를 매번 다시 OCR 돌리면 응답이
        # 느려져 브라우저 쪽 요청이 타임아웃난다(card_benefit의 "매칭
        # 진단"에서 실제로 같은 원인으로 재현됨) - 한 번 뽑은 글자는
        # 캐시해서 재사용한다.
        _ensure_column(conn, "documents", "extracted_text", "extracted_text TEXT DEFAULT ''")
        # 급여명세서 연도 재검사(_backfill_salary_future_year)도 같은
        # 이유로 재추출 결과를 캐시해서 재시작마다 다시 OCR 돌리지 않게 함.
        _ensure_column(conn, "salary_records", "extracted_text", "extracted_text TEXT DEFAULT ''")

        # 2026-11: 자동분류 로직이 아직 "발급일자"보다 "과세기간"을 우선
        # 보게 고쳐지기 전에 올린 "22년1기" 부가세 과세표준증명이 발급일자
        # (2023.01.02)의 연도를 잘못 집어서 2023년으로 등록됐다. 파일명으로
        # 특정해서 바로잡는다 - 이미 고쳐진 뒤에는 조건에 안 걸려서 다시
        # 실행해도 안전함(멱등).
        bad_vat_doc = conn.execute(
            "SELECT id FROM documents WHERE original_name LIKE '%22년1기%' AND year = 2023"
        ).fetchone()
        if bad_vat_doc:
            conn.execute(
                "UPDATE documents SET year = 2022, name = '2022 부가가치세 과세표준증명', "
                "period = '2022-01-01~2022-06-30', issue_date = '2023-01-02' WHERE id = ?",
                (bad_vat_doc["id"],),
            )

        conn.commit()
    finally:
        conn.close()
