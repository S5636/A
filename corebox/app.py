# -*- coding: utf-8 -*-
"""COREBOX - 자주 쓰는 서류를 한 곳에 모아두는 보관함. 연도/문서종류별로
숫자가 있는 서류(소득금액증명 등)는 자동으로 연도별 집계표를 보여준다."""
import datetime
import difflib
import hashlib
import json
import os
import re
import uuid
from io import BytesIO

from flask import Flask, jsonify, make_response, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

import models

app = Flask(__name__)
models.init_db()

_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic")
_OCR_READER = None


def _get_ocr_reader():
    """easyocr Reader는 생성(모델 로딩)이 느려서 한 번만 만들고 재사용한다."""
    global _OCR_READER
    if _OCR_READER is None:
        import easyocr
        _OCR_READER = easyocr.Reader(["ko", "en"], gpu=False)
    return _OCR_READER


def _extract_text_from_image(file_bytes):
    from PIL import Image
    import numpy as np
    try:
        pil_img = Image.open(BytesIO(file_bytes)).convert("RGB")
        target_width = 1600
        if pil_img.width < target_width:
            scale = target_width / pil_img.width
            resample = getattr(Image, "Resampling", Image).LANCZOS
            pil_img = pil_img.resize((target_width, round(pil_img.height * scale)), resample)
        ocr_input = np.array(pil_img)
    except Exception:
        ocr_input = file_bytes
    reader = _get_ocr_reader()
    detections = reader.readtext(ocr_input, paragraph=False)
    return "\n".join(d[1] for d in detections)


def _extract_text_from_pdf(file_bytes):
    """pypdf로 PDF 안에 글자정보가 있는 페이지의 텍스트를 뽑는다. 스캔해서
    이미지로만 들어있는 PDF(글자정보 자체가 없음)는 이 방법으로 못 읽는다 -
    그런 경우 빈 문자열이 나오고, 서류명/분류를 사용자가 직접 채워야 한다."""
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(file_bytes))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _extract_text_from_file(filename, file_bytes):
    ext = os.path.splitext(filename.lower())[1]
    try:
        if ext == ".pdf":
            return _extract_text_from_pdf(file_bytes)
        if ext in _IMAGE_EXTS:
            return _extract_text_from_image(file_bytes)
    except Exception:
        return ""
    return ""


# (화면에 보여줄 이름, 집계/분류에 쓸 doc_type, 글자에서 찾을 키워드들) -
# 사용자가 실제로 보관하려는 서류 종류가 늘어나면 여기 추가하면 된다.
_DOC_TYPE_RULES = [
    # "소득금액증명" 안에도 "종합소득세 신고(결정·경정) 현황"이라는
    # 소제목이 들어있어서, 종합소득세 신고서보다 반드시 먼저 확인해야
    # 소득금액증명이 종합소득세 신고서로 잘못 분류되지 않는다.
    ("소득금액증명", ["소득금액증명"]),
    ("부가가치세 과세표준증명", ["과세표준증명"]),
    ("부가가치세 신고서", ["부가가치세", "부가세"]),
    ("종합소득세 신고서", ["종합소득세"]),
    ("사업자등록증", ["사업자등록증"]),
    ("통신판매업 신고증", ["통신판매업"]),
    ("건강기능식품 영업 신고증", ["건강기능식품"]),
    ("의료기기 판매업 신고증", ["의료기기"]),
    # "통장사본"이라는 글자는 사람이 그 서류를 부르는 말일 뿐 실제
    # 통장 사진/스캔본 안에는 안 찍혀있다(실물 통장 사진으로 직접
    # 확인함) - 대신 어느 은행 통장이든 공통으로 있는 "예금주"/
    # "계좌번호"/"예금종류" 같은 항목명으로 알아본다.
    ("사업자 통장 사본", ["통장사본", "통장 사본", "예금주", "계좌번호", "예금종류"]),
]

# 사업자등록증/신고증류는 한 장짜리 보관용 서류라 발급 시점의 연도를
# 붙이는 게 의미가 없다(사업자등록증은 "2015년 사업자등록증"처럼 안
# 부름) - 이 서류들은 연도/기간을 아예 안 붙이고 그냥 이름 그대로
# 보관만 한다. 연도별 집계표에도 안 뜬다(그 표는 year IS NOT NULL만 봄).
_NO_YEAR_DOC_TYPES = {
    "사업자등록증", "통신판매업 신고증", "건강기능식품 영업 신고증",
    "의료기기 판매업 신고증", "사업자 통장 사본",
}

# doc_type별로 "금액" 칸에 뽑을 라벨(소득금액증명은 수입/소득 2개를 따로
# 보여주니 여기선 제외) - 서류에 없는 라벨이면 그냥 못 찾고 넘어간다.
_GENERIC_AMOUNT_LABELS = {
    "부가가치세 과세표준증명": ["과세표준액", "공급가액"],
    "부가가치세 신고서": ["과세표준", "공급가액"],
}


def _classify_text(text):
    """국세청 서류는 제목을 "소 득 금 액 증 명"처럼 글자 사이를 띄워서
    뽑히는 경우가 많아(가운데 정렬용 레이아웃), 공백을 다 지운 텍스트로
    비교한다."""
    compact = re.sub(r"\s+", "", text)
    for label, keywords in _DOC_TYPE_RULES:
        if any(k in compact for k in keywords):
            return label
    return ""


def _extract_year_from_filename(filename):
    """"부가세과세표준증명원_22년1기_230102.pdf"처럼 홈택스에서 받은
    파일은 파일명 자체에 귀속연도가 2자리로 박혀있는 경우가 많다(발급일
    "230102"는 YYMMDD라 "20XX년"으로는 안 걸림). "22년"(2자리)이나
    "2022년"(4자리) 둘 다 본다."""
    m = re.search(r"(20\d{2})년", filename)
    if m:
        return int(m.group(1))
    m = re.search(r"(?<!\d)(\d{2})년", filename)
    if m:
        return 2000 + int(m.group(1))
    return None


def _extract_year(text, filename):
    """연도 판단 우선순위: 본문의 "귀속"/"과세기간" 라벨 > 파일명 > 본문에
    처음 나오는 "20XX년". 마지막 순위를 제일 느슨하게 둔 이유는 "발급일자
    2023년..."처럼 귀속연도가 아닌 다른 날짜가 먼저 걸릴 위험이 있어서다
    (실제로 22년1기 서류가 발급일자 때문에 2023년으로 잘못 분류된 사고가
    있었음) - 그래서 그 경우를 막아줄 파일명 쪽을 본문 느슨한 매칭보다
    먼저 본다."""
    m = re.search(r"(?:귀속|과세기간)\D{0,10}?(20\d{2})", text)
    if m:
        return int(m.group(1))
    from_filename = _extract_year_from_filename(filename)
    if from_filename:
        return from_filename
    m = re.search(r"(20\d{2})\s*년", text)
    if m:
        return int(m.group(1))
    return None


_DATE_RE = r"(20\d{2})\s*[.\-년]\s*(\d{1,2})\s*[.\-월]\s*(\d{1,2})\s*일?"


