# -*- coding: utf-8 -*-
"""부가세 통합 탭 - 마켓별 부가세 신고자료 엑셀 파싱

각 마켓이 내려주는 '부가세 신고자료' 엑셀은 서식이 마켓마다 다르다. 지금까지
실제로 확인된 4가지 서식을 자동 판별해서 (연도, 월, 결제수단)별 금액으로
집계한다. 결제수단은 신용카드/현금/휴대폰/기타 4종으로 통일.

서식 A (쿠팡·TOSS형): 신용카드(판매)/현금(판매)/기타(판매) - (환불) 컬럼
서식 B (11번가형): 신용카드결제금액/현금영수증(소득공제용)/현금영수증(지출증빙용)/휴대폰결제금액/기타결제금액
서식 C (G마켓형): '결제방법' 컬럼(행마다 카드/현금/휴대폰/기타)에 금액 컬럼
서식 D (네이버 등 정산결제수단형): 신용카드매출전표/현금영수증(소득공제)/현금영수증(지출증빙)/현금영수증(발행제외)/기타
"""
import re
import sqlite3
import pandas as pd

CATEGORIES = ['credit', 'cash', 'mobile', 'other']
CATEGORY_LABEL = {'credit': '신용카드', 'cash': '현금', 'mobile': '휴대폰', 'other': '기타'}

HEADER_SIGNATURE_KEYWORDS = ['신용카드(판매)', '신용카드결제금액', '결제방법', '신용카드매출전표', '신용카드 매출전표']


def _norm(s):
    return re.sub(r'\s+', '', str(s))


def _find_col(columns, *keywords_all_required):
    for c in columns:
        n = _norm(c)
        if all(k in n for k in keywords_all_required):
            return c
    return None


def _num(v):
    try:
        s = re.sub(r'[^\d.\-]', '', str(v))
        return float(s) if s not in ('', '-', '.') else 0.0
    except Exception:
        return 0.0


def _year_month(val):
    m = re.match(r'\s*(\d{4})[.\-/](\d{1,2})', str(val))
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    if 1 <= mo <= 12:
        return y, mo
    return None


def _find_header_row(raw_df):
    """raw_df: header=None으로 읽은 원본. 시그니처 키워드가 있는 행을 헤더로 판단."""
    for i in range(min(15, len(raw_df))):
        vals = [_norm(v) for v in raw_df.iloc[i].values]
        if any(any(kw.replace(' ', '') in v for v in vals) for kw in HEADER_SIGNATURE_KEYWORDS):
            return i
    return None


def _add(result, ym, category, amount):
    if amount == 0:
        return
    key = (ym[0], ym[1], category)
    result[key] = result.get(key, 0) + amount


def _parse_format_a(df):
    """쿠팡·TOSS형: 매출인식일 + 신용카드(판매)/현금(판매)/기타(판매) - (환불)"""
    date_col = _find_col(df.columns, '매출인식일') or _find_col(df.columns, '인식일')
    c_sale = _find_col(df.columns, '신용카드', '판매')
    if date_col is None or c_sale is None:
        return None
    c_ref = _find_col(df.columns, '신용카드', '환불')
    h_sale = _find_col(df.columns, '현금', '판매')
    h_ref = _find_col(df.columns, '현금', '환불')
    o_sale = _find_col(df.columns, '기타', '판매')
    o_ref = _find_col(df.columns, '기타', '환불')

    result = {}
    for _, row in df.iterrows():
        ym = _year_month(row.get(date_col))
        if not ym:
            continue
        credit = _num(row.get(c_sale)) - (_num(row.get(c_ref)) if c_ref else 0)
        cash = (_num(row.get(h_sale)) if h_sale else 0) - (_num(row.get(h_ref)) if h_ref else 0)
        other = (_num(row.get(o_sale)) if o_sale else 0) - (_num(row.get(o_ref)) if o_ref else 0)
        _add(result, ym, 'credit', credit)
        _add(result, ym, 'cash', cash)
        _add(result, ym, 'other', other)
    return result


def _parse_format_b(df):
    """11번가형: 결제완료일 + 신용카드결제금액/현금영수증(소득공제용,지출증빙용)/휴대폰결제금액/기타결제금액"""
    credit_col = _find_col(df.columns, '신용카드결제금액')
    mobile_col = _find_col(df.columns, '휴대폰결제금액')
    if credit_col is None and mobile_col is None:
        return None
    date_col = _find_col(df.columns, '결제완료일') or _find_col(df.columns, '결제일')
    if date_col is None:
        return None
    cash1_col = _find_col(df.columns, '현금영수증', '소득공제')
    cash2_col = _find_col(df.columns, '현금영수증', '지출증빙')
    other_col = _find_col(df.columns, '기타결제금액')

    result = {}
    for _, row in df.iterrows():
        ym = _year_month(row.get(date_col))
        if not ym:
            continue
        credit = _num(row.get(credit_col)) if credit_col else 0
        cash = (_num(row.get(cash1_col)) if cash1_col else 0) + (_num(row.get(cash2_col)) if cash2_col else 0)
        mobile = _num(row.get(mobile_col)) if mobile_col else 0
        other = _num(row.get(other_col)) if other_col else 0
        _add(result, ym, 'credit', credit)
        _add(result, ym, 'cash', cash)
        _add(result, ym, 'mobile', mobile)
        _add(result, ym, 'other', other)
    return result


