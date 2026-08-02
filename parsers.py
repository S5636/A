# -*- coding: utf-8 -*-
"""엑셀/CSV 업로드 파싱 (스펙 5.1) + HL 수기 매입처 텍스트 파싱 (부록 C)"""
import re
import sqlite3
import pandas as pd

from calc_engine import clean_id, safe_float, STATUS_CODE_MAP


def _clamp_ship_fee(val):
    s = str(val).strip().replace(',', '')
    if s.lower() in ('nan', 'none', '-', ''):
        return '0'
    try:
        v = float(s)
        return str(int(v)) if v < 500000 else '0'
    except ValueError:
        return '0'


def _decode_status(val):
    v = str(val).strip()
    if v.endswith('.0'):
        v = v[:-2]
    if v.lower() in ('nan', 'none', ''):
        return '신규주문'
    return STATUS_CODE_MAP.get(v, v)


def _get_col(df, name_list):
    for n in name_list:
        if n in df.columns:
            return df[n]
    return pd.Series([''] * len(df), index=df.index)


def _clean_date_str(date_val):
    s = str(date_val).strip()
    if not s or s in ('nan', '0', 'None', ''):
        return ''
    s = s.replace('/', '-').replace('.', '-')
    s = re.sub(r'\s*\((.*?)\)', r' \1:00', s)
    return s.strip()


def detect_and_normalize(df):
    """다팔자 / TOSS 정산 엑셀을 판별해 표준 컬럼으로 변환. 매칭 실패 시 None."""
    if '마켓별칭' in df.columns or '판매처' in df.columns:
        new_df = pd.DataFrame()
        new_df['order_id'] = _get_col(df, ['상품주문번호', '주문상품번호']).apply(clean_id)
        new_df['bundle_no'] = _get_col(df, ['주문번호', '결제번호', '원주문번호', '묶음번호']).apply(clean_id)
        new_df['market'] = _get_col(df, ['마켓별칭', '판매처'])
        new_df['order_date'] = _get_col(df, ['주문일시', '결제일시']).apply(_clean_date_str)
        new_df['prod_id'] = _get_col(df, ['상품ID', '상품번호'])
        new_df['vendor_prod_id'] = _get_col(df, ['판매사상품번호', '업체상품코드'])
        new_df['prod_name'] = _get_col(df, ['상품명', '주문상품명'])
        new_df['order_amt'] = _get_col(df, ['주문금액', '결제금액'])
        new_df['ship_fee'] = _get_col(df, ['마켓배송비', '배송비']).apply(_clamp_ship_fee)
        new_df['add_ship_fee'] = _get_col(df, ['추가배송비', '추가비']).apply(_clamp_ship_fee)
        new_df['qty'] = _get_col(df, ['주문수량', '수량'])
        new_df['sell_status'] = _get_col(df, ['주문상태', '진행상태']).apply(_decode_status)
        new_df['source'] = '다팔자'
        return new_df.fillna(''), '다팔자'

    if '배송비합계' in df.columns or '주문상품번호' in df.columns or '주문금액' in df.columns or '거래금액' in df.columns:
        new_df = pd.DataFrame()
        new_df['order_id'] = _get_col(df, ['주문상품번호']).apply(clean_id)
        new_df['bundle_no'] = _get_col(df, ['주문번호', '결제번호']).apply(clean_id)
        new_df['order_date'] = _get_col(df, ['주문일시', '주문일자', '결제일시']).apply(_clean_date_str)
        new_df['prod_id'] = _get_col(df, ['상품ID', '상품번호'])
        new_df['vendor_prod_id'] = _get_col(df, ['상품관리코드', '옵션관리코드', '상품코드', '옵션코드']).astype(str)
        new_df['prod_name'] = _get_col(df, ['상품명'])
        new_df['order_amt'] = _get_col(df, ['주문금액', '거래금액', '결제금액'])
        new_df['ship_fee'] = _get_col(df, ['배송비합계', '배송비']).apply(_clamp_ship_fee)
        new_df['add_ship_fee'] = '0'
        new_df['qty'] = _get_col(df, ['주문건수', '수량'])
        new_df['sell_status'] = _get_col(df, ['주문상태', '상태'])
        new_df['source'] = 'TOSS'
        new_df['market'] = 'TOSS'
        new_df = new_df[~new_df['order_id'].astype(str).str.replace(' ', '').str.contains('수정불가|수정가능', na=False)]
        return new_df.fillna(''), 'TOSS'

    return None, None