def _extract_date_near_label(text, labels):
    """라벨(예: "발급일자") 뒤 20자 이내에 나오는 "2023.01.02"/"2023 년
    1 월 2 일"(국세청 서류는 숫자랑 년/월/일 사이에 띄어쓰기가 들어가는
    경우가 많음) 모양 날짜를 찾아 YYYY-MM-DD로 돌려준다. 못 찾으면 None."""
    for label in labels:
        m = re.search(re.escape(label) + r"\D{0,20}?" + _DATE_RE, text)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


def _extract_issue_date_fallback(text, exclude_dates=()):
    """"발급일자" 같은 라벨 없이 그냥 세무서장 직인 옆에 날짜만 단독으로
    찍히는 서류가 있다(부가가치세과세표준증명 등). 그런 경우 문서 안의
    모든 날짜 중 과세기간 표에 이미 쓰인 날짜(exclude_dates)가 아니면서
    가장 마지막에 나오는 날짜를 발급일로 본다 - 보통 서류 맨 끝에
    "증명합니다" 뒤에 발급일, 발급기관 순서로 나오기 때문."""
    last = None
    for m in re.finditer(_DATE_RE, text):
        date_str = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        if date_str in exclude_dates:
            continue
        last = date_str
    return last


def _extract_signing_date(text):
    """종합소득세 신고서는 서류 끝의 "2026년 05월 24일 신고인 OOO
    (서명 또는 인)"에 실제로 신고한 날짜가 찍힌다. 11페이지짜리 서류라
    뒤쪽 첨부서식에 양식 개정일("<개정 2018.3.21.>" 같은, 신고일과
    전혀 상관없는 날짜)이 섞여 있어서, "문서 전체에서 제일 마지막 날짜"
    방식(_extract_issue_date_fallback)을 쓰면 그 개정일을 잘못 집는다.
    "신고인" 바로 앞 날짜만 정확히 찍어서 피한다."""
    m = re.search(_DATE_RE + r"\s*신고인", text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


def _extract_period(text):
    """"과세기간 2022.01.01 ~ 2022.06.30"처럼 기간 전체(시작~끝)가 있으면
    그대로 "YYYY-MM-DD~YYYY-MM-DD"로 돌려준다. 날짜가 하나만 걸리면(기간이
    아니라 단일 시점) 기간으로 보지 않고 None - 발급일자랑 헷갈리면 안 됨."""
    m = re.search(
        r"(?:과세기간|귀속기간)\D{0,10}?" + _DATE_RE + r"\D{0,5}?~\D{0,5}?" + _DATE_RE,
        text,
    )
    if m:
        start = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        end = f"{m.group(4)}-{int(m.group(5)):02d}-{int(m.group(6)):02d}"
        return f"{start}~{end}"
    return None


def _extract_amount_near_label(text, label):
    """"수입금액 48,956,333원"처럼 라벨 뒤 15자 이내에 나오는 숫자를
    뽑는다(음수 소득금액 "-37,693,734"도 있어서 부호 포함). 콤마 위치가
    OCR로 깨질 수 있어 완벽하진 않다 - 틀리면 사용자가 수정 모달에서
    직접 고치는 걸 전제로 한다. "소득금액" 라벨은 "소득금액증명"(서류
    제목 자체)에도 들어있어서, 라벨 바로 뒤가 "증명"이면 제목을 잘못
    집어온 거니 건너뛴다(negative lookahead)."""
    m = re.search(re.escape(label) + r"(?!증명)\D{0,15}?(-?[\d,]{4,})", text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _extract_income_row(text, label):
    """소득금액증명의 "수입금액 0 0 72,934,754 27,190,718 0 0 100,125,472"
    행은 칸이 "이자 배당 사업 근로 연금 기타 합계" 순서로 고정돼 있다.
    합계(제일 중요한 숫자, 맨 뒤 칸)만 보여주다가 사업/근로/기타소득별
    세부 금액도 따로 보여달라는 요청으로 7칸을 다 파싱한다. 칸 수가
    7개가 아니면(서류 서식이 다르거나 OCR이 깨졌을 때) 세부는 포기하고
    합계(마지막 숫자)만이라도 건진다. 라벨과 숫자 사이가 표 칸 정렬용
    공백으로 멀리 떨어져 있어 라벨 바로 뒤가 아니라 같은 줄 전체에서
    숫자를 찾는다. 숫자가 없는 줄(용어설명 등)은 건너뛰고 다음 줄을 본다."""
    for line in text.split("\n"):
        label_pos = line.find(label)
        if label_pos < 0:
            continue
        # pypdf로 뽑으면 "소득금액" 줄 앞에 결정세액(다른 칸, 두 행에
        # 걸친 병합 셀)이 줄바꿈 없이 그대로 붙어 나올 때가 있다
        # ("418,790소득금액 0 0 ..."). 라벨보다 앞쪽 숫자는 그 다른 칸
        # 값이니 무시하고, 라벨 뒤쪽만 본다.
        after_label = line[label_pos + len(label):]
        # 사업/근로 등 칸이 0원이면 그냥 "0" 한 글자로만 찍혀서, 자릿수가
        # 많은 숫자만 잡던 기존 패턴({4,})으로는 그 칸 자체가 안 잡혀
        # 7칸이 안 맞는다 - 이 줄 안에서는 이미 라벨로 맞는 행인 게
        # 확실하니 짧은 숫자도 다 잡는다.
        numbers = re.findall(r"-?[\d,]+", after_label)
        numbers = [n for n in numbers if n.strip(",")]
        if not numbers:
            continue
        try:
            values = [float(n.replace(",", "")) for n in numbers]
        except ValueError:
            continue
        if len(values) == 7:
            interest, dividend, business, wage, pension, other, total = values
            return {"total": total, "business": business, "wage": wage, "other": other}
        return {"total": values[-1], "business": None, "wage": None, "other": None}
    return None


def _extract_generic_amount(doc_type, text):
    for label in _GENERIC_AMOUNT_LABELS.get(doc_type, []):
        amount = _extract_amount_near_label(text, label)
        if amount is not None:
            return amount
    return None


def _extract_jongso_amounts(text):
    """종합소득세 신고서의 "❹ 세액의 계산" 표는 줄마다 상자번호(19, 21,
    28, 37...)가 숫자 바로 앞에 붙어있다. 라벨 자체는 "종합소득" / "액
    금"처럼 세로로 여러 줄에 걸쳐 쪼개져 나와서 라벨 문자열로 찾기
    어렵고, 상자번호가 훨씬 안정적이다. 다만 그 번호가 "신고유형"
    코드표(11~40 같은 숫자들) 등 표 바깥에도 우연히 나올 수 있어서,
    "세액의 계산" 표 구간으로 범위를 좁혀서 찾는다. 종합소득금액(19)과
    최종 납부/환급세액(37, 없으면 33)만 우선 뽑는다 - 과세표준/결정세액/
    기납부세액까지 더 보여줄지는 실제 서류로 확인 후 결정."""
    start = text.find("세액의 계산")
    if start < 0:
        return None, None
    end = text.find("신고인은", start)
    section = text[start:end] if end > start else text[start:start + 2000]

    def box(num):
        m = re.search(rf"(?<!\d){num}(?!\d)\s+(-?[\d,]+)", section)
        if not m:
            return None
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return None

    income = box(19)
    tax_due = box(37)
    if tax_due is None:
        tax_due = box(33)
    return income, tax_due


def _extract_wage_payers(text):
    """종합소득세 신고서의 "❽ 근로소득ㆍ연금소득ㆍ기타소득명세서" 표에서
    급여를 준 회사 이름과 금액을 회사별로 뽑는다(51은 근로소득 코드).
    실제 운영에선 pypdf로 글자를 뽑는데, 이게 표 한 칸을 한 줄씩
    따로따로 내놓는다 - "51 1" 줄, 그 다음 회사명 줄, 그 다음 숫자
    5개(총수입금액/필요경비/소득금액/소득세/농특세) 줄, 그 다음
    사업자등록번호 줄, 이렇게 순서대로 분리된 줄들이 이어진다(처음엔
    테스트에 pdftotext를 써서 한 줄에 다 붙어나오는 걸로 착각하고
    만들었다가 실제 pypdf 결과로 다시 뽑아보고 틀린 걸 발견함). 그래서
    "51 <일련번호>"만 있는 줄을 기준점으로 잡고, 그 아래 몇 줄 안에서
    숫자 없는 줄(회사명)과 숫자 3개 이상인 줄(금액들)을 순서 상관없이
    찾는다. 연금/기타소득(다른 코드)은 아직 안 보여달라고 한 적이 없어서
    근로소득만."""
    start = text.find("근로소득")
    if start < 0:
        return []
    end = text.find("종합소득금액 및 결손금", start)
    section = text[start:end] if end > start else text[start:start + 3000]
    lines = [l.strip() for l in section.split("\n") if l.strip()]
    payers = []
    for i, line in enumerate(lines):
        if not re.match(r"^51\s+\d+$", line):
            continue
        name = ""
        numbers = []
        for look in lines[i + 1:i + 6]:
            nums_here = re.findall(r"-?[\d,]+", look)
            is_reg_no = re.match(r"^\d{3}-\d{2}-\d{5}$", look)
            if not name and not nums_here and not is_reg_no:
                name = look
            elif not numbers and len(nums_here) >= 3 and not is_reg_no:
                numbers = nums_here
            if name and numbers:
                break
        if not name or len(numbers) < 3:
            continue
        try:
            total = float(numbers[0].replace(",", ""))
            taxable = float(numbers[2].replace(",", ""))
        except ValueError:
            continue
        payers.append({"name": name, "income": total, "taxable": taxable})
    return payers


# 월급여명세서 항목 순서(지급/공제 각각 명세서에 찍힌 순서 그대로) -
# DB 칼럼명과 매칭된다. "몇 월 분"인지는 명세서 서식마다 발급일/서명일만
# 있고 제각각이라 믿을 수 없어서, 업로드할 때 사람이 직접 연/월을 고른다.
_SALARY_PAY_FIELDS = [
    ("base_pay", "기본급"), ("overtime_pay", "연장수당"), ("position_pay", "직책수당"),
    ("night_pay", "야근수당"), ("overnight_pay", "철야수당"), ("special_pay", "특근수당"),
    ("aiming_pay", "에이밍"), ("other_pay", "기타"),
]
_SALARY_DEDUCT_FIELDS = [
    ("income_tax", "갑근세"), ("local_tax", "주민세"), ("national_pension", "국민연금"),
    ("health_insurance", "건강보험"), ("employment_insurance", "고용보험"),
    ("loan_repayment", "학자금대출상환"),
]


def _salary_label_match(line, label):
    """OCR이 "연장"을 "면장", "건강"을 "건감"처럼 한 글자씩 틀리게
    읽는 경우가 많아서, 글자가 정확히 같은지 대신 비슷한 정도(문자열
    유사도)로 판단한다."""
    cleaned = re.sub(r"[()\s]", "", line)
    if not cleaned:
        return False
    ratio = difflib.SequenceMatcher(None, cleaned[: len(label) + 2], label).ratio()
    return ratio > 0.55


def _salary_parse_number(line):
    """OCR이 숫자 "0"을 영문자 "O"로 잘못 읽는 경우가 있어 같이 바꿔준다."""
    cleaned = line.strip().replace(",", "").replace("O", "0").replace("o", "0")
    if re.fullmatch(r"-?\d+", cleaned):
        return float(cleaned)
    return None


_SALARY_WORK_MONTH_RE = re.compile(r"\(?\s*(\d{1,2})\s*\)?\s*월\s*급여")
_SALARY_PAY_DATE_RE = re.compile(r"(20\d{2})-(\d{1,2})-(\d{1,2})")


def _extract_salary_period(text):
    """명세서 맨 앞의 "(4)월 급여명세서"가 실제 일한 달이고, 머리글에
    하이픈으로 찍힌 "2026-05-08" 같은 날짜가 그 급여를 받은 지급일이다
    (이름 옆 "2025,10,13,"처럼 쉼표로 된 날짜는 다른 용도라 안 씀).
    지급일의 연도를 기준으로 일한 연도를 구하되, 12월에 일하고 다음 해
    1월에 받는 경우만 한 해 앞당긴다."""
    m_month = _SALARY_WORK_MONTH_RE.search(text)
    worked_month = int(m_month.group(1)) if m_month else None
    if worked_month is not None and not (1 <= worked_month <= 12):
        worked_month = None

    m_date = _SALARY_PAY_DATE_RE.search(text)
    worked_year = None
    if worked_month and m_date:
        pay_year, pay_month = int(m_date.group(1)), int(m_date.group(2))
        worked_year = pay_year - 1 if (worked_month == 12 and pay_month == 1) else pay_year
    # 이미 지난 달 월급명세서가 미래 연도로 읽힐 수는 없다(OCR이 숫자
    # 하나를 잘못 읽은 것) - 그런 값은 버리고 업로드할 때 고른 연도를
    # 대신 쓰게 한다.
    if worked_year is not None and worked_year > datetime.date.today().year:
        worked_year = None
    return worked_year, worked_month


def _extract_salary_slip(text):
    """월급여명세서는 "항목명" 줄 다음에 "숫자" 줄이 오는 걸 반복하는
    표인데, 지급 항목과 공제 항목이 나란히 두 칸으로 인쇄돼 있어서 OCR이
    왼쪽 한 줄, 오른쪽 한 줄 순서로 번갈아 읽어온다. 값이 없는 항목(예:
    이번 달에 야근수당이 없으면 "야근수당()" 바로 다음 줄이 숫자가
    아니라 다음 항목명)은 건너뛴다. 항목 순서는 고정이라고 보고
    (_SALARY_PAY_FIELDS/_SALARY_DEDUCT_FIELDS), 그 순서대로 하나씩
    맞춰나간다."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    result = {}
    pay_fields = list(_SALARY_PAY_FIELDS)
    deduct_fields = list(_SALARY_DEDUCT_FIELDS)
    subtotal_seen = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        matched = False
        next_val = _salary_parse_number(lines[i + 1]) if i + 1 < len(lines) else None
        if pay_fields and _salary_label_match(line, pay_fields[0][1]):
            key, _ = pay_fields.pop(0)
            matched = True
        elif deduct_fields and _salary_label_match(line, deduct_fields[0][1]):
            key, _ = deduct_fields.pop(0)
            matched = True
        elif _salary_label_match(line, "소계"):
            key = "gross_pay" if subtotal_seen == 0 else "total_deduction"
            subtotal_seen += 1
            matched = True
        elif _salary_label_match(line, "실지급액"):
            key = "net_pay"
            matched = True
        if matched:
            if next_val is not None:
                result[key] = next_val
                i += 2
            else:
                i += 1
        else:
            i += 1
    return result


def _period_label_from_date(start_date):
    """날짜 하나(기간 시작일)로 짧은 표시용 이름을 만든다 - "25년 1기"처럼
    연도는 뒷 2자리만, 반기(1~6월=1기, 7~12월=2기)로 끊는다."""
    y, m, _ = start_date.split("-")
    half = 1 if int(m) <= 6 else 2
    return f"{y[2:]}년 {half}기"


_VAT_DATE_PAIR_RE = re.compile(
    r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})\D{0,10}?"
    r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})"
)


def _extract_vat_period_rows(text):
    """부가가치세과세표준증명은 한 장에 "과세기간(부터~까지) / 배출과세표준
    (계/과세분/면세분) / 납부할 세액" 행이 여러 개(보통 반기별로 2개) 같이
    찍혀 나온다 - 발급 시점까지의 지난 기수를 전부 모아서 보여주는
    서류라서다. 매출(과세표준)액과 실제 낸(또는 환급받은) 세액 둘 다
    중요하므로 같이 뽑는다 - 그 기간 뒤에 나오는 숫자들 중 첫 번째(계=
    과세표준액)와 마지막(납부할 세액, 환급이면 마이너스)을 쓴다. 표의
    모든 행을 찾아서 기수별로 따로따로 돌려준다(한 장을 여러 기수
    버튼이 같은 파일을 가리키게 등록하기 위함). 표를 못 찾으면 빈 리스트."""
    rows = []
    matches = list(_VAT_DATE_PAIR_RE.finditer(text))
    for i, m in enumerate(matches):
        start = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        end = f"{m.group(4)}-{int(m.group(5)):02d}-{int(m.group(6)):02d}"
        # 이 행의 숫자 4개(계/과세분/면세분/세액)만 보면 되니, 다음 행
        # 시작 전까지로 자르되 줄바꿈이 먼저 나오면 거기서 끊는다 - 안
        # 그러면 다음 행이 없는 마지막 기수에서 그 뒤에 찍히는 발급일자
        # 숫자("2026")까지 세액으로 잘못 집을 수 있다.
        hard_end = matches[i + 1].start() if i + 1 < len(matches) else min(len(text), m.end() + 60)
        newline_pos = text.find("\n", m.end())
        chunk_end = min(hard_end, newline_pos) if 0 <= newline_pos < hard_end else hard_end
        numbers = re.findall(r"-?[\d,]{4,}", text[m.end():chunk_end])
        if not numbers:
            continue
        try:
            base_amount = float(numbers[0].replace(",", ""))
            tax_amount = float(numbers[-1].replace(",", ""))
        except ValueError:
            continue
        rows.append({
            "year": int(m.group(1)),
            "period": f"{start}~{end}",
            "period_label": _period_label_from_date(start),
            "amount": base_amount,
            "tax_amount": tax_amount,
        })
    return rows


def _classify_document(filename, text):
    """서류 하나를 분석해서 등록할 document 행(들)을 돌려준다. 보통 1건
    이지만, 부가가치세과세표준증명처럼 한 장에 여러 기수 표가 들어있으면
    기수 개수만큼 여러 건으로 나눠서 돌려준다(파일은 같은 걸 같이 씀)."""
    doc_type = _classify_text(text)
    issue_date = _extract_date_near_label(text, ["발급일자", "발급일"])

    if doc_type == "부가가치세 과세표준증명":
        vat_rows = _extract_vat_period_rows(text)
        if vat_rows:
            if not issue_date:
                used_dates = set()
                for row in vat_rows:
                    used_dates.update(row["period"].split("~"))
                issue_date = _extract_issue_date_fallback(text, used_dates)
            return [
                {
                    "name": f"{row['period_label']} {doc_type}",
                    "category": doc_type,
                    "issue_date": issue_date,
                    "period": row["period"],
                    "period_label": row["period_label"],
                    "amount": row["amount"],
                    "tax_amount": row["tax_amount"],
                    "year": row["year"],
                    "doc_type": doc_type,
                    "income_amount": None,
                    "taxable_income_amount": None,
                    "income_business": None,
                    "income_wage": None,
                    "income_other": None,
                    "taxable_business": None,
                    "taxable_wage": None,
                    "taxable_other": None,
                    "income_sources": "",
                }
                for row in vat_rows
            ]

    if doc_type in _NO_YEAR_DOC_TYPES:
        year = None
        period = None
    else:
        year = _extract_year(text, filename)
        period = _extract_period(text)
    if not issue_date and doc_type == "종합소득세 신고서":
        issue_date = _extract_signing_date(text)
    if not issue_date:
        used_dates = set(period.split("~")) if period else set()
        issue_date = _extract_issue_date_fallback(text, used_dates)
    income_amount = None
    taxable_income_amount = None
    income_business = income_wage = income_other = None
    taxable_business = taxable_wage = taxable_other = None
    amount = None
    tax_amount = None
    income_sources = ""
    if doc_type == "소득금액증명":
        income_row = _extract_income_row(text, "수입금액")
        if income_row:
            income_amount = income_row["total"]
            income_business, income_wage, income_other = (
                income_row["business"], income_row["wage"], income_row["other"],
            )
        taxable_row = _extract_income_row(text, "소득금액")
        if taxable_row:
            taxable_income_amount = taxable_row["total"]
            taxable_business, taxable_wage, taxable_other = (
                taxable_row["business"], taxable_row["wage"], taxable_row["other"],
            )
    elif doc_type == "종합소득세 신고서":
        amount, tax_amount = _extract_jongso_amounts(text)
        payers = _extract_wage_payers(text)
        if payers:
            income_sources = json.dumps(payers, ensure_ascii=False)
    else:
        amount = _extract_generic_amount(doc_type, text)
    period_label = _period_label_from_date(period.split("~")[0]) if period else (f"{str(year)[2:]}년" if year else "")
    if doc_type and year:
        name = f"{year} {doc_type}"
    elif doc_type:
        name = doc_type
    else:
        name = os.path.splitext(filename)[0]
    return [{
        "name": name,
        "category": doc_type,
        "issue_date": issue_date,
        "period": period,
        "period_label": period_label,
        "amount": amount,
        "tax_amount": tax_amount,
        "year": year,
        "doc_type": doc_type,
        "income_amount": income_amount,
        "taxable_income_amount": taxable_income_amount,
        "income_business": income_business,
        "income_wage": income_wage,
        "income_other": income_other,
        "taxable_business": taxable_business,
        "taxable_wage": taxable_wage,
        "taxable_other": taxable_other,
        "income_sources": income_sources,
    }]


def _parse_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_year(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def serialize_documents(conn):
    rows = conn.execute(
        "SELECT * FROM documents ORDER BY year IS NOT NULL, year DESC, sort_order ASC, id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def _ensure_doc_type_order(conn):
    """연도별 집계에 등장하는 문서 종류마다 순서값이 있어야 위/아래로
    옮길 수 있다 - 새로 보는 문서 종류가 생기면(처음 분류됐을 때) 지금
    있는 것 중 제일 뒤에 자동으로 추가해준다."""
    known = {r["doc_type"] for r in conn.execute("SELECT doc_type FROM doc_type_order")}
    used = [
        r["doc_type"] for r in conn.execute(
            "SELECT DISTINCT doc_type FROM documents WHERE doc_type != '' ORDER BY doc_type"
        )
    ]
    missing = [d for d in used if d not in known]
    if not missing:
        return
    max_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) AS m FROM doc_type_order"
    ).fetchone()["m"]
    for doc_type in missing:
        max_order += 1
        conn.execute(
            "INSERT INTO doc_type_order (doc_type, sort_order) VALUES (?, ?)",
            (doc_type, max_order),
        )


def serialize_aggregate(conn):
    """doc_type이 등록된(비어있지 않은) 서류만 연도+문서종류로 묶어서
    금액 합계를 보여준다. doc_type이 없는 서류(사업자등록증 등 집계
    의미가 없는 서류)는 여기 안 잡힌다. 문서종류(섹션) 순서는 사용자가
    위/아래 버튼으로 바꿔둔 순서(doc_type_order)를 따른다."""
    _ensure_doc_type_order(conn)
    rows = conn.execute(
        """SELECT d.year, d.doc_type,
                  SUM(d.income_amount) AS income_total,
                  SUM(d.taxable_income_amount) AS taxable_total,
                  SUM(d.income_business) AS income_business_total,
                  SUM(d.income_wage) AS income_wage_total,
                  SUM(d.income_other) AS income_other_total,
                  SUM(d.taxable_business) AS taxable_business_total,
                  SUM(d.taxable_wage) AS taxable_wage_total,
                  SUM(d.taxable_other) AS taxable_other_total,
                  SUM(d.amount) AS amount_total,
                  SUM(d.tax_amount) AS tax_total,
                  COUNT(*) AS doc_count
           FROM documents d
           LEFT JOIN doc_type_order o ON o.doc_type = d.doc_type
           WHERE d.doc_type != '' AND d.year IS NOT NULL
           GROUP BY d.year, d.doc_type
           ORDER BY COALESCE(o.sort_order, 999999), d.doc_type, d.year DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def serialize_salary(conn):
    rows = conn.execute(
        "SELECT * FROM salary_records ORDER BY year DESC, month DESC"
    ).fetchall()
    return [dict(r) for r in rows]


@app.route("/")
def index():
    """css/js 파일 수정시각을 쿼리스트링(?v=)에 붙여서, 폰 브라우저가 코드
    새로 받아놓고도 예전에 캐시해둔 css/js를 계속 쓰는 일이 없게 한다.
    이 HTML 자체도 캐시되면 새 버전 번호가 붙은 링크조차 못 받아보게
    되므로 페이지 본문은 절대 캐시하지 않게 막는다(코어싱크와 동일한
    방식 - 이게 빠져있어서 서버 쪽은 다 고쳐도 폰 화면은 계속 예전
    그대로 보였던 것으로 보임)."""
    versioned_paths = [
        os.path.join(app.static_folder, "js", "app.js"),
        os.path.join(app.static_folder, "css", "style.css"),
        os.path.join(app.static_folder, "img", "logo.png"),
        os.path.join(app.static_folder, "img", "wordmark.png"),
        os.path.join(app.static_folder, "img", "icon-192.png"),
        os.path.join(app.static_folder, "img", "icon-512.png"),
        os.path.join(app.static_folder, "img", "apple-touch-icon.png"),
    ]
    asset_version = str(int(max(os.path.getmtime(p) for p in versioned_paths if os.path.exists(p))))
    resp = make_response(render_template("index.html", asset_version=asset_version))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.route("/api/full")
def api_full():
    conn = models.get_conn()
    try:
        return jsonify({
            "documents": serialize_documents(conn),
            "aggregate": serialize_aggregate(conn),
            "salary": serialize_salary(conn),
        })
    finally:
        conn.close()


@app.route("/api/doc_type_order/<doc_type>", methods=["PUT"])
def update_doc_type_order(doc_type):
    """연도별 집계에서 문서 종류별 표(섹션)를 한 칸 위/아래로 옮긴다 -
    인접한 문서 종류와 sort_order를 서로 맞바꾼다. 맨 위/아래면 그냥
    아무 것도 안 하고 지금 상태 그대로 돌려준다."""
    data = request.get_json(silent=True) or {}
    direction = data.get("direction")
    if direction not in ("up", "down"):
        return jsonify({"error": "direction은 up 또는 down이어야 합니다."}), 400
    conn = models.get_conn()
    try:
        _ensure_doc_type_order(conn)
        rows = conn.execute(
            "SELECT doc_type, sort_order FROM doc_type_order ORDER BY sort_order"
        ).fetchall()
        order_list = [r["doc_type"] for r in rows]
        if doc_type in order_list:
            idx = order_list.index(doc_type)
            swap_idx = idx - 1 if direction == "up" else idx + 1
            if 0 <= swap_idx < len(order_list):
                other = order_list[swap_idx]
                a, b = rows[idx]["sort_order"], rows[swap_idx]["sort_order"]
                conn.execute("UPDATE doc_type_order SET sort_order=? WHERE doc_type=?", (b, doc_type))
                conn.execute("UPDATE doc_type_order SET sort_order=? WHERE doc_type=?", (a, other))
                conn.commit()
        return jsonify({
            "documents": serialize_documents(conn),
            "aggregate": serialize_aggregate(conn),
        })
    finally:
        conn.close()


@app.route("/api/documents/auto", methods=["POST"])
def create_document_auto():
    """파일만 올리면 글자를 읽어서 서류명/분류/연도/집계용 문서종류(+ 소득
    금액증명이면 금액까지) 자동으로 채워서 등록한다. 틀린 부분은 등록 후
    목록에서 "수정"으로 고치면 된다."""
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return jsonify({"error": "파일을 선택하세요."}), 400

    file_bytes = uploaded.read()
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    conn = models.get_conn()
    try:
        # 내용이 완전히 똑같은 파일이 이미 등록돼 있으면(같은 파일 실수로
        # 두 번 올림) 또 등록해서 집계가 중복으로 합산되는 걸 막는다.
        dup = conn.execute(
            "SELECT name FROM documents WHERE file_hash = ? LIMIT 1", (file_hash,)
        ).fetchone()
        if dup:
            return jsonify({
                "error": f"이미 등록된 파일입니다 (\"{dup['name']}\"). 다른 파일이면 다시 확인해주세요."
            }), 409

        text = _extract_text_from_file(uploaded.filename, file_bytes)
        detected_list = _classify_document(uploaded.filename, text)

        ext = os.path.splitext(secure_filename(uploaded.filename))[1]
        file_name = f"{uuid.uuid4().hex}{ext}"
        with open(os.path.join(models.UPLOAD_DIR, file_name), "wb") as f:
            f.write(file_bytes)

        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS m FROM documents"
        ).fetchone()["m"]
        first_id = None
        for detected in detected_list:
            max_order += 1
            cur = conn.execute(
                """INSERT INTO documents (name, category, year, doc_type, income_amount,
                   taxable_income_amount, income_business, income_wage, income_other,
                   taxable_business, taxable_wage, taxable_other, income_sources, amount,
                   tax_amount, issue_date, period, period_label, file_name, original_name,
                   file_hash, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    detected["name"],
                    detected["category"],
                    detected["year"],
                    detected["doc_type"],
                    detected["income_amount"],
                    detected["taxable_income_amount"],
                    detected["income_business"],
                    detected["income_wage"],
                    detected["income_other"],
                    detected["taxable_business"],
                    detected["taxable_wage"],
                    detected["taxable_other"],
                    detected.get("income_sources") or "",
                    detected["amount"],
                    detected.get("tax_amount"),
                    detected["issue_date"] or "",
                    detected["period"] or "",
                    detected["period_label"] or "",
                    file_name,
                    uploaded.filename,
                    file_hash,
                    max_order,
                ),
            )
            if first_id is None:
                first_id = cur.lastrowid
        conn.commit()
        return jsonify({
            "documents": serialize_documents(conn),
            "aggregate": serialize_aggregate(conn),
            "detected": detected_list[0] if detected_list else {},
            "detected_count": len(detected_list),
            "id": first_id,
            "text_found": bool(text.strip()),
        })
    finally:
        conn.close()


@app.route("/api/documents", methods=["POST"])
def create_document():
    name = (request.form.get("name") or "").strip()
    if not name:
        return jsonify({"error": "서류명을 입력하세요."}), 400

    file_name = ""
    original_name = ""
    uploaded = request.files.get("file")
    if uploaded and uploaded.filename:
        original_name = uploaded.filename
        ext = os.path.splitext(secure_filename(uploaded.filename))[1]
        file_name = f"{uuid.uuid4().hex}{ext}"
        uploaded.save(os.path.join(models.UPLOAD_DIR, file_name))

    conn = models.get_conn()
    try:
        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS m FROM documents"
        ).fetchone()["m"]
        conn.execute(
            """INSERT INTO documents (name, category, year, doc_type, income_amount,
               taxable_income_amount, income_business, income_wage, income_other,
               taxable_business, taxable_wage, taxable_other, amount, tax_amount,
               issue_date, period, period_label, file_name, original_name, memo, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                (request.form.get("category") or "").strip(),
                _parse_year(request.form.get("year")),
                (request.form.get("doc_type") or "").strip(),
                _parse_float(request.form.get("income_amount")),
                _parse_float(request.form.get("taxable_income_amount")),
                _parse_float(request.form.get("income_business")),
                _parse_float(request.form.get("income_wage")),
                _parse_float(request.form.get("income_other")),
                _parse_float(request.form.get("taxable_business")),
                _parse_float(request.form.get("taxable_wage")),
                _parse_float(request.form.get("taxable_other")),
                _parse_float(request.form.get("amount")),
                _parse_float(request.form.get("tax_amount")),
                (request.form.get("issue_date") or "").strip(),
                (request.form.get("period") or "").strip(),
                (request.form.get("period_label") or "").strip(),
                file_name,
                original_name,
                (request.form.get("memo") or "").strip(),
                max_order + 1,
            ),
        )
        conn.commit()
        return jsonify({
            "documents": serialize_documents(conn),
            "aggregate": serialize_aggregate(conn),
        })
    finally:
        conn.close()


@app.route("/api/documents/<int:doc_id>", methods=["PUT"])
def update_document(doc_id):
    conn = models.get_conn()
    try:
        item = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if not item:
            return jsonify({"error": "서류를 찾을 수 없습니다."}), 404

        name = (request.form.get("name") or item["name"]).strip()
        file_name = item["file_name"]
        original_name = item["original_name"]
        uploaded = request.files.get("file")
        if uploaded and uploaded.filename:
            if item["file_name"]:
                old_path = os.path.join(models.UPLOAD_DIR, item["file_name"])
                if os.path.exists(old_path):
                    os.remove(old_path)
            original_name = uploaded.filename
            ext = os.path.splitext(secure_filename(uploaded.filename))[1]
            file_name = f"{uuid.uuid4().hex}{ext}"
            uploaded.save(os.path.join(models.UPLOAD_DIR, file_name))

        conn.execute(
            """UPDATE documents SET name=?, category=?, year=?, doc_type=?, income_amount=?,
               taxable_income_amount=?, income_business=?, income_wage=?, income_other=?,
               taxable_business=?, taxable_wage=?, taxable_other=?, amount=?, tax_amount=?,
               issue_date=?, period=?, period_label=?, file_name=?, original_name=?, memo=?
               WHERE id=?""",
            (
                name,
                (request.form.get("category", item["category"]) or "").strip(),
                _parse_year(request.form.get("year", item["year"])),
                (request.form.get("doc_type", item["doc_type"]) or "").strip(),
                _parse_float(request.form.get("income_amount", item["income_amount"])),
                _parse_float(request.form.get("taxable_income_amount", item["taxable_income_amount"])),
                _parse_float(request.form.get("income_business", item["income_business"])),
                _parse_float(request.form.get("income_wage", item["income_wage"])),
                _parse_float(request.form.get("income_other", item["income_other"])),
                _parse_float(request.form.get("taxable_business", item["taxable_business"])),
                _parse_float(request.form.get("taxable_wage", item["taxable_wage"])),
                _parse_float(request.form.get("taxable_other", item["taxable_other"])),
                _parse_float(request.form.get("amount", item["amount"])),
                _parse_float(request.form.get("tax_amount", item["tax_amount"])),
                (request.form.get("issue_date", item["issue_date"]) or "").strip(),
                (request.form.get("period", item["period"]) or "").strip(),
                (request.form.get("period_label", item["period_label"]) or "").strip(),
                file_name,
                original_name,
                (request.form.get("memo", item["memo"]) or "").strip(),
                doc_id,
            ),
        )
        conn.commit()
        return jsonify({
            "documents": serialize_documents(conn),
            "aggregate": serialize_aggregate(conn),
        })
    finally:
        conn.close()


@app.route("/api/documents/<int:doc_id>", methods=["DELETE"])
def delete_document(doc_id):
    conn = models.get_conn()
    try:
        item = conn.execute("SELECT file_name FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if item and item["file_name"]:
            path = os.path.join(models.UPLOAD_DIR, item["file_name"])
            if os.path.exists(path):
                os.remove(path)
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        conn.commit()
        return jsonify({
            "documents": serialize_documents(conn),
            "aggregate": serialize_aggregate(conn),
        })
    finally:
        conn.close()


@app.route("/api/documents/<int:doc_id>/download")
def download_document(doc_id):
    conn = models.get_conn()
    try:
        item = conn.execute("SELECT file_name, original_name FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if not item or not item["file_name"]:
            return jsonify({"error": "파일이 없습니다."}), 404
        # as_attachment=True면 "다운로드"로 취급돼서 크롬/웨일이
        # http 주소에서는 "안전하게 받을 수 없음"이라고 막아버린다.
        # inline으로 그냥 보여주면(브라우저 내장 PDF 뷰어 등) 다운로드로
        # 안 잡혀서 안 막힌다 - 필요하면 거기서 직접 길게 눌러 저장하면 됨.
        return send_from_directory(
            models.UPLOAD_DIR, item["file_name"],
            as_attachment=False, download_name=item["original_name"] or item["file_name"],
        )
    finally:
        conn.close()


_SALARY_FIELD_NAMES = [
    "base_pay", "overtime_pay", "position_pay", "night_pay", "overnight_pay",
    "special_pay", "aiming_pay", "other_pay", "gross_pay", "income_tax", "local_tax",
    "national_pension", "health_insurance", "employment_insurance", "loan_repayment",
    "total_deduction", "net_pay",
]


@app.route("/api/salary/auto", methods=["POST"])
def create_salary_auto():
    """월급여명세서 파일을 올리면 항목별 금액을 읽어서 채운다. 서식
    맨 앞의 "(4)월 급여명세서"와 지급일로 실제 일한 연/월을 알아낼 수
    있으면 그걸 쓰고(업로드할 때 고른 값은 못 알아냈을 때만 대신 씀),
    둘 다 없으면 에러."""
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return jsonify({"error": "파일을 선택하세요."}), 400
    form_year = _parse_year(request.form.get("year"))
    form_month = _parse_year(request.form.get("month"))

    file_bytes = uploaded.read()
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    conn = models.get_conn()
    try:
        dup = conn.execute(
            "SELECT year, month FROM salary_records WHERE file_hash = ? LIMIT 1", (file_hash,)
        ).fetchone()
        if dup:
            return jsonify({
                "error": f"이미 등록된 파일입니다 ({dup['year']}년 {dup['month']}월)."
            }), 409

        text = _extract_text_from_file(uploaded.filename, file_bytes)
        parsed = _extract_salary_slip(text) if text else {}
        detected_year, detected_month = _extract_salary_period(text) if text else (None, None)
        year = detected_year or form_year
        month = detected_month or form_month
        if not year or not month or not (1 <= month <= 12):
            return jsonify({"error": "연도와 월을 선택하세요."}), 400

        ext = os.path.splitext(secure_filename(uploaded.filename))[1]
        file_name = f"{uuid.uuid4().hex}{ext}"
        with open(os.path.join(models.UPLOAD_DIR, file_name), "wb") as f:
            f.write(file_bytes)

        columns = ", ".join(_SALARY_FIELD_NAMES)
        placeholders = ", ".join("?" for _ in _SALARY_FIELD_NAMES)
        cur = conn.execute(
            f"""INSERT INTO salary_records (year, month, {columns}, file_name, original_name,
               file_hash) VALUES (?, ?, {placeholders}, ?, ?, ?)""",
            (
                year, month,
                *[parsed.get(f) for f in _SALARY_FIELD_NAMES],
                file_name, uploaded.filename, file_hash,
            ),
        )
        conn.commit()
        return jsonify({
            "salary": serialize_salary(conn),
            "id": cur.lastrowid,
            "text_found": bool(text),
            "year": year,
            "month": month,
            "period_detected": bool(detected_year and detected_month),
        })
    finally:
        conn.close()


@app.route("/api/salary", methods=["POST"])
def create_salary_manual():
    year = _parse_year(request.form.get("year"))
    month = _parse_year(request.form.get("month"))
    if not year or not month or not (1 <= month <= 12):
        return jsonify({"error": "연도와 월을 선택하세요."}), 400

    file_name = ""
    original_name = ""
    uploaded = request.files.get("file")
    if uploaded and uploaded.filename:
        ext = os.path.splitext(secure_filename(uploaded.filename))[1]
        file_name = f"{uuid.uuid4().hex}{ext}"
        original_name = uploaded.filename
        uploaded.save(os.path.join(models.UPLOAD_DIR, file_name))

    conn = models.get_conn()
    try:
        columns = ", ".join(_SALARY_FIELD_NAMES)
        placeholders = ", ".join("?" for _ in _SALARY_FIELD_NAMES)
        conn.execute(
            f"""INSERT INTO salary_records (year, month, {columns}, file_name, original_name,
               memo) VALUES (?, ?, {placeholders}, ?, ?, ?)""",
            (
                year, month,
                *[_parse_float(request.form.get(f)) for f in _SALARY_FIELD_NAMES],
                file_name, original_name,
                (request.form.get("memo") or "").strip(),
            ),
        )
        conn.commit()
        return jsonify({"salary": serialize_salary(conn)})
    finally:
        conn.close()


@app.route("/api/salary/<int:record_id>", methods=["PUT"])
def update_salary(record_id):
    conn = models.get_conn()
    try:
        item = conn.execute("SELECT * FROM salary_records WHERE id = ?", (record_id,)).fetchone()
        if not item:
            return jsonify({"error": "기록을 찾을 수 없습니다."}), 404

        file_name = item["file_name"]
        original_name = item["original_name"]
        uploaded = request.files.get("file")
        if uploaded and uploaded.filename:
            if item["file_name"]:
                old_path = os.path.join(models.UPLOAD_DIR, item["file_name"])
                if os.path.exists(old_path):
                    os.remove(old_path)
            original_name = uploaded.filename
            ext = os.path.splitext(secure_filename(uploaded.filename))[1]
            file_name = f"{uuid.uuid4().hex}{ext}"
            uploaded.save(os.path.join(models.UPLOAD_DIR, file_name))

        set_clause = ", ".join(f"{f}=?" for f in _SALARY_FIELD_NAMES)
        conn.execute(
            f"""UPDATE salary_records SET year=?, month=?, {set_clause}, file_name=?,
               original_name=?, memo=? WHERE id=?""",
            (
                _parse_year(request.form.get("year", item["year"])),
                _parse_year(request.form.get("month", item["month"])),
                *[_parse_float(request.form.get(f, item[f])) for f in _SALARY_FIELD_NAMES],
                file_name, original_name,
                (request.form.get("memo", item["memo"]) or "").strip(),
                record_id,
            ),
        )
        conn.commit()
        return jsonify({"salary": serialize_salary(conn)})
    finally:
        conn.close()


@app.route("/api/salary/<int:record_id>", methods=["DELETE"])
def delete_salary(record_id):
    conn = models.get_conn()
    try:
        item = conn.execute("SELECT file_name FROM salary_records WHERE id = ?", (record_id,)).fetchone()
        if item and item["file_name"]:
            path = os.path.join(models.UPLOAD_DIR, item["file_name"])
            if os.path.exists(path):
                os.remove(path)
        conn.execute("DELETE FROM salary_records WHERE id = ?", (record_id,))
        conn.commit()
        return jsonify({"salary": serialize_salary(conn)})
    finally:
        conn.close()


@app.route("/api/salary/<int:record_id>/download")
def download_salary(record_id):
    conn = models.get_conn()
    try:
        item = conn.execute(
            "SELECT file_name, original_name FROM salary_records WHERE id = ?", (record_id,)
        ).fetchone()
        if not item or not item["file_name"]:
            return jsonify({"error": "파일이 없습니다."}), 404
        return send_from_directory(
            models.UPLOAD_DIR, item["file_name"],
            as_attachment=False, download_name=item["original_name"] or item["file_name"],
        )
    finally:
        conn.close()


def _reclassify_blank_documents():
    """분류 로직이 바뀔 때마다 예전에 올려서 doc_type이 빈 채로 저장된
    서류들(인식 실패했던 것들)을 사용자가 지우고 다시 올릴 필요 없이,
    서버가 켜질 때 저장된 원본 파일로 자동으로 다시 분류해본다. 이미
    doc_type이 있는(제대로 분류됐거나 직접 입력한) 서류는 손대지 않고,
    다시 해봐도 못 알아내면 그대로 둔다."""
    conn = models.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, file_name, original_name FROM documents "
            "WHERE (doc_type IS NULL OR doc_type = '') AND file_name != ''"
        ).fetchall()
        for row in rows:
            path = os.path.join(models.UPLOAD_DIR, row["file_name"])
            try:
                with open(path, "rb") as f:
                    file_bytes = f.read()
            except Exception:
                continue
            fname = row["original_name"] or row["file_name"]
            text = _extract_text_from_file(fname, file_bytes)
            if not text:
                continue
            detected_list = _classify_document(fname, text)
            if not detected_list or not detected_list[0]["doc_type"]:
                continue
            d = detected_list[0]
            conn.execute(
                "UPDATE documents SET name=?, category=?, year=?, doc_type=?, income_amount=?, "
                "taxable_income_amount=?, income_business=?, income_wage=?, income_other=?, "
                "taxable_business=?, taxable_wage=?, taxable_other=?, income_sources=?, amount=?, "
                "tax_amount=?, issue_date=?, period=?, period_label=? WHERE id=?",
                (d["name"], d["category"], d["year"], d["doc_type"], d["income_amount"],
                 d["taxable_income_amount"], d["income_business"], d["income_wage"],
                 d["income_other"], d["taxable_business"], d["taxable_wage"], d["taxable_other"],
                 d.get("income_sources") or "", d["amount"], d["tax_amount"], d["issue_date"],
                 d["period"], d["period_label"], row["id"]),
            )
        conn.commit()
    finally:
        conn.close()


def _backfill_income_breakdown():
    """사업/근로/기타소득 세부 항목을 나중에 추가해서, 그 전에 이미
    doc_type까지는 제대로 분류됐던 소득금액증명 서류는 _reclassify_
    blank_documents(doc_type 빈 것만 다시 봄)로는 못 건드린다. 그런
    서류만 따로 찾아서 세부 항목만 채워 넣는다."""
    conn = models.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, file_name, original_name FROM documents "
            "WHERE doc_type = '소득금액증명' AND file_name != '' AND taxable_business IS NULL"
        ).fetchall()
        for row in rows:
            path = os.path.join(models.UPLOAD_DIR, row["file_name"])
            try:
                with open(path, "rb") as f:
                    file_bytes = f.read()
            except Exception:
                continue
            fname = row["original_name"] or row["file_name"]
            text = _extract_text_from_file(fname, file_bytes)
            if not text:
                continue
            detected_list = _classify_document(fname, text)
            if not detected_list:
                continue
            d = detected_list[0]
            conn.execute(
                "UPDATE documents SET income_business=?, income_wage=?, income_other=?, "
                "taxable_business=?, taxable_wage=?, taxable_other=? WHERE id=?",
                (d["income_business"], d["income_wage"], d["income_other"],
                 d["taxable_business"], d["taxable_wage"], d["taxable_other"], row["id"]),
            )
        conn.commit()
    finally:
        conn.close()


def _backfill_wage_payers():
    """근로소득 지급자(회사)별 내역, 발급일(신고일), 금액을 나중에
    고쳐서(pypdf 실제 출력 기준으로 다시 검증하기 전까지 세 개 다
    버그가 있었음) 그 전에 이미 등록된 종합소득세 신고서는 틀린 채로
    남아있다. income_sources가 비어있는 서류(= 이 수정 전에 등록된
    서류)만 다시 원본 파일로 분류해서 issue_date/amount/tax_amount/
    income_sources를 전부 다시 채운다 - 이미 맞게 올라간 서류는
    income_sources가 채워져 있으니 안 건드림."""
    conn = models.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, file_name, original_name FROM documents "
            "WHERE doc_type = '종합소득세 신고서' AND file_name != '' "
            "AND (income_sources IS NULL OR income_sources = '')"
        ).fetchall()
        for row in rows:
            path = os.path.join(models.UPLOAD_DIR, row["file_name"])
            try:
                with open(path, "rb") as f:
                    file_bytes = f.read()
            except Exception:
                continue
            fname = row["original_name"] or row["file_name"]
            text = _extract_text_from_file(fname, file_bytes)
            if not text:
                continue
            detected_list = _classify_document(fname, text)
            if not detected_list:
                continue
            d = detected_list[0]
            conn.execute(
                "UPDATE documents SET issue_date=?, amount=?, tax_amount=?, income_sources=? "
                "WHERE id=?",
                (d["issue_date"] or "", d["amount"], d["tax_amount"],
                 d.get("income_sources") or "", row["id"]),
            )
        conn.commit()
    finally:
        conn.close()