def _parse_format_c(df):
    """G마켓형: 행마다 '결제방법'(카드/현금/휴대폰/기타)에 금액 1개"""
    method_col = _find_col(df.columns, '결제방법')
    if method_col is None:
        return None
    date_col = _find_col(df.columns, '입금', '환불일') or _find_col(df.columns, '결제일')
    amount_col = _find_col(df.columns, '구매자결제금') or _find_col(df.columns, '매출액')
    if date_col is None or amount_col is None:
        return None

    result = {}
    for _, row in df.iterrows():
        ym = _year_month(row.get(date_col))
        if not ym:
            continue
        method = str(row.get(method_col) or '').strip()
        if '카드' in method or '신용' in method:
            cat = 'credit'
        elif '현금' in method:
            cat = 'cash'
        elif '휴대' in method or '폰' in method:
            cat = 'mobile'
        else:
            cat = 'other'
        _add(result, ym, cat, _num(row.get(amount_col)))
    return result


def _parse_format_d(df):
    """네이버 등 정산결제수단형: 신용카드매출전표/현금영수증(소득공제,지출증빙,발행제외)/기타"""
    credit_col = _find_col(df.columns, '신용카드', '매출전표')
    if credit_col is None:
        return None
    date_col = (_find_col(df.columns, '세금신고기준일') or _find_col(df.columns, '기준일')
                or _find_col(df.columns, '신고일'))
    if date_col is None:
        return None
    cash_cols = [c for c in df.columns if '현금' in _norm(c)]
    other_col = None
    for c in df.columns:
        if _norm(c) == '기타':
            other_col = c
            break

    result = {}
    for _, row in df.iterrows():
        ym = _year_month(row.get(date_col))
        if not ym:
            continue
        credit = _num(row.get(credit_col))
        cash = sum(_num(row.get(c)) for c in cash_cols)
        other = _num(row.get(other_col)) if other_col else 0
        _add(result, ym, 'credit', credit)
        _add(result, ym, 'cash', cash)
        _add(result, ym, 'other', other)
    return result


PARSERS = [_parse_format_a, _parse_format_b, _parse_format_c, _parse_format_d]
FORMAT_NAMES = ['쿠팡·TOSS형', '11번가형', 'G마켓형', '네이버 등 결제수단형']


def parse_sheet(raw_df):
    """header=None으로 읽은 시트 하나. (집계결과 dict, 서식이름) 또는 (None, None)."""
    header_row = _find_header_row(raw_df)
    if header_row is None:
        return None, None
    df = raw_df.iloc[header_row + 1:].copy()
    df.columns = [str(c).strip() for c in raw_df.iloc[header_row].values]
    for parser, name in zip(PARSERS, FORMAT_NAMES):
        result = parser(df)
        if result:
            return result, name
    return None, None


def process_vat_upload(db_path, fp, filename, market):
    """엑셀 파일 하나(여러 시트 가능)를 파싱해서 vat_summary에 반영.
    같은 market의 (연,월)에 데이터가 있으면 그 (연,월)만 통째로 덮어쓴다
    (재업로드 시 중복 합산 방지)."""
    xl = pd.ExcelFile(fp)
    merged = {}
    matched_sheets = []
    for sheet in xl.sheet_names:
        raw_df = pd.read_excel(fp, sheet_name=sheet, header=None, dtype=str)
        result, fmt_name = parse_sheet(raw_df)
        if result:
            matched_sheets.append(f"{sheet}({fmt_name})")
            for key, amt in result.items():
                merged[key] = merged.get(key, 0) + amt

    if not merged:
        return {'error': '인식 가능한 부가세 서식을 찾지 못했습니다 (쿠팡·TOSS형/11번가형/G마켓형/네이버형 중 하나여야 합니다).'}

    year_months = sorted(set((y, m) for (y, m, _) in merged.keys()))
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    for y, m in year_months:
        cur.execute("DELETE FROM vat_summary WHERE market=? AND year=? AND month=?", (market, y, m))
    for (y, m, cat), amt in merged.items():
        cur.execute("""INSERT INTO vat_summary (market, year, month, category, amount)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(market, year, month, category) DO UPDATE SET amount = amount + excluded.amount""",
            (market, y, m, cat, int(round(amt))))
    conn.commit()
    conn.close()

    return {
        'sheets': matched_sheets,
        'months': [f"{y}-{m:02d}" for y, m in year_months],
    }


def get_vat_table(db_path, year=None, month=None):
    """year/month가 있으면 해당 (연,월)만, 없으면 전체 기간 합계."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    if year and month:
        cur.execute("SELECT market, category, amount FROM vat_summary WHERE year=? AND month=?", (year, month))
    else:
        cur.execute("SELECT market, category, SUM(amount) FROM vat_summary GROUP BY market, category")
    rows = cur.fetchall()

    cur.execute("SELECT DISTINCT year, month FROM vat_summary ORDER BY year, month")
    available_months = [f"{y}-{m:02d}" for y, m in cur.fetchall()]
    conn.close()

    by_market = {}
    for market, cat, amt in rows:
        by_market.setdefault(market, {c: 0 for c in CATEGORIES})[cat] = int(amt or 0)

    table = []
    total = {c: 0 for c in CATEGORIES}
    for market, cats in sorted(by_market.items()):
        row_total = sum(cats.values())
        table.append({'market': market, **cats, 'total': row_total})
        for c in CATEGORIES:
            total[c] += cats[c]
    total['total'] = sum(total.values())

    return {'rows': table, 'total': total, 'available_months': available_months}