OWNERCLAN_COLS = ['주문코드', '주문일자', '상품코드', '상품명', '배송상태', '주문수량', '택배회사',
                   '송장번호', '받는사람', '보내는사람', '총결제금액', '상품가격', '배송비',
                   '택배송장메모', '주문관리메모']


def _normalize_header(cols):
    return [re.sub(r'\s+', '', str(x)).replace('﻿', '') for x in cols]


def read_upload_file(fp, filename):
    """fp: 디스크 임시파일을 거치지 않고 업로드된 내용을 그대로 담은 BytesIO.
    (윈도우 백신이 방금 생성된 임시파일을 잠가서 PermissionError가 나던 문제를
    아예 디스크에 안 쓰는 방식으로 근본 해결)"""
    if filename.lower().endswith('.csv'):
        try:
            return pd.read_csv(fp, encoding='utf-8-sig', dtype=str, engine='python', on_bad_lines='skip')
        except Exception:
            fp.seek(0)
            return pd.read_csv(fp, encoding='cp949', dtype=str, engine='python', on_bad_lines='skip')
    return pd.read_excel(fp, dtype=str)


def process_upload(db_path, fp, filename):
    """엑셀/CSV 한 개 처리. 반환: {'type': ..., 'inserted': n, 'updated': n} 또는 {'error': ...}"""
    df = read_upload_file(fp, filename)
    temp_df = df.copy()

    header_keywords = ['주문번호', '주문상품번호', '상품주문번호', '원장주문코드']
    cols = _normalize_header(temp_df.columns)
    has_found = any(k in cols for k in header_keywords)
    if has_found:
        temp_df.columns = cols
    else:
        for i in range(min(15, len(temp_df))):
            vals = _normalize_header(temp_df.iloc[i].values)
            if any(k in vals for k in header_keywords):
                temp_df.columns = vals
                temp_df = temp_df.iloc[i + 1:].reset_index(drop=True)
                has_found = True
                break
    temp_df.columns = _normalize_header(temp_df.columns)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    if '원장주문코드' in temp_df.columns:
        n_rows = 0
        # 스펙 5.2가 가정하는 '합배송 3단계 구조'(원장주문코드 없는 빈 행이
        # 하위상품/합산행으로 낀다)가 지금도 실제 다운로드 파일에 있는지
        # 확인하려고 빈 원장주문코드 행 개수를 같이 세어둔다 - 이게 0이면
        # 그 3단계 구조 자체가 지금 안 내려온다는 뜻이라, 합배송 매입상태
        # 매칭이 안 맞는 문제의 원인일 수 있다.
        n_blank_oid = 0
        for _, r in temp_df.iterrows():
            oid = clean_id(r.get('원장주문코드', ''))
            jcode = clean_id(r.get('주문코드', ''))
            if not oid:
                n_blank_oid += 1
            row_vals = []
            for c in OWNERCLAN_COLS:
                if c == '주문코드':
                    row_vals.append(jcode)
                else:
                    v = str(r.get(c, '')).strip()
                    if v.endswith('.0') and c in ['송장번호', '상품코드', '주문수량']:
                        v = v[:-2]
                    if v.lower() in ('nan', 'none', '-', '0.0'):
                        v = ''
                    row_vals.append(v)
            has_any_value = oid or jcode or any(v for v in row_vals if v)
            if not has_any_value:
                continue
            vals = [oid] + row_vals
            cur.execute(f"""INSERT OR REPLACE INTO ownerclan_raw
                (원장주문코드, {', '.join(OWNERCLAN_COLS)}) VALUES ({', '.join(['?'] * (len(OWNERCLAN_COLS) + 1))})""", vals)
            n_rows += 1
        conn.commit()
        conn.close()
        return {'type': '오너클랜 발주내역', 'inserted': n_rows, 'updated': 0, 'blank_ledger_rows': n_blank_oid}

    df_map, source_type = detect_and_normalize(temp_df)
    if df_map is None or df_map.empty:
        conn.close()
        return {'error': '알 수 없는 파일 형식입니다 (다팔자/TOSS/오너클랜 서식이 아님)'}

    cur.execute("SELECT order_id FROM merged_orders")
    existing = {str(r[0]).strip() for r in cur.fetchall()}
    c_in = c_up = 0
    for _, row in df_map.iterrows():
        order_id = clean_id(row['order_id'])
        bundle_no = clean_id(row.get('bundle_no', ''))
        if not order_id:
            continue
        if order_id in existing:
            cur.execute("""UPDATE merged_orders SET source=?, market=?, sell_status=?, order_date=?,
                prod_id=?, prod_name=?, qty=?, order_amt=?, ship_fee=?, vendor_prod_id=?,
                bundle_no=?, add_ship_fee=? WHERE order_id=?""", (
                str(row['source']), str(row['market']), str(row['sell_status']), str(row['order_date']),
                str(row['prod_id']), str(row['prod_name']), str(row['qty']), str(row['order_amt']),
                str(row['ship_fee']), str(row['vendor_prod_id']), bundle_no, str(row.get('add_ship_fee', '0')),
                order_id))
            c_up += 1
        else:
            cur.execute("""INSERT INTO merged_orders (order_id, source, market, sell_status, order_date,
                prod_id, prod_name, qty, order_amt, ship_fee, vendor_prod_id, margin_chk, bundle_no, add_ship_fee)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'AUTO', ?, ?)""", (
                order_id, str(row['source']), str(row['market']), str(row['sell_status']), str(row['order_date']),
                str(row['prod_id']), str(row['prod_name']), str(row['qty']), str(row['order_amt']),
                str(row['ship_fee']), str(row['vendor_prod_id']), bundle_no, str(row.get('add_ship_fee', '0'))))
            existing.add(order_id)
            c_in += 1
    conn.commit()
    conn.close()
    return {'type': source_type, 'inserted': c_in, 'updated': c_up}


