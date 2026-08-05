# -*- coding: utf-8 -*-
"""
이유상점 Margin Board - 계산 엔진 (단일 소스)

원본 PyQt5 데스크톱 프로그램(app.py)의 수수료/매입매칭/마진판정 로직을 그대로 이식.
모든 화면(대시보드 표, 상단 실시간 합계, 요약 리포트, 캘린더)은 반드시 이 모듈의
compute_dataset() 결과만 사용한다 - 화면마다 계산을 따로 구현하지 않는다.
(원본에서 화면별로 계산이 따로 구현되어 광고비 할인 로직이 캘린더에서만 누락되는
사고가 있었음. 이 구조는 그 문제를 원천 차단하기 위한 것.)
"""
import sqlite3
import re
import json
from datetime import datetime

MARKETS = ["쿠팡", "네이버", "11번가", "지마켓", "옥션", "TOSS", "카카오"]

DEFAULT_FEES = {
    "쿠팡": ["자동매칭(11.55)", 0.0, 3.3, 0.0, 0, 0],
    "네이버": [2.73, 1.947, 0.0, 0.0, 0, 0],
    "11번가": [14.3, 0.0, 3.3, 0.0, 0, 0],
    "지마켓": [14.3, 0.0, 3.3, 0.0, 0, 0],
    "옥션": [14.3, 0.0, 3.3, 0.0, 0, 0],
    "TOSS": [8.8, 1.76, 1.76, 0.0, 8900, 2.0],
    "카카오": [3.3, 0.0, 0.0, 0.0, 0, 0],
}

COUPANG_FEE_DB = [
    ("순금", 4.4), ("골드바", 4.4), ("돌반지", 4.4), ("모니터", 4.95), ("컴퓨터", 5.5), ("PC", 5.5), ("태블릿", 5.5),
    ("에어컨", 6.38), ("냉장고", 6.38), ("세탁기", 6.38), ("TV", 6.38), ("프린터", 6.38), ("복사기", 6.38), ("스캐너", 6.38),
    ("쌀", 6.38), ("잡곡", 6.38), ("기저귀", 7.04), ("분유", 7.04), ("게임", 7.48), ("블랙박스", 7.48), ("하이패스", 7.48),
    ("내비게이션", 7.48), ("자전거", 8.36), ("골프", 8.36), ("가전", 8.58), ("디지털", 8.58), ("드론", 8.58), ("스마트기기", 8.58),
    ("물티슈", 9.02), ("뷰티", 10.56), ("화장품", 10.56), ("타이어", 10.56), ("휠", 10.56), ("출산", 11.0), ("유아", 11.0),
    ("자동차", 11.0), ("의류", 11.55), ("패션", 11.55), ("신발", 11.55), ("가방", 11.55), ("식품", 11.66), ("가구", 11.88),
    ("인테리어", 11.88), ("도서", 11.88), ("음반", 11.88), ("문구", 11.88), ("반려", 11.88), ("애완", 11.88), ("공구", 11.88),
    ("철물", 11.88), ("조명", 11.88), ("장난감", 11.88), ("주방", 11.88), ("칼", 11.88), ("냄비", 11.88), ("프라이팬", 11.88),
]

STATUS_CODE_MAP = {
    # 코드 1이 '신규주문'이 아니라 '배송준비'라는 걸 사용자가 실제 다팔자
    # 화면으로 직접 확인해서 알려줬다 - 이전 매핑은 처음부터 틀려있었다.
    # 진짜 신규주문은 코드 0이거나 빈 값으로 보이는데(_decode_status가 빈
    # 값은 이미 '신규주문'으로 처리하니 0도 명시적으로 맞춰둔다), 이 부분은
    # 아직 실제 신규주문 건으로 재확인이 안 된 상태라 추후 다시 틀렸다고
    # 나오면 바로 고쳐야 한다.
    '0': '신규주문', '1': '배송준비', '2': '배송중', '3': '배송완료', '4': '구매확정',
    '10': '취소요청', '11': '취소', '12': '취소철회',
    '20': '반품요청', '21': '반품', '22': '반품철회',
    '30': '교환요청', '31': '교환완료',
    '99': '발주취소',
    '배송준비(1)': '배송준비', '배송중(2)': '배송중', '배송완료(3)': '배송완료',
    '구매확정(4)': '구매확정', '취소요청(10)': '취소요청', '취소(11)': '취소', '취소철회(12)': '취소철회',
    '반품요청(20)': '반품요청', '반품(21)': '반품', '반품철회(22)': '반품철회',
    '교환요청(30)': '교환요청', '교환완료(31)': '교환완료', '발주취소(99)': '발주취소',
}


