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

HEADER_SIGNATURE_KEYWORDS = ['신용카드(판매)', '신용카드결제금액', '결제방법', '결제수단',
                              '신용카드매출전표', '신용카드 매출전표']


def _norm(s):
    return re.sub(r'\s+', '', str(s))


def _find_col(columns, *keywords_all_required):
    for c in columns:
        n = _norm(c)
        if all(k in n for k in keywords_all_required):
            return c
    return None


def _find_exact_col(columns, *names):
    normed = {_norm(name) for name in names}
    for c in columns:
        if _norm(c) in normed:
            return c
    return None


def _scalar(v):
    # 실제 마켓 엑셀에는 빈 헤더 셀이 여러 개라 컬럼명이 중복되는 경우가 있는데,
    # 이러면 df[col]이 Series를 여러 개 묶어서 돌려줘서 이후 계산이 통째로 죽는다.
    # 그럴 땐 첫 번째 값만 취한다.
    if isinstance(v, pd.Series):
        return v.iloc[0] if len(v) else None
    return v


def _num(v):
    try:
        s = re.sub(r'[^\d.\-]', '', str(_scalar(v)))
        return float(s) if s not in ('', '-', '.') else 0.0
    except Exception:
        return 0.0


def _year_month(val):
    # "2026-05-01" / "2026.06.05" / "2026/03/21" / "2026. 05. 19. 21:16:32"(마침표 뒤 공백 포함) 전부 대응
    m = re.match(r'\s*(\d{4})[.\-/]\s*(\d{1,2})', str(_scalar(val)))
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
    """쿠팡·TOSS형: 매출인식일 + 신용카드(판매)/현금(판매)/기타(판매) - (환불).
    TOSS는 '현금(판매)' 컬럼이 따로 없고, '기타(판매)'(토스머니 전체)에 현금영수증
    발행분이 섞여 들어온다. 이럴 땐 '현금영수증' 컬럼을 현금으로 따로 떼어내고,
    기타에서는 그만큼을 빼서 순수 전자화폐 금액만 남긴다."""
    date_col = _find_col(df.columns, '매출인식일') or _find_col(df.columns, '인식일')
    c_sale = _find_col(df.columns, '신용카드', '판매')
    if date_col is None or c_sale is None:
        return None
    c_ref = _find_col(df.columns, '신용카드', '환불')
    h_sale = _find_col(df.columns, '현금', '판매')
    h_ref = _find_col(df.columns, '현금', '환불')
    o_sale = _find_col(df.columns, '기타', '판매')
    o_ref = _find_col(df.columns, '기타', '환불')
    receipt_col = None
    if h_sale is None:
        receipt_col = _find_col(df.columns, '현금영수증')

    result = {}
    for _, row in df.iterrows():
        try:
            ym = _year_month(row.get(date_col))
            if not ym:
                continue
            credit = _num(row.get(c_sale)) - (_num(row.get(c_ref)) if c_ref else 0)
            other_raw = (_num(row.get(o_sale)) if o_sale else 0) - (_num(row.get(o_ref)) if o_ref else 0)
            if h_sale:
                cash = _num(row.get(h_sale)) - (_num(row.get(h_ref)) if h_ref else 0)
                other = other_raw
            elif receipt_col:
                cash = _num(row.get(receipt_col))
                other = other_raw - cash
            else:
                cash = 0
                other = other_raw
            _add(result, ym, 'credit', credit)
            _add(result, ym, 'cash', cash)
            _add(result, ym, 'other', other)
        except Exception:
            continue
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
        try:
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
        except Exception:
            continue
    return result


def _parse_format_c(df):
    """행마다 결제수단 컬럼(카드/현금/휴대폰/기타/토스머니 등)에 금액 1개인 서식.
    G마켓형('결제방법'), TOSS 주문상세형('결제수단'+'결제수단 결제 금액'), 옥션 등
    오픈마켓형('결제수단'+'매출금액')을 전부 이 하나로 처리."""
    method_col = _find_exact_col(df.columns, '결제방법', '결제수단')
    if method_col is None:
        return None
    date_col = (_find_col(df.columns, '입금', '환불일') or _find_col(df.columns, '매출기준일')
                or _find_col(df.columns, '결제일시') or _find_col(df.columns, '결제완료일')
                or _find_col(df.columns, '결제일'))
    amount_col = (_find_col(df.columns, '구매자결제금') or _find_col(df.columns, '결제금액')
                  or _find_col(df.columns, '매출금액') or _find_col(df.columns, '매출액'))
    if date_col is None or amount_col is None:
        return None

    result = {}
    for _, row in df.iterrows():
        try:
            ym = _year_month(row.get(date_col))
            if not ym:
                continue
            method = str(_scalar(row.get(method_col)) or '').strip()
            if '카드' in method or '신용' in method:
                cat = 'credit'
            elif '현금' in method:
                cat = 'cash'
            elif '휴대' in method or '폰' in method:
                cat = 'mobile'
            else:
                cat = 'other'
            _add(result, ym, cat, _num(row.get(amount_col)))
        except Exception:
            continue
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
        try:
            ym = _year_month(row.get(date_col))
            if not ym:
                continue
            credit = _num(row.get(credit_col))
            cash = sum(_num(row.get(c)) for c in cash_cols)
            other = _num(row.get(other_col)) if other_col else 0
            _add(result, ym, 'credit', credit)
            _add(result, ym, 'cash', cash)
            _add(result, ym, 'other', other)
        except Exception:
            continue
    return result


