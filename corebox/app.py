# -*- coding: utf-8 -*-
"""COREBOX - 자주 쓰는 서류를 한 곳에 모아두는 보관함. 연도/문서종류별로
숫자가 있는 서류(소득금액증명 등)는 자동으로 연도별 집계표를 보여준다."""
import os
import re
import uuid
from io import BytesIO

from flask import Flask, jsonify, render_template, request, send_from_directory
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
    ("소득금액증명", ["소득금액증명"]),
    ("부가가치세 과세표준증명", ["과세표준증명"]),
    ("부가가치세 신고서", ["부가가치세", "부가세"]),
    ("사업자등록증", ["사업자등록증"]),
    ("통신판매업 신고증", ["통신판매업"]),
    ("건강기능식품 영업 신고증", ["건강기능식품"]),
    ("의료기기 판매업 신고증", ["의료기기"]),
    ("사업자 통장 사본", ["통장사본", "통장 사본"]),
]

# doc_type별로 "금액" 칸에 뽑을 라벨(소득금액증명은 수입/소득 2개를 따로
# 보여주니 여기선 제외) - 서류에 없는 라벨이면 그냥 못 찾고 넘어간다.
_GENERIC_AMOUNT_LABELS = {
    "부가가치세 과세표준증명": ["과세표준액", "공급가액"],
    "부가가치세 신고서": ["과세표준", "공급가액"],
}


def _classify_text(text):
    for label, keywords in _DOC_TYPE_RULES:
        if any(k in text for k in keywords):
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


def _extract_generic_amount(doc_type, text):
    for label in _GENERIC_AMOUNT_LABELS.get(doc_type, []):
        amount = _extract_amount_near_label(text, label)
        if amount is not None:
            return amount
    return None


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
                }
                for row in vat_rows
            ]

    year = _extract_year(text, filename)
    period = _extract_period(text)
    if not issue_date:
        used_dates = set(period.split("~")) if period else set()
        issue_date = _extract_issue_date_fallback(text, used_dates)
    income_amount = None
    taxable_income_amount = None
    amount = None
    if doc_type == "소득금액증명":
        income_amount = _extract_amount_near_label(text, "수입금액")
        taxable_income_amount = _extract_amount_near_label(text, "소득금액")
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
        "tax_amount": None,
        "year": year,
        "doc_type": doc_type,
        "income_amount": income_amount,
        "taxable_income_amount": taxable_income_amount,
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


def serialize_aggregate(conn):
    """doc_type이 등록된(비어있지 않은) 서류만 연도+문서종류로 묶어서
    금액 합계를 보여준다. doc_type이 없는 서류(사업자등록증 등 집계
    의미가 없는 서류)는 여기 안 잡힌다."""
    rows = conn.execute(
        """SELECT year, doc_type,
                  SUM(income_amount) AS income_total,
                  SUM(taxable_income_amount) AS taxable_total,
                  SUM(amount) AS amount_total,
                  SUM(tax_amount) AS tax_total,
                  COUNT(*) AS doc_count
           FROM documents
           WHERE doc_type != '' AND year IS NOT NULL
           GROUP BY year, doc_type
           ORDER BY doc_type, year DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/full")
def api_full():
    conn = models.get_conn()
    try:
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
    text = _extract_text_from_file(uploaded.filename, file_bytes)
    detected_list = _classify_document(uploaded.filename, text)

    ext = os.path.splitext(secure_filename(uploaded.filename))[1]
    file_name = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(models.UPLOAD_DIR, file_name), "wb") as f:
        f.write(file_bytes)

    conn = models.get_conn()
    try:
        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS m FROM documents"
        ).fetchone()["m"]
        first_id = None
        for detected in detected_list:
            max_order += 1
            cur = conn.execute(
                """INSERT INTO documents (name, category, year, doc_type, income_amount,
                   taxable_income_amount, amount, tax_amount, issue_date, period, period_label,
                   file_name, original_name, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    detected["name"],
                    detected["category"],
                    detected["year"],
                    detected["doc_type"],
                    detected["income_amount"],
                    detected["taxable_income_amount"],
                    detected["amount"],
                    detected.get("tax_amount"),
                    detected["issue_date"] or "",
                    detected["period"] or "",
                    detected["period_label"] or "",
                    file_name,
                    uploaded.filename,
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
               taxable_income_amount, amount, tax_amount, issue_date, period, period_label,
               file_name, original_name, memo, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                (request.form.get("category") or "").strip(),
                _parse_year(request.form.get("year")),
                (request.form.get("doc_type") or "").strip(),
                _parse_float(request.form.get("income_amount")),
                _parse_float(request.form.get("taxable_income_amount")),
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
               taxable_income_amount=?, amount=?, tax_amount=?, issue_date=?, period=?,
               period_label=?, file_name=?, original_name=?, memo=? WHERE id=?""",
            (
                name,
                (request.form.get("category", item["category"]) or "").strip(),
                _parse_year(request.form.get("year", item["year"])),
                (request.form.get("doc_type", item["doc_type"]) or "").strip(),
                _parse_float(request.form.get("income_amount", item["income_amount"])),
                _parse_float(request.form.get("taxable_income_amount", item["taxable_income_amount"])),
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
        return send_from_directory(
            models.UPLOAD_DIR, item["file_name"],
            as_attachment=True, download_name=item["original_name"] or item["file_name"],
        )
    finally:
        conn.close()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5051, debug=False)
