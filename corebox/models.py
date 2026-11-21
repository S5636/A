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
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,              -- 서류명(예: "사업자 등록증", "2020 소득금액증명")
    category TEXT DEFAULT '',        -- 자유 분류(예: "사업자 서류", "세금", "통장")
    year INTEGER,                    -- 연도(해당 없으면 NULL - 사업자등록증처럼 연도 무관한 서류)
    doc_type TEXT DEFAULT '',        -- 집계용 문서 종류(예: "소득금액증명") - 비어있으면 집계표에 안 잡힘
    income_amount REAL,              -- 수입금액(해당 없으면 NULL)
    taxable_income_amount REAL,      -- 소득금액(해당 없으면 NULL)
    file_name TEXT DEFAULT '',       -- 실제 저장 파일명(uploads/ 안의 이름, 랜덤)
    original_name TEXT DEFAULT '',   -- 업로드할 때 원래 파일명(다운로드 시 이 이름으로 줌)
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
        conn.commit()
    finally:
        conn.close()