# ---------------------------------------------------------------------------
# HL(수기 매입처) 텍스트 파싱 - 부록 C
# ---------------------------------------------------------------------------

_DATE_PATTERN = re.compile(r'^\d{4}\.\d{2}\.\d{2}$')
_PRICE_PATTERN = re.compile(r'(.*?)\s*([0-9,]+)\s*원')


def _process_hl_block(block_lines):
    date_str, order_no, status_str = block_lines[0], "", ""
    product_lines = []
    for line in block_lines[1:]:
        if line.startswith('202') and sum(ch.isdigit() for ch in line) >= 8:
            order_no = line
        elif "입금" in line or "출고" in line:
            status_str = line
        else:
            product_lines.append(line)

    product_info = " ".join(product_lines)
    product_name, cost_str = product_info, "0"
    match = _PRICE_PATTERN.search(product_info)
    if match:
        product_name = match.group(1).strip()
        cost_str = match.group(2).replace(',', '')

    try:
        cost_val = int(cost_str)
    except ValueError:
        cost_val = 0
    ship_fee = 3000 if 0 < cost_val < 50000 else 0

    return {
        'order_date': date_str, 'hl_order_no': order_no, 'prod_name': product_name,
        'buy_status': status_str, 'buy_cost': cost_val, 'ship_fee': ship_fee,
    }


def parse_hl_text(raw_text):
    text = raw_text.replace('\xa0', ' ').strip().replace('\t', '\n')
    lines = [ln.strip() for ln in text.split('\n') if ln.strip()]
    blocks, current = [], []
    for line in lines:
        if _DATE_PATTERN.match(line):
            if current:
                blocks.append(_process_hl_block(current))
            current = [line]
        else:
            if current:
                current.append(line)
    if current:
        blocks.append(_process_hl_block(current))
    return blocks


def save_hl_matching(db_path, rows):
    """rows: [{hl_order_no, buy_status, buy_cost, ship_fee, manual_order_id}]"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    updated, errors = 0, []
    for i, row in enumerate(rows):
        manual_raw = str(row.get('manual_order_id', '')).strip()
        if not manual_raw:
            continue
        manual_order_id = re.sub(r'[^0-9A-Za-z]', '', manual_raw)
        hl_order_no = re.sub(r'[^0-9]', '', str(row.get('hl_order_no', '')))
        cost_val = safe_float(row.get('buy_cost', 0))
        ship_val = safe_float(row.get('ship_fee', 0))
        status_text = str(row.get('buy_status', ''))

        if len(manual_order_id) >= 5 and len(hl_order_no) >= 5:
            cur.execute("""INSERT OR REPLACE INTO purchase_ledger
                (order_id, vendor_prod_id, buy_cost, buy_ship_fee, buy_total, buy_status)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (manual_order_id, hl_order_no, cost_val, ship_val, cost_val + ship_val, status_text))
            updated += 1
        else:
            errors.append({'row': i + 1, 'manual_order_id': manual_order_id, 'hl_order_no': hl_order_no})
    conn.commit()
    conn.close()
    return updated, errors