def _strip_year_from_single_page_docs():
    """사업자등록증처럼 한 장짜리 보관용 서류(_NO_YEAR_DOC_TYPES)에
    실수로 연도가 붙어서 등록된 게 있으면 지운다 - 발급일자의 해였을
    뿐 귀속연도가 아니라서 의미가 없고, 연도별 집계표에도 뜨면 안 된다.
    이름이 자동생성 그대로("2015 사업자등록증")였으면 doc_type 이름으로
    되돌리고, 사용자가 직접 고쳐둔 이름이면 그대로 둔다."""
    conn = models.get_conn()
    try:
        placeholders = ",".join("?" for _ in _NO_YEAR_DOC_TYPES)
        rows = conn.execute(
            f"SELECT id, doc_type, name, year FROM documents "
            f"WHERE doc_type IN ({placeholders}) AND year IS NOT NULL",
            tuple(_NO_YEAR_DOC_TYPES),
        ).fetchall()
        for row in rows:
            new_name = row["doc_type"] if row["name"] == f"{row['year']} {row['doc_type']}" else row["name"]
            conn.execute(
                "UPDATE documents SET year=NULL, period='', period_label='', name=? WHERE id=?",
                (new_name, row["id"]),
            )
        conn.commit()
    finally:
        conn.close()


