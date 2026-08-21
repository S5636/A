# -*- coding: utf-8 -*-
"""엑셀/CSV 업로드 파싱 (스펙 5.1) + HL 수기 매입처 텍스트 파싱 (부록 C)"""
import json
import re
import sqlite3
import pandas as pd

from calc_engine import clean_id, safe_float, STATUS_CODE_MAP


def _clean_vendor_prod_id(val):
    # 판매사상품코드가 정상일 땐 'W...'로 시작하는데, 일부 상품은 같은
    # 칸에 'sellerbot_W...'처럼 접두어가 붙어서 들어온다 - 접두어가 있으면
    # 실제 코드(W로 시작하는 부분)만 남긴다. 이 접두어 때문에 같은 상품인데
    # 코드가 서로 다르게 보여서 합배송 매칭(수령인+배송지+상품코드 비교)이
    # 실패하는 사고가 있었다(사용자 지시로 정규화).
    s = str(val).strip()
    if s.lower().startswith('sellerbot_'):
        s = s[len('sellerbot_'):]
    return s


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


def _raw_json_column(df):
    """지금 당장 쓰는 컬럼으로 안 뽑아내는 값들도(택배사/운송장번호/CS메모 등)
    나중에 필요해질 수 있으니, 업로드 원본 행 전체를 손대지 않고 그대로
    JSON으로 같이 저장해둔다(사용자 지시: "가공하지 말고 다 db에 쌓아라") -
    당장 화면에 안 보여도 이 컬럼에서 언제든 꺼내 쓸 수 있다."""
    def _clean(v):
        if pd.isna(v):
            return ''
        return v
    return [json.dumps({k: _clean(v) for k, v in row.items()}, ensure_ascii=False, default=str)
            for _, row in df.iterrows()]


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
        new_df['vendor_prod_id'] = _get_col(df, ['판매사상품번호', '업체상품코드']).apply(_clean_vendor_prod_id)
        new_df['prod_name'] = _get_col(df, ['상품명', '주문상품명'])
        new_df['order_amt'] = _get_col(df, ['주문금액', '결제금액'])
        new_df['ship_fee'] = _get_col(df, ['마켓배송비', '배송비']).apply(_clamp_ship_fee)
        new_df['add_ship_fee'] = _get_col(df, ['추가배송비', '추가비']).apply(_clamp_ship_fee)
        new_df['qty'] = _get_col(df, ['주문수량', '수량'])
        _raw_status = _get_col(df, ['주문상태', '진행상태'])
        new_df['sell_status'] = _raw_status.apply(_decode_status)
        # 주문상태가 빈 값이면 '신규주문'으로 기본 처리되는데, 실제로는
        # 다팔자에 '배송준비' 등 다른 상태로 찍혀있는데도 우리 쪽이 그
        # 컬럼을 못 찾아서 계속 신규주문으로 보이는 사고인지 확인하려고
        # 원래 빈 값이었는지를 따로 남겨둔다.
        new_df['sell_status_was_blank'] = _raw_status.apply(
            lambda v: str(v).strip().lower() in ('nan', 'none', ''))
        new_df['option_name'] = _get_col(df, ['옵션', '옵션정보', '주문옵션', '상품옵션', '옵션명', '마켓상품옵션명'])
        # 오너클랜 발주내역에 원장주문코드 흔적이 아예 없는 합배송 건을
        # 찾아내려고(사용자 지시) 수령인+배송지를 같이 저장해둔다 - 같은
        # 상품을 같은 날 같은 사람/주소로 보낸 미매입 주문은, 매칭된 다른
        # 주문과 묶어서 매입가를 나눠 받을 수 있게 하는 근거가 된다.
        new_df['recipient'] = _get_col(df, ['수령인', '받는사람'])
        new_df['ship_address'] = _get_col(df, ['배송지', '주소'])
        # 쿠폰 할인 여부를 "주문금액이 얼마 이상이면 몇 % 추가수수료"로 추측하던
        # 방식이 실제로 쿠폰 안 쓴 주문까지 걸려서 부정확했다(사용자 지적) -
        # 다팔자 원본에 건별 실제 할인액이 그대로 있으니 그걸 직접 쓴다.
        # 정산가는 다팔자가 자체 계산한 값으로, 우리 계산과 비교/검증용으로만
        # 같이 저장해둔다.
        new_df['discount_amt'] = _get_col(df, ['할인금액'])
        new_df['settle_amt_dpj'] = _get_col(df, ['정산가'])
        new_df['recipient_phone'] = _get_col(df, ['수령인연락처'])
        new_df['zipcode'] = _get_col(df, ['우편번호'])
        new_df['raw_json'] = _raw_json_column(df)
        new_df['source'] = '다팔자'
        return new_df.fillna(''), '다팔자'

    if '배송비합계' in df.columns or '주문상품번호' in df.columns or '주문금액' in df.columns or '거래금액' in df.columns:
        new_df = pd.DataFrame()
        new_df['order_id'] = _get_col(df, ['주문상품번호']).apply(clean_id)
        new_df['bundle_no'] = _get_col(df, ['주문번호', '결제번호']).apply(clean_id)
        new_df['order_date'] = _get_col(df, ['주문일시', '주문일자', '결제일시']).apply(_clean_date_str)
        new_df['prod_id'] = _get_col(df, ['상품ID', '상품번호'])
        new_df['vendor_prod_id'] = _get_col(df, ['상품관리코드', '옵션관리코드', '상품코드', '옵션코드']).astype(str).apply(_clean_vendor_prod_id)
        new_df['prod_name'] = _get_col(df, ['상품명'])
        new_df['order_amt'] = _get_col(df, ['주문금액', '거래금액', '결제금액'])
        new_df['ship_fee'] = _get_col(df, ['배송비합계', '배송비']).apply(_clamp_ship_fee)
        new_df['add_ship_fee'] = '0'
        new_df['qty'] = _get_col(df, ['주문건수', '수량'])
        new_df['sell_status'] = _get_col(df, ['주문상태', '상태'])
        new_df['option_name'] = _get_col(df, ['옵션', '옵션정보', '주문옵션', '상품옵션', '옵션명', '마켓상품옵션명'])
        new_df['recipient'] = _get_col(df, ['수령인', '받는사람', '수취인'])
        new_df['ship_address'] = _get_col(df, ['배송지', '주소', '수령인주소'])
        new_df['discount_amt'] = _get_col(df, ['할인금액'])
        new_df['settle_amt_dpj'] = _get_col(df, ['정산가'])
        new_df['recipient_phone'] = _get_col(df, ['수령인연락처', '수취인연락처'])
        new_df['zipcode'] = _get_col(df, ['우편번호'])
        new_df['raw_json'] = _raw_json_column(df)
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
        # 스펙 5.2가 가정하는 '합배송 3단계 구조'(원장주문코드 없는 빈 행이
        # 하위상품/합산행으로 낀다)가 지금도 실제 다운로드 파일에 있는지
        # 확인하려고 빈 원장주문코드 행 개수를 같이 세어둔다 - 이게 0이면
        # 그 3단계 구조 자체가 지금 안 내려온다는 뜻이라, 합배송 매입상태
        # 매칭이 안 맞는 문제의 원인일 수 있다.
        n_blank_oid = n_new = n_upd = 0
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
            # '신규 N건 · 갱신 0건'으로 항상 0건만 찍히던 게 실제 계산이 아니라
            # 그냥 하드코딩된 값이었다 - 원장주문코드가 있는 행(정체성이 있는
            # 단건/대표행)에 한해 이미 있던 행인지 먼저 확인해서 실제 신규/갱신
            # 건수를 센다. 원장주문코드가 빈 하위행/합산행은 정체성이 없어서
            # 항상 새로 쌓이는 게 정상이라 전부 신규로 센다.
            if oid:
                cur.execute("SELECT 1 FROM ownerclan_raw WHERE 원장주문코드=? AND 주문코드=?", (oid, jcode))
                is_update = cur.fetchone() is not None
            else:
                is_update = False
            vals = [oid] + row_vals
            cur.execute(f"""INSERT OR REPLACE INTO ownerclan_raw
                (원장주문코드, {', '.join(OWNERCLAN_COLS)}) VALUES ({', '.join(['?'] * (len(OWNERCLAN_COLS) + 1))})""", vals)
            if is_update:
                n_upd += 1
            else:
                n_new += 1
        conn.commit()
        conn.close()
        return {'type': '오너클랜 발주내역', 'inserted': n_new, 'updated': n_upd, 'blank_ledger_rows': n_blank_oid}

    df_map, source_type = detect_and_normalize(temp_df)
    if df_map is None or df_map.empty:
        conn.close()
        return {'error': '알 수 없는 파일 형식입니다 (다팔자/TOSS/오너클랜 서식이 아님)'}

    cur.execute("SELECT order_id FROM merged_orders")
    existing = {str(r[0]).strip() for r in cur.fetchall()}
    c_in = c_up = 0
    # 다팔자 파일에 '옵션'/'묶음번호' 컬럼을 우리가 추측한 이름으로 제대로
    # 찾고 있는지 확인용 진단 카운트 - 옵션 매칭이 이상하게 나올 때 이
    # 컬럼 자체를 못 찾고 있는 건 아닌지 로그만 보고 바로 알 수 있게 한다.
    n_option_filled = n_bundle_filled = n_status_blank = 0
    for _, row in df_map.iterrows():
        order_id = clean_id(row['order_id'])
        bundle_no = clean_id(row.get('bundle_no', ''))
        if not order_id:
            continue
        option_name = str(row.get('option_name', '')).strip()
        if option_name.lower() in ('nan', 'none', '-'):
            option_name = ''
        if option_name:
            n_option_filled += 1
        if bundle_no:
            n_bundle_filled += 1
        if bool(row.get('sell_status_was_blank', False)):
            n_status_blank += 1
        recipient = str(row.get('recipient', '')).strip()
        ship_address = str(row.get('ship_address', '')).strip()
        # 할인액은 실제 계산에 쓰이니 숫자로 깨끗하게 정리해서 저장한다
        # (빈칸/쿠폰 미사용 건은 0원). 정산가/연락처/우편번호는 참고·표시용
        # 원본 값이라 nan/none만 빈 문자열로 걸러서 그대로 둔다.
        discount_amt = str(int(safe_float(row.get('discount_amt', 0))))

        def _clean_ref(v):
            s = str(v if v is not None else '').strip()
            return '' if s.lower() in ('nan', 'none', '-') else s

        settle_amt_dpj = _clean_ref(row.get('settle_amt_dpj', ''))
        recipient_phone = _clean_ref(row.get('recipient_phone', ''))
        zipcode = _clean_ref(row.get('zipcode', ''))
        raw_json = str(row.get('raw_json', '') or '')
        if order_id in existing:
            cur.execute("""UPDATE merged_orders SET source=?, market=?, sell_status=?, order_date=?,
                prod_id=?, prod_name=?, qty=?, order_amt=?, ship_fee=?, vendor_prod_id=?,
                bundle_no=?, add_ship_fee=?, option_name=?, recipient=?, ship_address=?,
                discount_amt=?, settle_amt_dpj=?, recipient_phone=?, zipcode=?, raw_json=? WHERE order_id=?""", (
                str(row['source']), str(row['market']), str(row['sell_status']), str(row['order_date']),
                str(row['prod_id']), str(row['prod_name']), str(row['qty']), str(row['order_amt']),
                str(row['ship_fee']), str(row['vendor_prod_id']), bundle_no, str(row.get('add_ship_fee', '0')),
                option_name, recipient, ship_address,
                discount_amt, settle_amt_dpj, recipient_phone, zipcode, raw_json, order_id))
            c_up += 1
        else:
            cur.execute("""INSERT INTO merged_orders (order_id, source, market, sell_status, order_date,
                prod_id, prod_name, qty, order_amt, ship_fee, vendor_prod_id, margin_chk, bundle_no, add_ship_fee,
                option_name, recipient, ship_address, discount_amt, settle_amt_dpj, recipient_phone, zipcode, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'AUTO', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                order_id, str(row['source']), str(row['market']), str(row['sell_status']), str(row['order_date']),
                str(row['prod_id']), str(row['prod_name']), str(row['qty']), str(row['order_amt']),
                str(row['ship_fee']), str(row['vendor_prod_id']), bundle_no, str(row.get('add_ship_fee', '0')),
                option_name, recipient, ship_address, discount_amt, settle_amt_dpj, recipient_phone, zipcode, raw_json))
            existing.add(order_id)
            c_in += 1
    conn.commit()
    conn.close()
    # 할인액/정산가/연락처/우편번호가 계속 빈 값으로 나오는 게 우리 파싱
    # 문제인지, 아니면 이번에 실제로 받은 파일에 그 컬럼 자체가 없는건지
    # 바로 구분할 수 있게, 정규화된 헤더 기준으로 컬럼 존재 여부를 같이
    # 돌려준다(temp_df는 _normalize_header로 공백만 제거된 원본 헤더).
    raw_columns_found = {
        '할인금액': '할인금액' in temp_df.columns,
        '정산가': '정산가' in temp_df.columns,
        '수령인연락처': '수령인연락처' in temp_df.columns,
        '우편번호': '우편번호' in temp_df.columns,
    }
    return {'type': source_type, 'inserted': c_in, 'updated': c_up,
            'option_filled': n_option_filled, 'bundle_filled': n_bundle_filled,
            'status_blank': n_status_blank, 'total_rows': c_in + c_up,
            'raw_columns_found': raw_columns_found}


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