PARSERS = [_parse_format_a, _parse_format_b, _parse_format_c, _parse_format_d]
FORMAT_NAMES = ['쿠팡·TOSS형', '11번가형', '결제수단별(G마켓·TOSS주문상세·옥션 등)', '네이버 등 결제수단형']


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


def _read_raw_sheets(fp, filename):
    """(시트이름, header=None 원본 DataFrame) 리스트. csv는 시트 1개짜리로 취급.
    fp: 디스크 임시파일을 거치지 않고 업로드된 내용을 그대로 담은 BytesIO.
    (윈도우 백신이 방금 생성된 임시파일을 잠가서 PermissionError가 나던 문제를
    아예 디스크에 안 쓰는 방식으로 근본 해결)"""
    if filename.lower().endswith('.csv'):
        try:
            raw_df = pd.read_csv(fp, header=None, dtype=str, encoding='utf-8-sig')
        except UnicodeDecodeError:
            fp.seek(0)
            raw_df = pd.read_csv(fp, header=None, dtype=str, encoding='cp949')
        return [('CSV', raw_df)]
    xl = pd.ExcelFile(fp)
    return [(sheet, pd.read_excel(fp, sheet_name=sheet, header=None, dtype=str)) for sheet in xl.sheet_names]


def process_vat_upload(db_path, fp, filename, market):
    """엑셀/CSV 파일 하나(엑셀은 여러 시트 가능)를 파싱해서 vat_summary에 반영.
    같은 market의 (연,월)에 데이터가 있으면 그 (연,월)만 통째로 덮어쓴다
    (재업로드 시 중복 합산 방지)."""
    merged = {}
    matched_sheets = []
    for sheet, raw_df in _read_raw_sheets(fp, filename):
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


def get_vat_half_detail(db_path, year, half):
    """half(1=상반기 1~6월, 2=하반기 7~12월)를 '부가세 통합 엑셀'처럼 마켓별로 묶고,
    그 안에서 다시 결제수단(신용카드/현금/휴대폰/기타)별로 나눠서 월별 금액을 보여주는
    표로 반환한다. 마켓마다 [소계 + 결제수단 4줄], 오른쪽 끝엔 합계열, 맨 위엔 전체
    총합계를 둔다."""
    months = list(range(1, 7)) if half == 1 else list(range(7, 13))
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    placeholders = ','.join('?' * len(months))
    cur.execute(f"""SELECT market, month, category, SUM(amount) FROM vat_summary
        WHERE year=? AND month IN ({placeholders}) GROUP BY market, month, category""", [year, *months])
    rows = cur.fetchall()
    cur.execute("SELECT DISTINCT year FROM vat_summary ORDER BY year")
    available_years = [r[0] for r in cur.fetchall()]
    conn.close()

    markets = sorted({market for market, _, _, _ in rows})
    cell = {(market, m, cat): int(amt or 0) for market, m, cat, amt in rows}

    market_groups = []
    grand_month_totals = [0] * len(months)
    for market in markets:
        categories = {}
        subtotal_months = [0] * len(months)
        for cat in CATEGORIES:
            cat_months = [cell.get((market, m, cat), 0) for m in months]
            categories[cat] = {'label': CATEGORY_LABEL[cat], 'months': cat_months, 'row_total': sum(cat_months)}
            subtotal_months = [a + b for a, b in zip(subtotal_months, cat_months)]
        market_groups.append({
            'market': market, 'categories': categories,
            'months': subtotal_months, 'row_total': sum(subtotal_months),
        })
        grand_month_totals = [a + b for a, b in zip(grand_month_totals, subtotal_months)]

    market_groups.sort(key=lambda g: g['row_total'], reverse=True)
    grand_total = sum(grand_month_totals)

    return {
        'months': months, 'market_groups': market_groups, 'grand_month_totals': grand_month_totals,
        'grand_total': grand_total, 'available_years': available_years,
    }