def _backfill_salary_future_year():
    """미래 연도 방어 코드를 넣기 전에 올라간 급여명세서는 이미 틀린
    연도로 저장돼 있다. 아직 오지 않은 연도로 찍힌 기록만 실제 원본
    파일을 다시 읽어서 올바른 연/월로 고친다 - 못 읽으면 그대로 두고
    사용자가 직접 수정 버튼으로 고치게 한다."""
    conn = models.get_conn()
    try:
        this_year = datetime.date.today().year
        rows = conn.execute(
            "SELECT id, year, month, file_name, original_name FROM salary_records "
            "WHERE year > ? AND file_name != ''", (this_year,),
        ).fetchall()
        for row in rows:
            path = os.path.join(models.UPLOAD_DIR, row["file_name"])
            try:
                with open(path, "rb") as f:
                    file_bytes = f.read()
            except Exception:
                continue
            fname = row["original_name"] or row["file_name"]
            text = _extract_text_from_file(fname, file_bytes)
            if not text:
                continue
            fixed_year, fixed_month = _extract_salary_period(text)
            if fixed_year and fixed_month:
                conn.execute(
                    "UPDATE salary_records SET year=?, month=? WHERE id=?",
                    (fixed_year, fixed_month, row["id"]),
                )
        conn.commit()
    finally:
        conn.close()


_reclassify_blank_documents()
_backfill_income_breakdown()
_backfill_wage_payers()
_strip_year_from_single_page_docs()
_backfill_salary_future_year()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5051, debug=False)