def clean_id(val):
    s = str(val).strip() if val is not None else ""
    if s.endswith('.0'):
        s = s[:-2]
    if s.lower() in ('nan', 'none', '-', '0', '0.0', ''):
        return ''
    return s


def safe_float(value):
    try:
        clean_value = re.sub(r'[^\d.-]', '', str(value))
        return float(clean_value) if clean_value else 0.0
    except Exception:
        return 0.0


def fetch_coupang_fee_rate(prod_name):
    matched = [(rate, kw) for kw, rate in COUPANG_FEE_DB if kw in prod_name]
    if matched:
        matched.sort(key=lambda x: x[0], reverse=True)
        return matched[0][0]
    return None


def load_fees_config(fees_path):
    try:
        with open(fees_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return dict(DEFAULT_FEES)


def save_fees_config(fees_path, fees_dict):
    with open(fees_path, 'w', encoding='utf-8') as f:
        json.dump(fees_dict, f, ensure_ascii=False, indent=4)


def _get_market_fees(market_name, source_name, fees_config):
    f1, f2, f3, f4, threshold, extra = 11.55, 3.3, 3.3, 0.0, 0, 0
    for m_key, rates in fees_config.items():
        if not isinstance(rates, list):
            continue
        if m_key in market_name or m_key in source_name or (m_key == '네이버' and '스마트스토어' in market_name):
            f1 = safe_float(rates[0]) if "자동" not in str(rates[0]) else 11.55
            f2 = float(rates[1]) if len(rates) > 1 else 0.0
            f3 = float(rates[2]) if len(rates) > 2 else 0.0
            f4 = float(rates[3]) if len(rates) > 3 else 0.0
            threshold = safe_float(rates[4]) if len(rates) > 4 else 0
            extra = safe_float(rates[5]) if len(rates) > 5 else 0
            break
    return f1, f2, f3, f4, threshold, extra


def compute_fee(order_amt, ship_fee, add_ship_fee, market_name, source_name, prod_name, fees_config, ad_chk):
    """단일 진실 공급원: 판매수수료 계산 공식 (스펙 5.3)"""
    f1, f2, f3, f4, threshold, extra = _get_market_fees(market_name, source_name, fees_config)
    base_f1 = f1

    if "쿠팡" in market_name or "쿠팡" in source_name:
        matched_rate = fetch_coupang_fee_rate(prod_name)
        if matched_rate is not None:
            f1 = matched_rate

    if threshold > 0 and order_amt >= threshold:
        f1 += extra

    if ad_chk == 'Y':
        f1 = max(0.0, f1 - base_f1)

    total_sales = order_amt + ship_fee + add_ship_fee
    fee = int((order_amt * (f1 + f2) / 100.0) + ((ship_fee + add_ship_fee) * f3 / 100.0) - (total_sales * f4 / 100.0))
    return fee, f1, f2


def _build_purchase_dict(cur):
    cur.execute("""SELECT order_id, vendor_prod_id, buy_cost, buy_ship_fee, buy_total, buy_status
        FROM purchase_ledger
        ORDER BY CASE WHEN buy_status NOT LIKE '%취소%' AND buy_status NOT LIKE '%반품%'
                       AND buy_status NOT LIKE '%환불%' THEN 0 ELSE 1 END, rowid DESC""")
    purchase_dict = {}
    for pr in cur.fetchall():
        oid = clean_id(pr[0])
        if oid and oid not in purchase_dict:
            purchase_dict[oid] = ('HL', {
                'vendor_prod_id': pr[1], 'buy_cost': safe_float(pr[2]),
                'buy_ship_fee': safe_float(pr[3]), 'buy_total': safe_float(pr[4]), 'status': pr[5] or '',
            })
    return purchase_dict


def _build_owner_matches(cur, purchase_dict):
    """오너클랜 발주내역 3단계 구조 스캔 (스펙 5.2).
    단건(원장주문코드=w_id가 판매측 order_id와 그대로 일치하는 발주 관례) 매칭 결과는
    HL과 동일한 purchase_dict에 직접 병합한다 (HL 매칭이 있으면 덮어쓰지 않음) -
    스펙 5.2의 ①/② 우선순위가 원래 하나의 조회로 합쳐져 있는 구조를 그대로 재현.

    '합배송' 판정은 판매측 bundle_no(합배송코드)와 완전히 무관하다(사용자 지시:
    "합배송코드를 합배송 필터링하는데 사용하지 말라고 했잖아") - 오직 이 스캔이
    찾아낸 원장주문코드 3단계 구조만 기준으로 삼는다. 원장주문코드가 비어있는
    행(하위행/합산행)은 그 자체로 이미 합배송 구조의 일부이므로(사용자 지시:
    "원장주문코드가 비어있는 행은 무조건 합배송의 일부"), 대표행+합산행
    패턴으로 매입가가 확정된 건은 하위행 개수와 상관없이 전부 owner_group_of에
    기록해 반환한다. 매칭도 여기서 order_id로 직접 purchase_dict에 병합해두는
    것만 쓴다 - bundle_no를 거쳐서 매칭을 찾는 경로는 두지 않는다."""
    cur.execute("SELECT 원장주문코드, 주문코드, 상품코드, 상품가격, 배송비, 배송상태 FROM ownerclan_raw ORDER BY rowid ASC")
    raw_owner = cur.fetchall()

    owner_group_of = {}
    current_bundle_ids = []
    main_w_id = ""

    def _merge(oid_key, data):
        if oid_key not in purchase_dict or purchase_dict[oid_key][0] != 'HL':
            purchase_dict[oid_key] = ('OWNER', data)

    for pr in raw_owner:
        w_id = clean_id(pr[0])
        price = safe_float(pr[3])
        ship = safe_float(pr[4])
        total_buy = price + ship
        prod_code = str(pr[2] or '').strip()
        status = str(pr[5] or '')
        is_cancel = any(kw in status for kw in ['취소', '반품', '환불', '품절', '수거중']) and '철회' not in status

        if is_cancel:
            # 취소/반품/환불/품절/수거중 상태 행은 매칭 대상에서 완전히
            # 제외한다(스펙 5.2) - 예전엔 이 조건이 1단계/3단계 판별 조건에
            # 같이 섞여 있어서, 취소된 단건 주문이 '합배송 대표행'으로,
            # 취소된 합산행이 '하위행'으로 잘못 편입되는 사고가 있었다.
            # 그냥 건너뛴다(진행 중이던 합배송 스캔 상태는 그대로 유지).
            continue

        if w_id:
            if total_buy > 0:
                data = {'vendor_prod_id': prod_code, 'buy_cost': price, 'buy_ship_fee': ship,
                        'buy_total': total_buy, 'status': status}
                _merge(w_id, data)
                current_bundle_ids = []
                main_w_id = ""
            else:
                main_w_id = w_id
                current_bundle_ids = [main_w_id]
        else:
            if total_buy > 0 and main_w_id:
                data = {'vendor_prod_id': prod_code, 'buy_cost': price, 'buy_ship_fee': ship,
                        'buy_total': total_buy, 'status': status}
                for saved_id in current_bundle_ids:
                    _merge(saved_id, data)
                # 원장주문코드가 비어있는 행(하위행/합산행)은 그 자체로 이미
                # 합배송 3단계 구조의 일부다(사용자 지시) - 하위행이 몇 개
                # 딸려있는지와 상관없이, 대표행+합산행 패턴으로 매입가가
                # 확정된 건은 전부 이 구조에 속한 것으로 취급한다.
                for saved_id in current_bundle_ids:
                    owner_group_of[saved_id] = main_w_id
                current_bundle_ids = []
                main_w_id = ""
            else:
                sub_id = clean_id(pr[1])
                if sub_id and sub_id not in current_bundle_ids:
                    current_bundle_ids.append(sub_id)

    return owner_group_of


def compute_dataset(db_path, fees_path):
    """전체 주문 데이터를 매입 매칭 + 수수료/마진 계산까지 완료한 리스트로 반환.
    (order_date 기준 내림차순 - 원본과 동일한 처리 순서)"""
    fees_config = load_fees_config(fees_path)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM merged_orders WHERE order_date != '' ORDER BY order_date DESC")
    columns = [d[0] for d in cur.description]
    idx = {c: i for i, c in enumerate(columns)}
    raw_rows = cur.fetchall()

    purchase_dict = _build_purchase_dict(cur)
    owner_group_of = _build_owner_matches(cur, purchase_dict)
    stock_map = {}
    try:
        cur.execute("SELECT vendor_prod_id, option_name, status FROM stock_check")
        stock_map = {(row[0], row[1] or ''): row[2] for row in cur.fetchall()}
    except sqlite3.OperationalError:
        pass  # 아직 stock_check 테이블이 없는 예전 DB일 수 있음
    conn.close()

    # group_id(합배송 판정/매입가 중복배정 방지 기준)는 오직 오너클랜
    # 원장주문코드 그룹(owner_group_of)만 쓴다. bundle_no(합배송코드)는
    # 화면에 참고용으로만 보여주는 필드일 뿐, 그룹핑/매칭 어디에도 관여시키지
    # 않는다(사용자 지시: "합배송코드를 합배송 필터링하는데 사용하지 말라고
    # 했잖아" - bundle_no로 형제를 묶어서 매입가를 나눠 물려주는 것도 결국
    # bundle_no를 매칭 판단에 쓰는 것이라 금지 대상에 포함됨). 원장주문코드
    # 그룹이 없는 주문은 그냥 자기 자신만의 그룹이 된다 - 실제로 매칭되는
    # 오너클랜 데이터가 없으면 미매입으로 남는 게 맞다.
    oid_to_owner_key = {}
    for r in raw_rows:
        oid = clean_id(r[idx['order_id']])
        if oid and oid in owner_group_of:
            oid_to_owner_key[oid] = f"OWNERGRP:{owner_group_of[oid]}"

    # 보조 매칭: 오너클랜 발주내역에 원장주문코드 흔적이 아예 없는 합배송 건을
    # 위한 규칙(사용자가 실제 사례로 확인해서 확정) - 같은 판매사상품코드 +
    # 같은 수령인 + 같은 배송지를 가진 주문들 중, 시간 차이가 90초 이내인
    # 것들만 같은 그룹으로 묶는다(같은 날짜 전체를 묶으면 너무 넓어서
    # 무관한 주문까지 묶일 위험이 있다는 지적으로 좁혔고, 60초는 실제
    # 사례(65초차) 하나를 놓쳐서 90초로 확정함).
    # bundle_no는 여기서도 전혀 안 쓴다.
    if 'recipient' in idx and 'ship_address' in idx:
        def _parse_dt(s):
            try:
                return datetime.strptime(str(s or '').strip()[:19], '%Y-%m-%d %H:%M:%S')
            except Exception:
                return None

        pra_groups = {}
        for r in raw_rows:
            oid = clean_id(r[idx['order_id']])
            if not oid:
                continue
            recipient = str(r[idx['recipient']] or '').strip()
            ship_address = str(r[idx['ship_address']] or '').strip()
            vendor_prod_id = str(r[idx['vendor_prod_id']] or '').strip()
            dt = _parse_dt(r[idx['order_date']])
            if not (recipient and ship_address and vendor_prod_id and dt):
                continue
            key = (vendor_prod_id, recipient, ship_address)
            pra_groups.setdefault(key, []).append((oid, dt))

        # '매칭됨'은 owner_group_of(②+③ 다단계 패턴)뿐 아니라 ①(단건 즉시
        # 확정) 패턴이나 HL 매칭까지 전부 포함해야 한다 - 실제 사례(4410119815)가
        # 원장주문코드=자기 order_id에 총결제금액이 바로 찍힌 ① 단건 패턴이라,
        # owner_group_of에는 안 들어가고 purchase_dict에만 직접 들어있었다.
        for items in pra_groups.values():
            anchors = [(o, dt) for o, dt in items if o in oid_to_owner_key or o in purchase_dict]
            if not anchors:
                continue
            for o2, dt2 in items:
                if o2 in oid_to_owner_key:
                    continue
                for anchor_oid, anchor_dt in anchors:
                    if abs((dt2 - anchor_dt).total_seconds()) <= 90:
                        oid_to_owner_key[o2] = oid_to_owner_key.get(anchor_oid, anchor_oid)
                        break

    order_counts = {}
    prelim = []
    for r in raw_rows:
        oid = clean_id(r[idx['order_id']])
        if not oid or '수정' in oid or '불가' in oid:
            continue
        b_no = clean_id(r[idx['bundle_no']])
        group_id = oid_to_owner_key.get(oid, oid)
        order_counts[group_id] = order_counts.get(group_id, 0) + 1
        prelim.append((oid, b_no, group_id, r))

    # 1차 패스: 매입 매칭 우선순위 (스펙 5.2) + 묶음당 매입가 1회만 배정.
    # 매칭은 오직 order_id(원장주문코드 그룹으로 이미 병합된 purchase_dict)로만
    # 한다 - bundle_no를 거쳐서 매칭을 찾는 경로는 전부 제거했다(사용자 지시:
    # bundle_no는 매칭/그룹핑 어디에도 쓰면 안 됨).
    bundle_buy_assigned = set()
    bundle_has_purchase = set()
    # group_id 단위 실제 매입 정보 - 그룹 안에 매입 매칭이 없는 형제 행(수령인/
    # 배송지로 묶인 미매입 건 등)이 2차 패스에서 먼저 처리되면 그 행의 매입가
    # 0을 그룹 대표값으로 잘못 써버리는 사고가 있었다 - 그룹의 진짜 매입 정보를
    # 여기 별도로 저장해두고 2차 패스가 처리 순서와 무관하게 이 값을 쓰게 한다.
    group_buy_data = {}
    matched_rows = []
    for oid, b_no, group_id, r in prelim:
        ptype, data = None, None
        if oid in purchase_dict:
            ptype, data = purchase_dict[oid]

        buy_cost = buy_ship = buy_total = 0.0
        buy_status = ''
        vendor_prod_id = str(r[idx['vendor_prod_id']] or '')
        if ptype:
            if group_id not in bundle_buy_assigned:
                buy_cost, buy_ship, buy_total = data['buy_cost'], data['buy_ship_fee'], data['buy_total']
                bundle_buy_assigned.add(group_id)
                group_buy_data[group_id] = (buy_cost, buy_ship)
                if buy_total > 0:
                    bundle_has_purchase.add(group_id)
            buy_status = data['status']
            if ptype == 'HL' and '[HL]' not in buy_status:
                buy_status = f"[HL] {buy_status}" if buy_status else "[HL]"
            if ptype == 'OWNER' and data.get('vendor_prod_id'):
                vendor_prod_id = data['vendor_prod_id']

        matched_rows.append({
            'oid': oid, 'b_no': b_no, 'group_id': group_id, 'r': r,
            'buy_cost': buy_cost, 'buy_ship': buy_ship, 'buy_total': buy_total,
            'buy_status': buy_status, 'vendor_prod_id': vendor_prod_id,
        })

    # 2차 패스: 수수료/마진 계산 + 마진포함 판정 (스펙 5.3, 5.4, 5.5)
    processed_bundles = set()
    result = []
    for m in matched_rows:
        r, idx_ = m['r'], idx
        oid, b_no, group_id = m['oid'], m['b_no'], m['group_id']

        sell_status = str(r[idx_['sell_status']] or '').strip()
        if sell_status.endswith('.0'):
            sell_status = sell_status[:-2]
        if sell_status.lower() in ('nan', 'none', ''):
            sell_status = '신규주문'
        elif sell_status in STATUS_CODE_MAP:
            sell_status = STATUS_CODE_MAP[sell_status]

        market_name = str(r[idx_['market']] or '')
        source_name = str(r[idx_['source']] or '')
        prod_name = str(r[idx_['prod_name']] or '').strip()
        order_amt = int(safe_float(r[idx_['order_amt']]))
        ship_fee = int(safe_float(r[idx_['ship_fee']]))
        if ship_fee > 500000:
            ship_fee = 0
        add_ship_fee = int(safe_float(r[idx_['add_ship_fee']]))
        if add_ship_fee > 500000:
            add_ship_fee = 0
        total_sales = order_amt + ship_fee + add_ship_fee

        ad_chk = str(r[idx_['ad_chk']] or 'N') if 'ad_chk' in idx_ else 'N'
        fee_amt, disp_f1, disp_f2 = compute_fee(order_amt, ship_fee, add_ship_fee, market_name, source_name,
                                                 prod_name, fees_config, ad_chk)
        settle_amt = int(total_sales - fee_amt)

        margin_chk = str(r[idx_['margin_chk']] or 'AUTO') if 'margin_chk' in idx_ else 'AUTO'
        status_combined = m['buy_status'] + sell_status
        is_returned = any(k in status_combined for k in ["반품", "수거중"]) and "철회" not in status_combined
        is_cancelled_other = any(k in status_combined for k in ["취소", "환불", "품절", "발주취소"]) and "철회" not in status_combined
        is_cancelled = is_returned or is_cancelled_other
        is_bundled = order_counts.get(group_id, 0) > 1

        if margin_chk == 'Y':
            is_included = True
        elif margin_chk == 'N':
            is_included = False
        else:
            is_included = (not is_cancelled) and (group_id in bundle_has_purchase)

        display_buy_cost = display_buy_ship = 0
        if is_included and not is_cancelled:
            if group_id not in processed_bundles:
                display_buy_cost, display_buy_ship = group_buy_data.get(group_id, (0, 0))
                processed_bundles.add(group_id)

        margin_amt = None
        margin_rate = None
        margin_label = None
        if is_included:
            margin_amt = int(total_sales - fee_amt - display_buy_cost - display_buy_ship)
            margin_rate = round((margin_amt / total_sales * 100.0), 1) if total_sales > 0 else 0.0
        else:
            margin_label = "반품(제외)" if is_returned else ("취소(제외)" if is_cancelled_other else "미매입(제외)")

        is_toss = 'TOSS' in market_name or 'TOSS' in source_name

        result.append({
            'order_id': oid,
            'bundle_no': b_no,
            'source': source_name,
            'market': market_name,
            'sell_status': sell_status,
            'buy_status': m['buy_status'] or '',
            'order_date': str(r[idx_['order_date']] or ''),
            'prod_id': str(r[idx_['prod_id']] or ''),
            'prod_name': prod_name,
            'qty': str(r[idx_['qty']] or ''),
            'order_amt': order_amt,
            'ship_fee': ship_fee,
            'add_ship_fee': add_ship_fee,
            'total_sales': total_sales,
            'fee_rate_display': f"{(disp_f1 + disp_f2):.2f}%",
            'fee_amt': fee_amt,
            'settle_amt': settle_amt,
            'vendor_prod_id': m['vendor_prod_id'],
            'option_name': str(r[idx_['option_name']] or '') if 'option_name' in idx_ else '',
            # 재고상태는 상품+옵션 단위로 캐시돼있어서, 같은 상품을 산 취소된
            # 주문 건도 캐시 키가 같으면 화면에 그대로 보였다 - 취소건까지
            # 품절/정상이 찍혀 보여서 헷갈린다는 지적이 있었다. STOCK 확인
            # 대상 자체가 '신규주문' 건으로 한정돼있으니(app.py), 표시도
            # 똑같이 신규주문 건에만 보이게 맞춘다.
            'stock_status': (stock_map.get(
                (m['vendor_prod_id'], str(r[idx_['option_name']] or '') if 'option_name' in idx_ else ''), '')
                if sell_status == '신규주문' else ''),
            'buy_cost': int(display_buy_cost),
            'buy_ship_fee': int(display_buy_ship),
            'buy_total': int(display_buy_cost + display_buy_ship),
            'margin_amt': margin_amt,
            'margin_rate': margin_rate,
            'margin_label': margin_label,
            'margin_chk': margin_chk,
            'is_included': is_included,
            'ad_chk': ad_chk,
            'is_toss': is_toss,
            'is_bundled': is_bundled,
            'is_cancelled': is_cancelled,
            'is_returned': is_returned,
            'group_id': group_id,
        })

    return result


def summarize(rows):
    """KPI 5박스 + 마켓별 요약 + 도넛차트 데이터 (필터링 이전/이후 어디에든 사용 가능)"""
    sales = fees = costs = margin = base = 0
    market_totals = {m: {'sales': 0, 'fee': 0, 'cost': 0, 'margin': 0, 'base': 0} for m in MARKETS}
    for row in rows:
        if not row['is_included']:
            continue
        sales += row['total_sales']
        fees += row['fee_amt']
        costs += row['buy_cost'] + row['buy_ship_fee']
        margin += row['margin_amt']
        base += row['total_sales']
        for mk in MARKETS:
            if mk in row['market'] or mk in row['source']:
                mt = market_totals[mk]
                mt['sales'] += row['total_sales']
                mt['fee'] += row['fee_amt']
                mt['cost'] += row['buy_cost'] + row['buy_ship_fee']
                mt['margin'] += row['margin_amt']
                mt['base'] += row['total_sales']
                break
    rate = round((margin / base * 100.0), 1) if base > 0 else 0.0
    market_rows = []
    for mk in MARKETS:
        mt = market_totals[mk]
        mrate = round((mt['margin'] / mt['base'] * 100.0), 1) if mt['base'] > 0 else 0.0
        market_rows.append({'market': mk, **mt, 'rate': mrate})
    return {
        'sales': sales, 'fees': fees, 'costs': costs, 'margin': margin, 'rate': rate,
        'market_rows': market_rows,
        'donut': [{'market': mr['market'], 'value': mr['margin']} for mr in market_rows if mr['margin'] > 0],
    }


def daily_aggregate(rows):
    """캘린더용 일별 매출/마진/건수 집계 (마진포함 판정된 주문만, 스펙 5.6)"""
    daily = {}
    for row in rows:
        if not row['is_included']:
            continue
        date_str = row['order_date']
        if not date_str:
            continue
        day = date_str.split(' ')[0].replace('.', '-').replace('/', '-')
        parts = day.split('-')
        if len(parts) < 3:
            continue
        try:
            y, mo, d = int(parts[0]), int(parts[1]), int(parts[2])
            key = f"{y:04d}-{mo:02d}-{d:02d}"
        except ValueError:
            continue
        agg = daily.setdefault(key, {'sales': 0, 'margin': 0, 'count': 0})
        agg['sales'] += row['total_sales']
        agg['margin'] += row['margin_amt'] or 0
        agg['count'] += 1
    return daily


def apply_filters(rows, year=None, month=None, market=None, search=None,
                   hl_only=False, unpurchased_only=False, bundle_only=False):
    out = rows
    if year and year != '전체':
        out = [r for r in out if year in r['order_date']]
    if month and month != '전체':
        try:
            mm = f"-{int(month):02d}-"
        except ValueError:
            mm = None
        if mm:
            out = [r for r in out if mm in r['order_date'].replace('.', '-')]
    if market and market != '전체':
        out = [r for r in out if market in r['market'] or market in r['source']]
    if search:
        s = search.strip().lower()
        if s:
            out = [r for r in out if s in (r['prod_name'] or '').lower()
                   or s in (r['order_id'] or '').lower()
                   or s in (r['prod_id'] or '').lower()
                   or s in (r['vendor_prod_id'] or '').lower()]
    if hl_only:
        out = [r for r in out if '[HL]' in (r['buy_status'] or '')]
    if unpurchased_only:
        out = [r for r in out if r['margin_label'] and '미매입' in r['margin_label']]
    if bundle_only:
        # 합배송 판정은 판매측 bundle_no(합배송코드)가 아니라 오너클랜
        # 원장주문코드 3단계 구조에서 실제로 묶인 그룹인지를 기준으로 한다
        # (사용자 지시: "합배송코드는 그냥 참고용" - 원장주문코드 기준으로
        # compute_dataset()의 group_id/is_bundled가 이미 이렇게 계산돼있으니
        # 그 결과를 그대로 쓴다).
        out = [r for r in out if r['is_bundled']]
    return out
