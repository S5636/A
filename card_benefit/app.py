# -*- coding: utf-8 -*-
"""CORESYNC - PC/폰 공용 웹앱.

같은 와이파이(공유기)에 연결된 PC와 폰이 브라우저로 같은 서버(0.0.0.0)에
접속해서 카드별 혜택의 이번 달 남은 한도를 같이 보고 같이 기록한다.
"""
import calendar
import hashlib
import json
import os
import re
import secrets
import uuid
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from io import BytesIO

from flask import Flask, jsonify, make_response, render_template, request, send_file, send_from_directory
from openpyxl import Workbook, load_workbook
from werkzeug.utils import secure_filename

from models import (
    TIME_BLOCKED_LABEL,
    DEFAULT_PERFORMANCE_EXCLUDED_KEYWORDS,
    UPLOAD_DIR,
    annotate_merchant,
    card_codes_match,
    compute_change_earned,
    compute_percent_discount,
    current_year_month,
    cycle_bounds,
    extract_last4,
    find_statement_header,
    get_conn,
    get_setting,
    init_db,
    is_cancelled_status,
    is_foreign_merchant,
    is_medical_merchant,
    is_performance_excluded,
    match_benefit_keyword,
    match_rate_table,
    parse_rate_table_categories,
    rate_table_entry_by_label,
    parse_amount_cell,
    parse_date_cell,
    parse_datetime_cell,
    parse_notification,
    previous_year_month,
    set_setting,
    tier_limit_for_spend,
)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

app = Flask(__name__)

# 서버 프로세스가 이 코드로 떠서 돌기 시작한 시각 - launcher.py의
# self_update가 새 코드를 받으면 서버를 재시작시키므로, 사실상 "지금
# 실행 중인 코드가 언제 적용됐는지"를 보여준다. 코드를 고쳐서 올려도
# 자동 업데이트가 몇 분 주기로만 확인해서 반영까지 시차가 있는데,
# 화면에서 이 시각을 보면 "내가 보낸 수정이 실제로 반영됐는지"를 매번
# 캡처 주고받지 않고 바로 확인할 수 있다.
_SERVER_STARTED_AT = datetime.now()


def _log_stock_fetch_error(code, source, error, raw=""):
    """시세 가져오기 실패 원인을 앱 폴더의 stock_fetch_error.log에 남긴다.
    실제로 돌아가는 PC에서만 재현되는 문제라서, "실패했다"는 사실만으론
    뭘 고쳐야 할지 알 수 없다 - 서버가 실제로 뭘 돌려줬는지(raw, 앞부분만)
    같이 남겨야 파싱 로직을 실제 응답에 맞게 고칠 수 있다."""
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_fetch_error.log")
        snippet = (raw or "").strip().replace("\n", " ")[:4000]
        with open(path, "a", encoding="utf-8") as f:
            f.write(
                f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [{code}] {source} 실패 - "
                f"{type(error).__name__}: {error}"
                f"{' | 응답: ' + snippet if snippet else ''}\n"
            )
    except Exception:
        pass


def _fetch_stock_price(code):
    """KRX 종목 현재가를 가져온다. 둘 다 비공식 방식이라 언제든 막힐 수
    있어서 순서대로 시도한다:
    1) 네이버 금융 실시간 시세 API(JSON, 빠름)
    2) 네이버 금융 종목 상세 페이지(HTML) 긁기 - 1번이 막혀도 될 때가
       있어서 대비용.
    둘 다 실패하면 None을 돌려주고(예외를 올리지 않음, 호출하는 쪽이
    그 종목만 건너뛰고 계속 돌 수 있게) 실패 원인과 함께 실제 응답
    내용(일부)을 로그로 남긴다 - "실패했다"는 것만으론 뭘 고쳐야 할지
    알 수 없고, 서버가 실제로 뭘 돌려줬는지 봐야 파싱 로직을 맞게
    고칠 수 있다."""
    import urllib.request

    # KRX 종목은 공식적으로 "A"+6자리(예: A271160)로도 많이 표기되지만
    # (공모주 안내 등에서 흔히 이 형식으로 나옴), 네이버 금융 쪽 URL/API는
    # 앞의 "A" 없이 순수 6자리만 받는다. 앞에 A가 붙어 있으면 떼고 쓴다.
    code = re.sub(r"^[Aa]", "", code or "")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Referer": "https://finance.naver.com/",
    }

    url1 = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"
    raw1 = ""
    try:
        req = urllib.request.Request(url1, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw1 = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw1)
        # 2026-11-11: 실제 PC 로그로 확인된 진짜 응답 모양은 예전에 가정했던
        # data["result"]["areas"][0]["datas"][0]["nv"]가 아니라 최상위
        # data["datas"][0] 형태(네이버가 API를 바꾼 것). 가격 필드는
        # "closePriceRaw"(콤마 없는 순수 숫자 문자열) - 이름과 달리 전일종가가
        # 아니라 실시간 현재가임(같이 찍히는 compareToPreviousClosePrice/
        # fluctuationsRatio 값으로 역산해서 실제 로그 응답에서 직접 확인함).
        return float(data["datas"][0]["closePriceRaw"])
    except Exception as e1:
        _log_stock_fetch_error(code, "실시간 API", e1, raw1)

    url2 = f"https://finance.naver.com/item/main.naver?code={code}"
    raw2 = ""
    try:
        from bs4 import BeautifulSoup
        req = urllib.request.Request(url2, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw_bytes = resp.read()
        raw2 = raw_bytes.decode("utf-8", errors="replace")
        soup = BeautifulSoup(raw_bytes, "lxml")
        tag = soup.select_one("#_nowVal") or soup.select_one(".no_today .blind")
        if tag:
            return float(tag.get_text(strip=True).replace(",", ""))
        # 긁으려던 선택자가 페이지 맨 앞부분(head/css 등)보다 훨씬 뒤에 있을
        # 수 있어서, 앞부분만 자르는 raw 스니펫으로는 안 보일 수 있다. 그래서
        # 그 선택자들이 전체 문서 "어디에든" 있긴 한지부터 따로 확인해서
        # 남긴다 - 있는데도 못 찾은 거면 선택자가 틀린 거고, 아예 없으면
        # 이 페이지는 가격을 자바스크립트로 나중에 채우는 방식이라 긁기
        # 자체가 안 되는 페이지라는 뜻.
        has_nowval = "_nowVal" in raw2
        has_no_today = "no_today" in raw2
        _log_stock_fetch_error(
            code, "페이지 긁기",
            Exception(f"가격 요소를 못 찾음 (문서 내 _nowVal 존재={has_nowval}, no_today 존재={has_no_today})"),
            raw2,
        )
    except Exception as e2:
        _log_stock_fetch_error(code, "페이지 긁기", e2, raw2)

    return None


# 2026-11-13: 사용자가 보내준 실제 진단 로그(스코넥 276040, 실제 거래정지
# 상태)로 정확한 필드를 확인함 - 실시간 API 응답의 datas[0]["tradeStopType"]
# 가 거래상태 그 자체임: 정상이면 {"code":"1","text":"운영.Trading",
# "name":"TRADING"}, 거래정지면 {"code":"2","text":"정지.Halted",
# "name":"HALTED"}로 옴(tradableStatus도 "tradable" vs "halt"로 같이 바뀜).
# 이제 이 필드로 정확히 판단한다 - 더 이상 문구를 추측해서 찾을 필요 없음.
# 다만 "관리종목"/"투자경고종목" 등 매매는 되는 채로 걸리는 다른 위험
# 상태는 아직 실제 값을 본 적이 없어서(지금까지 확인된 보유종목 중엔
# 없었음) code가 "1"도 "2"도 아닌 제3의 값으로 올 거라 추정만 함 - 그런
# 경우는 text를 그대로 보여주고 로그를 남겨서, 실제로 그 상태인 종목이
# 나오면 정확한 한글명으로 다시 맞춘다.
_TRADE_STOP_LABELS = {
    "HALTED": "거래정지",
}


def _log_stock_risk_debug(code, raw1, raw2=""):
    """위험표시 판단에 처음 보는 값(code가 "1"/"2"가 아니거나 파싱 자체가
    실패)이 나오면 실제 응답을 남겨서, 다음엔 추측 없이 정확히 맞출 수
    있게 한다(매번 쌓이면 너무 커지니 추가만 하되 - 문제되면 사용자가
    파일 지우면 됨)."""
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_risk_debug.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [{code}] =====\n")
            f.write(f"--- 실시간 API ---\n{(raw1 or '(응답 없음/실패)')[:4000]}\n")
            if raw2:
                f.write(f"--- 종목 상세 페이지(일부) ---\n{raw2[:4000]}\n")
    except Exception:
        pass


def _fetch_stock_risk_label(code):
    """종목이 거래정지 등 위험 상태인지 확인해서, 해당하면 그 상태 이름을
    돌려준다(아니면 빈 문자열). 실시간 API의 tradeStopType 필드로 판단."""
    import urllib.request

    code = re.sub(r"^[Aa]", "", code or "")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Referer": "https://finance.naver.com/",
    }

    raw1 = ""
    url1 = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"
    try:
        req = urllib.request.Request(url1, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw1 = resp.read().decode("utf-8", errors="replace")
        stop = (json.loads(raw1)["datas"][0] or {}).get("tradeStopType") or {}
        stop_name = stop.get("code") and stop.get("name")
        if stop.get("code") not in (None, "1"):
            label = _TRADE_STOP_LABELS.get(stop_name)
            if not label:
                # 처음 보는 상태 - 일단 받은 text 그대로 보여주고(아예
                # 빈칸보다 나음) 로그를 남겨서 정확한 한글명으로 맞출 자료를 모은다.
                label = (stop.get("text") or stop_name or "위험").split(".")[0]
                _log_stock_risk_debug(code, raw1)
            return label
        return ""
    except Exception as e1:
        _log_stock_fetch_error(code, "위험종목 확인(실시간 API)", e1, raw1)
        return ""


def _send_windows_toast(title, message):
    """윈도우 토스트 알림을 띄운다. winotify가 없거나(다른 OS 등) 실패하면
    조용히 넘어간다 - 알림 하나 실패했다고 전체 가격 확인 루프가 멈추면
    안 되므로."""
    try:
        from winotify import Notification
        toast = Notification(app_id="CORESYNC", title=title, msg=message, duration="long")
        toast.show()
    except Exception:
        pass


def _is_krx_market_hours():
    """평일 09:00~15:30(한국거래소 정규장)에만 시세를 확인한다 - 장 밖
    시간엔 가격이 안 바뀌니 괜히 API를 두드릴 필요 없음."""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return (9, 0) <= (now.hour, now.minute) <= (15, 30)


def _check_stocks_and_notify():
    conn = get_conn()
    try:
        stocks = conn.execute(
            "SELECT * FROM stocks WHERE active = 1 AND code != ''"
        ).fetchall()
        for s in stocks:
            # 거래정지 상태면 가격 자체가 안 내려올 수 있어서(거래가 없으니
            # 실시간가가 없음), 위험표시는 가격 조회 성공 여부와 무관하게
            # 항상 따로 확인한다.
            risk_label = _fetch_stock_risk_label(s["code"])
            if risk_label != (s["risk_label"] or ""):
                conn.execute("UPDATE stocks SET risk_label = ? WHERE id = ?", (risk_label, s["id"]))

            price = _fetch_stock_price(s["code"])
            if price is None:
                continue
            profit = (price - (s["buy_price"] or 0)) * (s["quantity"] or 0)
            is_positive = 1 if profit > 0 else 0
            if is_positive and not s["last_known_positive"]:
                _send_windows_toast(
                    f"📈 {s['name']} 플러스 전환",
                    f"현재가 {price:,.0f}원 · 평가손익 +{profit:,.0f}원",
                )
            conn.execute(
                "UPDATE stocks SET last_price = ?, last_checked_at = ?, last_known_positive = ? "
                "WHERE id = ?",
                (price, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), is_positive, s["id"]),
            )
        conn.commit()
    finally:
        conn.close()


def _stock_monitor_loop():
    import time
    while True:
        try:
            if _is_krx_market_hours():
                _check_stocks_and_notify()
        except Exception:
            pass
        time.sleep(300)


def start_stock_monitor():
    """launcher.py에서 서버 시작 직전에 호출한다(app.py를 import만 할 때는
    백그라운드 스레드가 돌지 않게 - 테스트/마이그레이션 스크립트 실행
    중에 괜히 네트워크를 두드리지 않도록)."""
    import threading
    threading.Thread(target=_stock_monitor_loop, daemon=True).start()


# 지출탭 항목(구독뿐 아니라 렌탈/통신비 등 카테고리 상관없이 전부) 이름
# 옆에 실제 브랜드 로고를 붙이고 싶은데, 로고 이미지를 미리 앱 안에 박아두는
# 대신(저작권 있는 이미지라
# 서버에 넣어두기보다, 공개된 아이콘 모음에서 실행 중인 PC가 직접 받게
# 하는 게 낫다고 판단) 앱이 실제로 돌아가는 PC에서 1회 자동으로 받아서
# static/img/sub_logos/ 밑에 캐시해둔다 - 한 번 받으면 그다음부터는 로컬
# 파일을 그대로 쓰므로 매번 인터넷에 안 나간다.
# 1순위: simple-icons(MIT 라이선스 공개 아이콘 모음)의 깔끔한 SVG. 근데
# 실제로 받아보니 토스/쿠팡/디즈니+/왓챠/웨이브/티빙처럼 한국 서비스는
# 이 모음에 아예 없어서(simple-icons 데이터 직접 확인함 - 전세계
# 유명 브랜드 위주라 국내 서비스는 거의 없음) 전부 글자 배지로만
# 떨어졌었음. 2순위: 그 서비스 공식 홈페이지의 파비콘(구글 파비콘
# 서비스로 받음 - 어떤 도메인이든 다 되는 범용 방식이라 simple-icons에
# 없는 서비스도 커버됨).
# 각 항목: (이름 패턴, simple-icons slug, 파비콘용 공식 도메인, 앱스토어
# 검색어). simple-icons는 전부 "단색 실루엣" 아이콘이라(그라데이션/미묘한
# 색조 변화가 있는 진짜 로고가 있어도 그걸 무시하고 brand hex 한 가지
# 색으로만 칠해짐 - 실제로 그게 부정확하다는 지적을 받음) 이제 애플
# 말고는 안 쓴다(애플 로고는 실제로도 완전 단색이라 문제 없음). 나머지는
# 전부 애플 앱스토어 검색 API(iTunes Search, 키 없이 공개로 쓸 수 있음)로
# 그 앱의 실제(그라데이션 등 색 그대로 살아있는) 아이콘을 받아서 쓴다 -
# 실패하면 파비콘으로 폴백.
SUBSCRIPTION_LOGO_SLUGS = [
    (re.compile(r"넷플릭스|netflix", re.I), None, "netflix.com", "Netflix"),
    (re.compile(r"디즈니|disney", re.I), None, "disneyplus.com", None),
    (re.compile(r"네이버", re.I), None, "naver.com", "네이버"),
    (re.compile(r"쿠팡|coupang", re.I), None, "coupang.com", None),
    (re.compile(r"토스|toss", re.I), None, "toss.im", None),
    (re.compile(r"왓챠|watcha", re.I), None, "watcha.com", None),
    (re.compile(r"웨이브|wavve", re.I), None, "wavve.com", None),
    (re.compile(r"티빙|tving", re.I), None, "tving.com", None),
    (re.compile(r"유튜브|youtube", re.I), None, "youtube.com", "YouTube"),
    (re.compile(r"애플|apple", re.I), "apple", "apple.com", None),
    (re.compile(r"스포티파이|spotify", re.I), None, "spotify.com", "Spotify"),
    # 2026-11-14: "구독" 아닌 다른 지출 항목(렌탈/통신비 등)에도 로고를
    # 붙여달라는 요청으로 추가 - 더 이상 구독에만 한정하지 않음.
    (re.compile(r"코웨이|coway", re.I), None, "coway.co.kr", "코웨이 IoCare"),
    (re.compile(r"클로드|claude", re.I), None, "claude.ai", "Claude by Anthropic"),
    # lguplus.com/uplus.co.kr 파비콘 둘 다 LG그룹 공통 로고라 U+ 고유의
    # 마젠타 로고가 아니었음(사용자 확인) - 사용자가 직접 보내준 스샷으로
    # 실제 앱 이름이 "U+one"인 걸 확인해서, 그 앱 아이콘을 앱스토어에서
    # 직접 받아온다(파비콘 의존 그만둠).
    (re.compile(r"LGU\+|LG\s*유플러스|유플러스", re.I), None, "uplus.co.kr", "U+one"),
    (re.compile(r"롯데렌탈|롯데렌터카", re.I), None, "lotterental.com", "롯데렌탈"),
    (re.compile(r"골프존|golfzon", re.I), None, "golfzon.com", "골프존"),
    # "카카오 골프"는 계열사 카카오VX 서비스라 실제로는 로고가 다른데,
    # kakaovx.com은 404(사용자 로그로 확인)라 도메인으로는 못 찾았음 -
    # 앱스토어에서 "카카오 VX"로 검색해서 시도해본다.
    # si_slug를 가짜("kakaogolf")로 줘서 파일명이 일반 카카오("kakao")랑
    # 안 겹치게 함(둘이 겹치면 다른 걸 따로 등록해도 캐시 파일을 서로
    # 덮어써서 둘 중 하나만 보임 - 실제로 한 번 이렇게 겹쳐서 터진 적 있음).
    # simple-icons엔 "kakaogolf"가 당연히 없어서 svg는 항상 실패하고
    # 바로 앱스토어/파비콘으로 넘어감(의도된 동작).
    (re.compile(r"카카오\s*골프|카카오\s*vx", re.I), "kakaogolf", "kakao.com", "카카오 VX"),
    (re.compile(r"카카오|kakao", re.I), None, "kakao.com", "카카오톡"),
]

# simple-icons 원본 svg 파일엔 브랜드 색이 안 박혀있어서(순수 모양 데이터만
# 있고 fill 지정이 없으면 브라우저가 기본값인 검정으로 그림) 색을 직접
# 박아 넣어야 하는데, 이게 "단색 실루엣"이라 그라데이션/미묘한 색조가
# 있는 진짜 로고는 부정확하게 뭉개버린다는 지적을 받아서 애플만 남기고
# (애플 로고는 실제로도 완전 단색이라 문제 없음) 나머진 전부 앱스토어
# 아이콘으로 바꿈. 애플만 simple-icons 자체 데이터(hex 필드)의 공식 색.
_SIMPLE_ICON_COLORS = {
    "apple": "000000",
}


def _log_subscription_logo_error(slug, error):
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_fetch_error.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(
                f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [구독로고:{slug}] 받기 실패 - "
                f"{type(error).__name__}: {error}\n"
            )
    except Exception:
        pass


def _fetch_subscription_logos_once():
    """지출탭에 등록된 항목 이름에 맞는 로고들을 추려서(카테고리 상관없이
    전부 대상), 아직 로컬에 없는 것만 받아 캐시한다(slug.svg는
    simple-icons, slug.png는 그 서비스 공식 홈페이지 파비콘 - 프론트가
    svg 먼저 시도하고 실패하면 png, 그것도 실패하면 색깔 글자 배지로
    대체함). 실패해도(인터넷 안 되는 PC, 접속 차단 등) 조용히 넘어간다 -
    로고는 있으면 좋은 장식일 뿐, 실패했다고 앱이 멈추면 안 된다."""
    import urllib.request

    try:
        conn = get_conn()
        try:
            names = [
                r["name"] for r in conn.execute("SELECT DISTINCT name FROM recurring_payments").fetchall()
            ]
        finally:
            conn.close()
    except Exception:
        return

    needed = {}  # slug(파일명) -> (simple_icons_slug, 홈페이지 도메인, 앱스토어 검색어)
    for name in names:
        for pattern, si_slug, domain, app_term in SUBSCRIPTION_LOGO_SLUGS:
            if pattern.search(name or ""):
                file_slug = si_slug or domain.split(".")[0]
                needed[file_slug] = (si_slug, domain, app_term)
                break

    logo_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "img", "sub_logos")
    os.makedirs(logo_dir, exist_ok=True)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    # 매번 로고 받아오는 방식(소스) 자체를 바꿀 때마다, 이미 파일이 있으면
    # "있음"으로 보고 넘어가는 구조라 바뀐 게 전혀 반영이 안 되는 문제가
    # 반복됐음 - 그래서 방식을 바꿀 때마다 마커 이름을 새로 붙여서 그때마다
    # 한 번씩은 전부 다시 받게 한다(최초 1회만, 그 다음부턴 정상적으로 캐시 씀).
    migrated_marker = os.path.join(logo_dir, ".appicon_migrated_v2")
    if not os.path.exists(migrated_marker):
        for fname in os.listdir(logo_dir):
            # v2: simple-icons 단색 svg(넷플릭스/네이버/유튜브/스포티파이/
            # 클로드/카카오)를 그만 쓰기로 해서 svg도 같이 지운다 - 안 지우면
            # 프론트가 여전히 그 옛날 단색 svg를 먼저 찾아서 로드해버려서
            # 새로 받은 앱스토어 png까지 아예 안 감.
            if fname.endswith(".png") or fname.endswith(".svg"):
                try:
                    os.remove(os.path.join(logo_dir, fname))
                except Exception:
                    pass
        try:
            with open(migrated_marker, "w") as f:
                f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        except Exception:
            pass

    def _download(url, dest):
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read()
        with open(dest, "wb") as f:
            f.write(data)

    def _fetch_app_store_icon(term, dest):
        # 애플 앱스토어 검색 API(iTunes Search, 공개/무키) - 이름으로 앱을
        # 찾아서 그 앱의 실제 아이콘(artworkUrl512, 없으면 100)을 받는다.
        # 홈페이지 파비콘보다 앱 아이콘이 진짜 브랜드 이미지인 서비스용.
        import json as _json
        from urllib.parse import quote
        url = f"https://itunes.apple.com/search?term={quote(term)}&country=kr&entity=software&limit=1"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read().decode("utf-8", errors="replace"))
        results = data.get("results") or []
        if not results:
            raise ValueError(f"앱스토어에서 '{term}' 검색 결과 없음")
        artwork_url = results[0].get("artworkUrl512") or results[0].get("artworkUrl100")
        if not artwork_url:
            raise ValueError(f"'{term}' 검색은 됐는데 아이콘 URL이 없음")
        _download(artwork_url, dest)
        return results[0].get("trackName")

    def _download_colored_svg(url, dest, hex_color):
        # simple-icons 원본 svg는 색이 안 박혀있어서(순수 모양만, fill 지정
        # 안 하면 브라우저 기본값인 검정으로 보임) 루트 <svg> 태그에 공식
        # 브랜드 색을 fill로 박아 넣고 저장한다 - 하위 path들이 자기 fill을
        # 따로 안 정했으면 이 색을 그대로 물려받아서 검정 대신 브랜드색으로 보임.
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            svg_text = resp.read().decode("utf-8", errors="replace")
        svg_text = svg_text.replace("<svg ", f'<svg fill="#{hex_color}" ', 1)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(svg_text)

    status_lines = []  # 이번 실행에서 각 slug가 실제로 어떻게 됐는지(성공/실패/스킵) 전부 남김

    for file_slug, (si_slug, domain, app_term) in needed.items():
        svg_dest = os.path.join(logo_dir, f"{file_slug}.svg")
        # 예전엔 색 안 박고(검정으로 보이게) 받아서 캐시해둔 적 있어서, fill이
        # 안 들어있는 옛날 파일이면 지우고 새로 받는다.
        if si_slug and os.path.exists(svg_dest):
            try:
                with open(svg_dest, "r", encoding="utf-8", errors="ignore") as f:
                    if "fill=" not in f.read(200):
                        os.remove(svg_dest)
            except Exception:
                pass
        if si_slug and os.path.exists(svg_dest):
            status_lines.append(f"{file_slug}.svg: 이미 있음(스킵)")
        elif si_slug:
            try:
                _download_colored_svg(
                    f"https://cdn.jsdelivr.net/npm/simple-icons@latest/icons/{si_slug}.svg",
                    svg_dest,
                    _SIMPLE_ICON_COLORS.get(si_slug, "000000"),
                )
                status_lines.append(f"{file_slug}.svg: simple-icons({si_slug})에서 새로 받음")
            except Exception as e:
                _log_subscription_logo_error(f"{file_slug}(simple-icons)", e)
                status_lines.append(f"{file_slug}.svg: simple-icons 받기 실패 - {e}")
        else:
            status_lines.append(f"{file_slug}.svg: si_slug 없음(파비콘/로고 API만 시도)")

        png_dest = os.path.join(logo_dir, f"{file_slug}.png")
        if os.path.exists(png_dest):
            status_lines.append(f"{file_slug}.png: 이미 있음(스킵) - 다시 받으려면 파일 지우고 재시작")
            continue

        # clearbit 로고 API는 2025-12-08에 완전히 서비스 종료돼서(사용자가
        # 보내준 로그로 확인) 뺐고, 앱스토어 검색어가 있는 항목은 그게
        # 진짜 브랜드 아이콘이라 파비콘보다 먼저 시도한다 - 실패하면 구글
        # 파비콘으로 넘어간다.
        got_it = False
        if app_term:
            try:
                track_name = _fetch_app_store_icon(app_term, png_dest)
                status_lines.append(f"{file_slug}.png: 앱스토어('{app_term}' -> {track_name})에서 받음")
                got_it = True
            except Exception as e:
                status_lines.append(f"{file_slug}.png: 앱스토어 검색 실패({e}) - 파비콘으로 대신 시도")

        if not got_it:
            try:
                _download(
                    f"https://www.google.com/s2/favicons?domain={domain}&sz=128", png_dest
                )
                status_lines.append(f"{file_slug}.png: 구글 파비콘({domain})에서 받음")
            except Exception as e:
                _log_subscription_logo_error(f"{file_slug}(favicon:{domain})", e)
                status_lines.append(f"{file_slug}.png: 구글 파비콘 받기 실패 - {e}")

    try:
        debug_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sub_logo_debug.log")
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 지출 로고 확인 결과\n")
            f.write("\n".join(status_lines) + "\n")
    except Exception:
        pass


def start_subscription_logo_fetch():
    """launcher.py에서 start_stock_monitor()와 같이 서버 시작 직전에
    한 번만 호출한다(루프 아님 - 로고는 자주 안 바뀌니 매번 다시 받을
    필요 없음). 새 구독을 추가했을 때 로고가 아직 없으면 서버를
    재시작해야 받아오지만(바로 그 순간 받아오게 만들면 매 페이지 요청마다
    인터넷에 나가는 걸 걱정해야 해서 이 정도로 충분함), 그 전까지는 색깔
    글자 배지로 보임."""
    import threading
    threading.Thread(target=_fetch_subscription_logos_once, daemon=True).start()


def serialize_inbox(conn):
    rows = conn.execute(
        "SELECT * FROM inbox_items WHERE status = 'pending' AND deleted_at IS NULL "
        "ORDER BY occurred_at DESC, id DESC"
    ).fetchall()
    # 해지된 카드는 last4가 남아있어도 매칭 후보에서 뺀다 - 실수로 같은
    # 뒷자리로 결제 알림이 들어와도 해지한 카드의 혜택으로 잘못 배정되지
    # 않게.
    cards_with_code = conn.execute(
        "SELECT id, name, issuer, last4 FROM cards WHERE last4 != '' AND closed = 0"
    ).fetchall()
    all_open_cards = conn.execute(
        "SELECT id, name, issuer FROM cards WHERE closed = 0"
    ).fetchall()
    benefits_by_card = {}
    for b in conn.execute(
        "SELECT id, card_id, name, merchant_keywords, calc_mode, always_doubled, rate_table, "
        "discount_percent, per_txn_cap FROM benefits"
    ):
        benefits_by_card.setdefault(b["card_id"], []).append(b)

    items = []
    for r in rows:
        card = None
        if r["last4"]:
            for c in cards_with_code:
                if card_codes_match(c["last4"], r["last4"]):
                    card = c
                    break

        matched_benefit = None
        matched_doubled = False

        if card and r["merchant"]:
            for b in benefits_by_card.get(card["id"], []):
                if match_benefit_keyword(r["merchant"], b["merchant_keywords"]):
                    matched_benefit = b
                    matched_doubled = b["calc_mode"] == "change_under_1000"
                    break

        # "[신한카드 자동납부 정상승인] ... [신한카드 1544-7000]"처럼 계좌/
        # 자동납부 알림은 카드 뒷자리가 괄호로 안 찍혀서(끝에 붙은 "1544-7000"
        # 같은 번호는 카드번호가 아니라 카드사 고객센터 전화번호라 last4로
        # 쓰면 안 됨) last4로 카드를 못 찾는다. 이럴 땐 발급사(issuer)가 같은
        # 카드들 중 가맹점 키워드가 매칭되는 카드+혜택이 "정확히 하나"일
        # 때만 그걸로 특정한다 - 후보가 둘 이상이면(여러 카드가 같은 키워드를
        # 등록해놨을 수 있음) 어느 카드인지 알 길이 없으니 손대지 않는다.
        if not card and r["issuer"] and r["merchant"]:
            candidates = [
                (c, b)
                for c in cards_with_code if c["issuer"] == r["issuer"]
                for b in benefits_by_card.get(c["id"], [])
                if match_benefit_keyword(r["merchant"], b["merchant_keywords"])
            ]
            if len(candidates) == 1:
                card, matched_benefit = candidates[0]
                matched_doubled = matched_benefit["calc_mode"] == "change_under_1000"

        # 위 두 방법(뒷자리, 발급사+가맹점키워드 동시매칭) 다 실패해도, 발급사만
        # 봤을 때 등록된 카드가 "정확히 하나"면 일단 카드만이라도 맞춰준다
        # (뒷자리를 아직 안 등록해놨거나, 가맹점 자동매칭 키워드가 없는/
        # OCR이 가맹점명을 살짝 다르게 읽은 경우에도 카드 자체는 하나로
        # 특정 가능하므로). 혜택까지는 못 정하니 "해당없음" 그대로 두고
        # 사람이 혜택만 고르게 한다 - 혜택은 추측하지 않는다.
        if not card and r["issuer"]:
            issuer_cards = [c for c in all_open_cards if c["issuer"] == r["issuer"]]
            if len(issuer_cards) == 1:
                card = issuer_cards[0]

        # 더모아 안내장상 "이동통신요금 자동이체"만 특별적립(2배) 대상이고,
        # "즉시납부"(그때그때 수동으로 낸 것)는 자동이체가 아니라서 2배 대상이
        # 아니다. 가맹점명 키워드(SKT/KT/LGU+ 등)만 보고는 둘을 구분 못 해서
        # 즉시납부 건도 2배로 잘못 잡혔었음 - 가맹점명에 "즉시납부"라고 명시돼
        # 있으면 2배 적용을 취소한다(1배 적립은 그대로 유지).
        if matched_doubled and "즉시납부" in (r["merchant"] or ""):
            matched_doubled = False

        is_foreign = (r["usage_type"] and "해외" in r["usage_type"]) or is_foreign_merchant(r["merchant"])
        if not matched_benefit and card and is_foreign:
            # 가맹점명만으론 아무 혜택도 안 걸렸는데, 카드사 엑셀의 "이용구분"에
            # "해외"라고 명시돼 있거나(가맹점명 추측이 아니라 확실한 신호) PayPal/
            # AliExpress처럼 "해외" 정보가 아예 없는 엑셀에서도 가맹점명으로 확실히
            # 해외임을 알 수 있으면(is_foreign_merchant) "해외"가 대상인 혜택으로
            # 바로 잡아준다. ANTHROPIC처럼 해외 온라인 결제는
            # 가맹점명으로 업종을 유추할 수가 없어서 이 방법밖에 없다. 먼저
            # merchant_keywords에 바로 "해외"가 있는 혜택을 찾고, 없으면 업종별
            # 요율표(rate_table) 안에 "해외:" 카테고리가 있는 혜택(예: Discount
            # Plan+의 Daily Plan 생활5%-해외)을 찾는다 - 후자는 category를 못
            # 골라줘도 일단 해당 혜택의 기본 할인율/한도로 계산되는데, 해외
            # 영역이 그 혜택의 다른 영역들과 할인율·한도가 똑같은 카드라면
            # 결과값은 어차피 정확하다.
            for b in benefits_by_card.get(card["id"], []):
                keywords = [k.strip().lower() for k in (b["merchant_keywords"] or "").split(",") if k.strip()]
                if "해외" in keywords:
                    matched_benefit = b
                    matched_doubled = b["calc_mode"] == "change_under_1000"
                    break
            if not matched_benefit:
                for b in benefits_by_card.get(card["id"], []):
                    if b["calc_mode"] != "percent_discount":
                        continue
                    try:
                        rt_rows = json.loads(b["rate_table"] or "[]")
                    except (TypeError, ValueError):
                        continue
                    if any(
                        isinstance(row, list) and row and isinstance(row[0], str) and row[0].startswith("해외:")
                        for row in rt_rows
                    ):
                        matched_benefit = b
                        matched_doubled = False
                        break

        if not matched_benefit and card:
            # 가맹점 키워드로 못 찾았어도, 이 카드에 "잔돈 자동계산"형 혜택이
            # 있으면(더모아처럼 거의 모든 결제가 적립 대상인 카드) 기본으로
            # 그 혜택을 제안한다. 키워드가 비어있는(catch-all) 혜택이 있으면
            # 그걸 우선 쓰고("특별적립 전용" 혜택을 따로 등록해둔 경우 대비),
            # 없으면(대부분의 경우 - 혜택이 하나뿐인 경우) 아무 change_under_1000
            # 혜택이나 후보로 삼는다 - 혜택을 하나만 등록한 사람도 자동매칭돼야 하므로.
            change_benefits = [
                b for b in benefits_by_card.get(card["id"], []) if b["calc_mode"] == "change_under_1000"
            ]
            catch_all = [b for b in change_benefits if not b["merchant_keywords"]]
            fallback_pool = catch_all or change_benefits
            if fallback_pool:
                matched_benefit = fallback_pool[0]
                # merchant_keywords가 있는 혜택은 "매칭되면 2배, 안 되면 1배"를
                # 구분하려고 키워드를 등록해둔 것이므로, 여기(키워드 매칭 실패)로
                # 온 이상 always_doubled를 적용하면 안 된다. always_doubled는
                # 키워드가 아예 없는(=구분할 필요 없이 통째로 2배인) 혜택에만 쓴다.
                matched_doubled = bool(matched_benefit["always_doubled"]) and not matched_benefit["merchant_keywords"]

        is_online = bool(r["raw_text"] and "온라인" in r["raw_text"])

        if not matched_benefit and card:
            # 더모아형(change_under_1000)이 아니라, "기본적립"처럼 특정 업종
            # 구분 없이 전체 결제에 그냥 적용되는 percent_discount 혜택도 있다
            # (merchant_keywords를 일부러 비워서 "그 외 전부"를 뜻하게 등록한
            # 경우 - 씨티카드 특별적립/기본적립처럼 업종별 혜택과 전체 기본
            # 적립이 같이 있는 카드가 그렇다). 더 구체적인 혜택(키워드 있는
            # 특별적립 등)으로 못 찾았을 때만 이걸 기본값으로 쓴다.
            percent_catch_all = [
                b for b in benefits_by_card.get(card["id"], [])
                if b["calc_mode"] == "percent_discount" and not b["merchant_keywords"]
            ]
            if percent_catch_all:
                matched_benefit = percent_catch_all[0]
                matched_doubled = False

        # "잡화:다이소" 온라인 결제처럼, 실제 저장 땐 _apply_calc_mode가 0%로
        # 억제하는데도 알림 목록 추천 배지는 그 사정을 몰라서 "🎯 Daily
        # Plan"이 마치 확정된 혜택인 것처럼 계속 떴다(사용자 지적: "다이소
        # 혜택 안 빠졌잖아"). 처음엔 catch-all(키워드 없는 혜택) 분기
        # 안에만 이 확인을 넣었는데, matched_benefit이 그보다 앞선 분기
        # (merchant_keywords 직접 매칭 등)에서 이미 정해지는 경우엔 전혀
        # 안 타서 똑같은 문제가 또 재현됐다 - matched_benefit이 최종적으로
        # 뭘로 정해지든 상관없이, percent_discount+rate_table이면 여기서
        # 한 번 더 확인해서 억제한다.
        if (
            matched_benefit
            and matched_benefit["calc_mode"] == "percent_discount"
            and matched_benefit["rate_table"]
        ):
            _, _, _, _, _, rt_category = match_rate_table(
                r["merchant"], matched_benefit["rate_table"],
                matched_benefit["discount_percent"], matched_benefit["per_txn_cap"],
            )
            if rt_category == "잡화" and is_online:
                matched_benefit = None
                matched_doubled = False

        # 카드사 엑셀의 "이용구분"에 해외/할부라고 명시돼 있으면, 가맹점명으로
        # 유추할 필요 없이 그 자체로 확실한 신호이므로 더모아형(잔돈 적립) 혜택은
        # 무조건 특별적립(2배) 대상으로 본다(안내장 기준: 해외이용, 할부거래는
        # 항상 2배). "이용구분" 정보가 아예 없는 엑셀이면 PayPal/AliExpress처럼
        # 가맹점명으로 확실히 해외인 경우만 보조로 잡는다.
        if matched_benefit and matched_benefit["calc_mode"] == "change_under_1000":
            usage_type = r["usage_type"] or ""
            if "해외" in usage_type or "할부" in usage_type or is_foreign_merchant(r["merchant"]):
                matched_doubled = True

        items.append({
            "id": r["id"],
            "raw_text": r["raw_text"],
            "amount": r["amount"],
            "last4": r["last4"],
            "issuer": r["issuer"],
            "merchant": r["merchant"],
            "occurred_at": r["occurred_at"],
            "matched_card_id": card["id"] if card else None,
            "matched_card_name": card["name"] if card else None,
            "matched_benefit_id": matched_benefit["id"] if matched_benefit else None,
            "matched_benefit_name": matched_benefit["name"] if matched_benefit else None,
            "matched_benefit_doubled": matched_doubled,
            "is_foreign": is_foreign,
            # "잡화:다이소"처럼 매장 할인 키워드가 온라인몰 결제에도 그대로
            # 걸리는 경우가 있는데, 실제로는 매장 결제만 그 할인 대상이고
            # 온라인은 대상이 아닌 카드가 있다(사용자 확인: 신한 Discount
            # Plan+의 "잡화" 10%는 오프라인 전용) - 알림 원문에 "온라인"이
            # 찍혀 있으면 이 결제가 온라인임을 화면 미리보기/저장 양쪽에서
            # 알 수 있게 표시만 해둔다(실제 억제는 _apply_calc_mode에서).
            "is_online": is_online,
        })
    return items


def serialize_no_benefit(conn):
    # 고정지출/보험으로 연결된 "혜택없음" 알림은 inbox_items.card_id가 아니라
    # recurring_payments.card_id/insurances.card_id로 카드가 정해지는데,
    # card_id만 보면 그게 비어있어서 카드별 "혜택없음" 목록에서 통째로
    # 빠져버린다 - 실적엔 잡히는데 내역엔 안 보이는 사고(실제로 지적받음).
    # 근데 그 card_id를 "지금 당장" 값으로 채우면 또 다른 사고가 난다 -
    # 고정지출/보험의 결제카드를 나중에 바꾸면 이미 지나간 달 알림까지
    # 전부 새 카드로 보여버림(실제로 발생). 그래서 매칭 당시 찍어둔
    # matched_card_label 스냅샷으로 카드를 찾고, 스냅샷이 없는 옛날
    # 데이터만 지금 card_id를 보조로 쓴다(_auto_spend와 동일한 우선순위).
    cards = conn.execute("SELECT id, name, short_name, last4 FROM cards").fetchall()
    card_by_label = {(c["short_name"] or c["name"]): c for c in cards}
    card_by_id = {c["id"]: c for c in cards}

    rows = conn.execute(
        """SELECT inbox_items.*,
                  recurring_payments.card_id AS recurring_card_id,
                  recurring_payments.payment_method AS recurring_payment_method,
                  insurances.card_id AS insurance_card_id,
                  insurances.payment_method AS insurance_payment_method
           FROM inbox_items
           LEFT JOIN recurring_payments ON recurring_payments.id = inbox_items.recurring_id
           LEFT JOIN insurances ON insurances.id = inbox_items.insurance_id
           WHERE inbox_items.status = 'no_benefit' AND inbox_items.deleted_at IS NULL
           ORDER BY inbox_items.occurred_at DESC, inbox_items.id DESC"""
    ).fetchall()

    def effective_card(r):
        if r["card_id"]:
            return card_by_id.get(r["card_id"])
        if not r["recurring_id"] and not r["insurance_id"]:
            return None
        if r["matched_card_label"]:
            return card_by_label.get(r["matched_card_label"])
        live_card_id = r["recurring_card_id"] if r["recurring_id"] else r["insurance_card_id"]
        if live_card_id:
            return card_by_id.get(live_card_id)
        method = r["recurring_payment_method"] if r["recurring_id"] else r["insurance_payment_method"]
        last4 = _extract_method_last4(method)
        if last4:
            for c in cards:
                if c["last4"] and card_codes_match(c["last4"], last4):
                    return c
        return None

    result = []
    for r in rows:
        card = effective_card(r)
        result.append({
            "id": r["id"],
            "raw_text": r["raw_text"],
            "amount": r["amount"],
            "merchant": r["merchant"],
            "occurred_at": r["occurred_at"],
            "card_id": card["id"] if card else None,
            "card_name": card["name"] if card else None,
        })
    return result


def serialize_trash(conn):
    """삭제(완전삭제 아님) 처리한 알림들 - 휴지통. 상태 상관없이 deleted_at만
    찍혀 있으면 여기 보이고, 복원하면 deleted_at을 지워서 원래 있던 자리로
    돌아가게 한다(큐든 혜택없음 처리 내역이든 status는 그대로 보존돼 있음)."""
    rows = conn.execute(
        """SELECT inbox_items.*, cards.name AS card_name
           FROM inbox_items LEFT JOIN cards ON cards.id = inbox_items.card_id
           WHERE inbox_items.deleted_at IS NOT NULL
           ORDER BY inbox_items.deleted_at DESC, inbox_items.id DESC"""
    ).fetchall()
    return [
        {
            "id": r["id"],
            "raw_text": r["raw_text"],
            "amount": r["amount"],
            "merchant": r["merchant"],
            "occurred_at": r["occurred_at"],
            "card_id": r["card_id"],
            "card_name": r["card_name"],
            "deleted_at": r["deleted_at"],
        }
        for r in rows
    ]


def _match_card_and_benefit_for_item(r, cards_with_code, all_open_cards, benefits_by_card):
    """serialize_inbox의 카드/혜택 자동매칭 로직을 재사용할 수 있게 뺐다 -
    병원 결제를 무조건 "해당없음"으로 밀어버렸던 지난 실수(실제로 "의료
    할인" 혜택을 카드에 등록해둔 사용자가 있어서, 그 혜택이 매칭될
    기회조차 없이 전부 해당없음으로 가로채버렸음)를 되돌리면서, 이미
    잘못 "해당없음" 처리된 건들을 이 똑같은 로직으로 다시 매칭 시도하기
    위함."""
    card = None
    if r["last4"]:
        for c in cards_with_code:
            if card_codes_match(c["last4"], r["last4"]):
                card = c
                break
    matched_benefit = None
    matched_doubled = False
    if card and r["merchant"]:
        for b in benefits_by_card.get(card["id"], []):
            if match_benefit_keyword(r["merchant"], b["merchant_keywords"]):
                matched_benefit = b
                matched_doubled = b["calc_mode"] == "change_under_1000"
                break
    if not card and r["issuer"] and r["merchant"]:
        candidates = [
            (c, b)
            for c in cards_with_code if c["issuer"] == r["issuer"]
            for b in benefits_by_card.get(c["id"], [])
            if match_benefit_keyword(r["merchant"], b["merchant_keywords"])
        ]
        if len(candidates) == 1:
            card, matched_benefit = candidates[0]
            matched_doubled = matched_benefit["calc_mode"] == "change_under_1000"
    if not card and r["issuer"]:
        issuer_cards = [c for c in all_open_cards if c["issuer"] == r["issuer"]]
        if len(issuer_cards) == 1:
            card = issuer_cards[0]
    return card, matched_benefit, matched_doubled


def _retry_medical_no_benefit_items():
    """지난 실수로 병원/약국 결제를 전부 "해당없음"으로 자동 처리해버린
    적이 있어서(의료 할인 혜택이 등록돼 있었는데 그 혜택이 매칭될
    기회조차 없었음), 그렇게 잘못 처리된 것으로 보이는 건(해당없음 +
    혜택 미배정 + 의료기관 가맹점)들을 서버 시작할 때 지금 등록된 혜택
    키워드로 다시 매칭 시도한다. 이미 사람이 직접 "해당없음"으로 확정한
    건과 구분이 안 되지만, 실제로 맞는 혜택이 있으면 다시 걸리고 없으면
    그대로 해당없음에 남으니 해는 없다."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM inbox_items WHERE status = 'no_benefit' AND benefit_id IS NULL "
            "AND deleted_at IS NULL AND merchant != ''"
        ).fetchall()
        medical_rows = [r for r in rows if is_medical_merchant(r["merchant"])]
        if not medical_rows:
            return
        cards_with_code = conn.execute(
            "SELECT id, name, issuer, last4 FROM cards WHERE last4 != '' AND closed = 0"
        ).fetchall()
        all_open_cards = conn.execute("SELECT id, name, issuer FROM cards WHERE closed = 0").fetchall()
        benefits_by_card = {}
        for b in conn.execute(
            "SELECT id, card_id, name, merchant_keywords, calc_mode, always_doubled, rate_table FROM benefits"
        ):
            benefits_by_card.setdefault(b["card_id"], []).append(b)
        for r in medical_rows:
            card, matched_benefit, _ = _match_card_and_benefit_for_item(
                r, cards_with_code, all_open_cards, benefits_by_card
            )
            if matched_benefit:
                conn.execute(
                    "UPDATE inbox_items SET status = 'pending', card_id = ?, benefit_id = NULL WHERE id = ?",
                    (card["id"] if card else None, r["id"]),
                )
        conn.commit()
    except BaseException as e:
        _reraise_if_fatal(e)
    finally:
        conn.close()


def serialize_full():
    conn = get_conn()
    try:
        return {
            "cards": _serialize_cards(conn),
            "inbox": serialize_inbox(conn),
            "no_benefit": serialize_no_benefit(conn),
            "loans": serialize_loans(conn),
            "recurring_payments": serialize_recurring_payments(conn),
            "insurances": serialize_insurances(conn),
            "medical_expenses": serialize_medical_expenses(conn),
            "suspected_duplicates": find_suspected_duplicates(conn),
            "stocks": serialize_stocks(conn),
            "trash": serialize_trash(conn),
            "unassigned_attachments": serialize_unassigned_attachments(conn),
            "server_started_at": _SERVER_STARTED_AT.strftime("%m/%d %H:%M:%S"),
        }
    finally:
        conn.close()


def serialize_stocks(conn):
    """화면에 뿌릴 땐 백그라운드 스레드가 미리 갱신해둔 last_price(캐시)를
    그대로 쓴다 - 페이지 열 때마다 네이버 시세 API를 직접 호출하면
    느려지거나 그 API가 막혔을 때 페이지 전체가 멈출 수 있어서."""
    rows = conn.execute("SELECT * FROM stocks ORDER BY sort_order ASC, id ASC").fetchall()
    result = []
    for s in rows:
        buy_price = s["buy_price"] or 0
        quantity = s["quantity"] or 0
        sell_price = s["sell_price"] or 0
        # 매도한(active=0) 종목은 매도가가 등록돼 있으면 그 가격으로 손익을
        # 고정한다 - 안 그러면 시세가 계속 바뀌는 것처럼 보여서 이미 판
        # 종목의 손익이 헷갈리게 움직인다(백그라운드도 active=1만 확인하니
        # last_price 자체는 매도 시점 이후로 안 바뀌지만, 명시적으로 고정해두는
        # 쪽이 안전).
        effective_price = sell_price if (not s["active"] and sell_price) else (s["last_price"] or 0)
        profit = (effective_price - buy_price) * quantity if effective_price else None
        profit_percent = (
            round((effective_price - buy_price) / buy_price * 100, 2)
            if effective_price and buy_price else None
        )
        result.append({
            "id": s["id"],
            "name": s["name"],
            "code": s["code"],
            "broker": s["broker"],
            "buy_price": buy_price,
            "quantity": quantity,
            "sell_price": sell_price or None,
            "last_price": effective_price or None,
            "last_checked_at": s["last_checked_at"],
            "profit": profit,
            "profit_percent": profit_percent,
            "active": bool(s["active"]),
            "memo": s["memo"],
            "sort_order": s["sort_order"],
            "risk_label": s["risk_label"] or None,
        })
    return result


def serialize_recurring_payments(conn):
    rows = conn.execute(
        "SELECT * FROM recurring_payments ORDER BY payment_day ASC, sort_order ASC, id ASC"
    ).fetchall()
    return _attach_match_info(conn, rows, "recurring_id")


def _savings_maturity_projection(insurance):
    """적금/적립식 예금처럼 "보험" 테이블에 같이 등록해서 관리하는 항목은
    (순수 보험엔 없는) current_balance(현재 적립금액)와 interest_rate(적용
    이율)가 들어있을 때만, 지금 페이스(매달 monthly_premium만큼 입금)로
    만기까지 넣으면 최종 얼마가 되는지 추정한다. 단리 기준(각 회차
    입금액에 그 입금일부터 만기일까지 남은 일수만큼만 이자가 붙는다고
    봄 - 적금 이자는 보통 만기에 한 번에 지급돼서 현재 잔액엔 이자가
    아직 안 들어있다고 가정). 은행 계산방식이 다를 수 있어 "예상치".
    current_balance나 interest_rate가 없거나 만기일을 날짜로 특정할 수
    없으면(예: '종신') 계산 안 하고 None."""
    balance = insurance["current_balance"] or 0
    rate = insurance["interest_rate"] or 0
    if balance <= 0 or rate <= 0:
        return None
    try:
        maturity = date.fromisoformat((insurance["maturity_date"] or "")[:10])
    except ValueError:
        return None
    today = date.today()
    if maturity <= today:
        return None
    rate_frac = rate / 100

    total_deposits = balance
    interest = balance * rate_frac * (maturity - today).days / 365

    monthly = insurance["monthly_premium"] or 0
    if monthly > 0 and insurance["payment_day"]:
        try:
            payment_end = date.fromisoformat((insurance["payment_end_date"] or "")[:10])
        except ValueError:
            payment_end = maturity
        last_deposit = min(payment_end, maturity)
        cur = _next_payment_date(insurance["payment_day"])
        guard = 0
        while cur <= last_deposit:
            days_left = (maturity - cur).days
            interest += monthly * rate_frac * days_left / 365
            total_deposits += monthly
            cur = _add_month(cur, insurance["payment_day"])
            guard += 1
            if guard > 600:
                break

    return {
        "maturity_date": maturity.isoformat(),
        "total_deposits": round(total_deposits),
        "estimated_interest": round(interest),
        "maturity_amount": round(total_deposits + interest),
    }


def serialize_insurances(conn):
    rows = conn.execute(
        "SELECT * FROM insurances ORDER BY payment_day ASC, sort_order ASC, id ASC"
    ).fetchall()
    result = _attach_match_info(conn, rows, "insurance_id")
    for item, row in zip(result, rows):
        item["maturity_projection"] = _savings_maturity_projection(row)
    return result


def serialize_medical_expenses(conn):
    """받은 알림 중 가맹점명으로 봤을 때 병원/약국 등 의료기관 결제로 보이는 것들을
    모아서, 실손 등으로 청구했는지(medical_claimed) 체크할 수 있게 보여준다.
    "이건 아님" 처리(medical_excluded)한 건 다시 안 보여준다."""
    rows = conn.execute(
        "SELECT * FROM inbox_items WHERE merchant != '' AND medical_excluded = 0 "
        "AND deleted_at IS NULL ORDER BY occurred_at DESC, id DESC"
    ).fetchall()
    return [
        {
            "id": r["id"],
            "amount": r["amount"],
            "merchant": r["merchant"],
            "occurred_at": r["occurred_at"],
            "claimed": bool(r["medical_claimed"]),
        }
        for r in rows
        if is_medical_merchant(r["merchant"])
    ]


def find_suspected_duplicates(conn):
    """같은 날 같은 가맹점에서 금액까지 같게(또는 거의 같게) 2번 이상 찍힌
    알림을 묶어서 보여준다 - 같은 결제가 두 경로(폰 알림 자동수집 + 엑셀
    명세서 업로드)로 각각 들어와서 실제로는 한 건인데 행이 두 개로 남은
    경우를 잡기 위함(해외결제는 알림 시점 추정환율과 명세서 확정환율이
    달라 금액이 완전히 똑같지 않을 수 있어서 10%까지는 같은 걸로 본다).
    금액이 다르면 같은 가맹점 같은 날이어도 묶지 않는다 - 하루에 같은
    가맹점을 여러 번 들러 서로 다른 걸 사는 경우(예: 생활용품점)가 많아서,
    금액 무시하고 다 묶으면 실제 중복이 아닌 게 너무 많이 걸림.
    날짜가 하루라도 벌어지면 절대 안 묶는다(LG U+ 소액결제처럼 매일 반복
    찍히는 정액 결제가 전부 딸려 들어오는 걸 막기 위함)."""
    rows = conn.execute(
        "SELECT * FROM inbox_items WHERE status != 'cancelled' AND dup_ignored = 0 "
        "AND deleted_at IS NULL AND merchant != '' AND amount IS NOT NULL ORDER BY occurred_at"
    ).fetchall()

    card_label_for = _card_label_matcher(conn)
    benefits_by_id = {b["id"]: b for b in conn.execute("SELECT id, name FROM benefits")}
    recurring_by_id = {r["id"]: r for r in conn.execute("SELECT id, name FROM recurring_payments")}
    insurance_by_id = {
        r["id"]: r for r in conn.execute("SELECT id, company, product_name FROM insurances")
    }

    def linked_label(r):
        if r["recurring_id"] is not None and r["recurring_id"] in recurring_by_id:
            return f"고정지출 · {recurring_by_id[r['recurring_id']]['name']}"
        if r["insurance_id"] is not None and r["insurance_id"] in insurance_by_id:
            ins = insurance_by_id[r["insurance_id"]]
            return f"보험 · {ins['company']} {ins['product_name']}".strip()
        if r["status"] == "assigned" and r["benefit_id"] in benefits_by_id:
            return f"혜택 · {benefits_by_id[r['benefit_id']]['name']}"
        if r["status"] == "no_benefit":
            return "혜택 없음 처리됨"
        return "대기 중(미배정)"

    def is_pair(a, b):
        if not ((not a["last4"] or not b["last4"]) or card_codes_match(a["last4"], b["last4"])):
            return False
        if a["occurred_at"][:10] != b["occurred_at"][:10]:
            return False
        amt_a, amt_b = a["amount"], b["amount"]
        bigger = max(amt_a, amt_b)
        return bigger > 0 and abs(amt_a - amt_b) / bigger <= 0.1

    by_merchant = defaultdict(list)
    for r in rows:
        by_merchant[_merchant_base(r["merchant"])].append(r)

    groups = []
    for items in by_merchant.values():
        if len(items) < 2:
            continue
        # 같은 가맹점 안에서 서로 "의심 쌍"인 것들끼리 union-find로 묶는다
        # (A-B, B-C가 각각 의심쌍이면 A-C가 직접 조건을 만족 안 해도 한
        # 묶음으로 본다 - 세 소스에서 각각 들어온 경우를 위함).
        parent = {r["id"]: r["id"] for r in items}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x, y):
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[rx] = ry

        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if is_pair(items[i], items[j]):
                    union(items[i]["id"], items[j]["id"])

        clusters = defaultdict(list)
        for r in items:
            clusters[find(r["id"])].append(r)
        groups.extend(c for c in clusters.values() if len(c) >= 2)

    return [
        {
            "merchant": cluster[0]["merchant"],
            "items": [
                {
                    "id": r["id"],
                    "amount": r["amount"],
                    "occurred_at": r["occurred_at"],
                    "merchant": r["merchant"],
                    "card_label": card_label_for(r["last4"]),
                    "source": r["source"],
                    "linked_label": linked_label(r),
                }
                for r in cluster
            ],
        }
        for cluster in groups
    ]


def _parse_card_id(value):
    """결제수단으로 등록된 카드를 고른 경우 그 카드 id, 아니면 None(현금/계좌이체 등
    자유입력이거나 선택 안 함)으로 정리한다."""
    if value in (None, "", "null"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_payment_method(conn, card_id, fallback_text):
    """card_id가 있으면(등록된 카드를 결제수단으로 고른 경우) 카드탭의 현재
    이름+뒷자리를 그 자리에서 조합해서 돌려준다 - 카드탭에서 이름을 바꾸면
    고정지출/보험 쪽도 다음 조회 때 바로 반영되고, 따로 동기화할 필요가 없다.
    card_id가 없으면(현금/계좌이체 등) 저장된 텍스트를 그대로 쓴다."""
    if card_id:
        card = conn.execute("SELECT name, last4 FROM cards WHERE id = ?", (card_id,)).fetchone()
        if card:
            return f"{card['name']}({card['last4']})" if card["last4"] else card["name"]
    return fallback_text or ""


def _snapshot_card_label(conn, table, row_id):
    """고정지출/보험 항목이 매칭되는 바로 그 순간의 결제카드 이름을
    찍어서 돌려준다(카드탭 표기와 같은 짧은 이름). 나중에 그 항목의
    결제카드를 다른 카드로 바꿔도, 이미 지난 "내역"은 바뀌기 전 카드로
    계속 보여야 하기 때문(실제로 카드를 바꿨더니 지난 내역까지 전부
    새 카드로 보이는 사고가 있었음) - inbox_items.matched_card_label에
    이 값을 같이 저장해둔다."""
    row = conn.execute(f"SELECT card_id, payment_method FROM {table} WHERE id = ?", (row_id,)).fetchone()
    if not row:
        return ""
    if row["card_id"]:
        card = conn.execute("SELECT name, short_name FROM cards WHERE id = ?", (row["card_id"],)).fetchone()
        if card:
            return card["short_name"] or card["name"]
    return row["payment_method"] or ""


def _match_time_card_label(conn, item, table, row_id):
    """매칭되는 바로 그 순간 "내역"에 영구히 찍어둘 결제카드 이름을 정한다.
    1순위는 그 알림 자체에 실제로 찍힌 last4(실시간 알림 내용이 곧 사실이므로
    최우선) - last4가 있으면 카드탭에서 바로 찾는다. last4가 아예 없을 때만
    (구독성 알림처럼 카드정보가 안 찍히는 경우) 고정지출/보험 항목에 등록된
    카드로 대신 채운다. 한 번 이렇게 찍히면 나중에 등록 카드를 바꾸거나
    로직이 바뀌어도 이 값은 절대 다시 계산되지 않는다(매칭 당시 그대로 고정)."""
    label = _card_label_matcher(conn)(item["last4"]) if item else None
    return label or _snapshot_card_label(conn, table, row_id)


def _card_label_matcher(conn):
    """받은 알림의 last4(카드 뒷자리, 3~4자리 섞여 들어옴)를 보고 카드탭에
    등록된 카드의 요약명을 찾아주는 함수를 만들어 돌려준다. 고정지출/보험의
    "내역"이나 매칭 후보 목록처럼, 알림이 어느 카드로 결제된 건지 바로
    보여줘야 하는 곳에서 공통으로 쓴다."""
    cards_with_code = conn.execute("SELECT name, last4, short_name FROM cards WHERE last4 != ''").fetchall()

    def card_label_for(last4):
        if not last4:
            return None
        for c in cards_with_code:
            if card_codes_match(c["last4"], last4):
                return c["short_name"] or c["name"]
        return None

    return card_label_for


def _attach_match_info(conn, rows, fk_column):
    """고정지출/보험 공용: 이번 달 받은 알림 중 이 항목과 매칭된 게 있는지 붙여주고,
    결제수단이 등록된 카드(card_id)면 카드탭 기준으로 실시간 표기를 만들어 넣는다.
    과거 달 매칭 내역도 match_history로 전부 붙여서(몇 달치를 한 번에 엑셀로
    올려도) "내역" 버튼으로 지난 달 것까지 다 보이게 한다."""
    current_ym = current_year_month()
    # _match_time_card_label을 넣기 전에 매칭됐던 옛날 내역들은 matched_card_label이
    # 비어있어서, 그런 경우에만 그 알림 자체의 실제 last4로 보조 조회한다
    # (등록된 카드로 대신 보여주는 위험한 추측이 아니라, 알림 문자에 실제로
    # 찍힌 뒷자리를 그대로 찾는 것 - "내역에서 카드명이 통째로 사라졌다"는
    # 지적을 받고 복구함). matched_card_label이 있으면 그게 최우선이고
    # 다시 계산되지 않는다.
    card_label_for = _card_label_matcher(conn)

    result = []
    for r in rows:
        item = dict(r)
        item["payment_method"] = _resolve_payment_method(conn, r["card_id"], r["payment_method"])
        # "내역"에 보이는 결제가 전월실적 합계에서 빠지는지(실적 제외
        # 키워드에 걸리는지)를 같이 보여주기 위함 - card_diag 파일에서만
        # 보이던 걸 화면에서도 바로 보이게. 연결된 카드가 없으면(결제수단
        # 미등록 등) 실적 계산 자체가 그 카드 기준이 아니라서 표시 안 함.
        perf_excluded_keywords = None
        if r["card_id"]:
            card_row = conn.execute(
                "SELECT perf_excluded_keywords FROM cards WHERE id = ?", (r["card_id"],)
            ).fetchone()
            if card_row:
                perf_excluded_keywords = card_row["perf_excluded_keywords"]
        history = conn.execute(
            f"""SELECT id, amount, merchant, occurred_at, last4, matched_card_label
               FROM inbox_items WHERE {fk_column} = ? ORDER BY occurred_at DESC""",
            (r["id"],),
        ).fetchall()
        item["match_history"] = [
            {
                "id": h["id"],
                "amount": h["amount"],
                "merchant": h["merchant"],
                "occurred_at": h["occurred_at"],
                # 1순위: 매칭되는 순간 찍어둔 스냅샷(한 번 찍히면 절대 안
                # 바뀜). 2순위(스냅샷이 없는 옛날 내역만): 그 알림 자체의
                # 실제 last4로 지금 바로 조회 - 등록된 카드가 아니라 알림에
                # 실제로 찍힌 뒷자리라 안전함. 둘 다 없으면 빈칸(추측 금지).
                "card_label": h["matched_card_label"] or card_label_for(h["last4"]) or None,
                "is_perf_excluded": (
                    bool(r["card_id"]) and is_performance_excluded(h["merchant"], perf_excluded_keywords)
                ),
            }
            for h in history
        ]
        match = next((h for h in history if h["occurred_at"][:7] == current_ym), None)
        item["matched_this_month"] = bool(match)
        item["matched_amount"] = match["amount"] if match else None
        item["matched_merchant"] = match["merchant"] if match else None
        item["matched_occurred_at"] = match["occurred_at"] if match else None
        result.append(item)
    return result


def _extract_method_last4(payment_method):
    """고정지출/보험의 결제수단 문자열 끝에 '(2087)'처럼 붙은 카드 뒷자리를 뽑는다.
    현금/계좌이체처럼 괄호가 없으면 빈 문자열(= 카드 뒷자리로는 거를 수 없음)."""
    m = re.search(r"\((\d{3,4})\)\s*$", payment_method or "")
    return m.group(1) if m else ""


def _merchant_base(merchant):
    """가맹점명을 "해제" 시 기억해둘 때/나중에 비교할 때 쓰는 정규화 형태로
    만든다. "가맹점명: ㈜골프존" 같은 접두어, ㈜/(주) 표기 차이, 공백만
    다르고 사실상 같은 문구를 같은 것으로 보기 위함."""
    s = re.sub(r"^가맹점명\s*[:：]\s*", "", (merchant or "").strip())
    s = s.replace("㈜", "(주)")
    s = re.sub(r"\s+", "", s)
    return s.lower()


_NAME_TOKEN_STOPWORDS = {
    "hl", "주식회사", "(주)",
    # 특정 업체/서비스를 안 가리키고 그냥 업종/카테고리를 가리키는 흔한
    # 단어들 - 이런 단어만 보고 매칭하면 "LGU+ 통신요금 인터넷"의
    # "인터넷"이 전혀 무관한 "인터넷상거래"(온라인쇼핑 일반 표기) 결제를
    # 전부 끌어오는 식의 사고가 난다(실제로 발생 확인됨). 대기업 집단명
    # 제외(_CONGLOMERATE_STOPWORDS)와 같은 이유, 그냥 카테고리가 다를 뿐.
    "인터넷", "통신", "통신비", "통신요금", "휴대폰", "모바일",
}

# 여러 계열사/서비스를 거느린 대기업 집단 이름은 그 자체로는 아무것도
# 구분해주지 못한다 - "카카오"만 보고 매칭하면 카카오골프/카카오T택시/
# 카카오페이/카카오뱅크가 전부 같은 걸로 잡혀버린다("카카오 골프" 9,900원
# 구독이 카카오T택시 6,900원/14,700원 탑승 기록까지 전부 가져가버린 실제
# 사고 사례). "삼천리"(도시가스), "클로드"(Claude) 같은 건 그 자체로
# 충분히 특정 업체를 가리키지만, 이런 대기업 집단명은 안 그렇다.
_CONGLOMERATE_STOPWORDS = {
    "카카오", "네이버", "신한", "삼성", "현대", "롯데", "sk", "lg", "gs",
    "한화", "cj", "kt", "nh", "kb", "우리", "하나", "ibk", "농협", "케이티",
}


def _name_tokens(name):
    """고정지출/보험 이름(또는 별칭)에서 가맹점명 매칭에 쓸만한 단어만 뽑는다
    (너무 짧거나 흔한 접두어는 제외 - 도시가스/통신사처럼 공급사 이름이
    앞에 붙는 경우가 많아서, 'HL 삼천리 도시가스'에서 'HL'은 걸러낸다).
    금액까지 같은 후보 중에서 하나로 좁힐 때(이미 금액으로 걸러진 안전한
    상황)만 쓴다 - 아래 _safe_name_tokens보다 느슨하다."""
    tokens = re.split(r"[\s/()·\-]+", name or "")
    return [t for t in tokens if len(t) >= 2 and t.lower() not in _NAME_TOKEN_STOPWORDS]


def _safe_name_tokens(name):
    """금액이 전혀 안 맞는 상태에서(가맹점명 하나만 보고) 넓은 범위의 알림들
    중에 매칭 대상을 찾을 때 쓴다. 이때는 _name_tokens보다 훨씬 엄격해야
    한다 - 3자 미만 단어와 대기업 집단명(카카오/삼성/LG 등)은 그 자체로
    아무것도 특정하지 못해서, 금액 검증 없이 쓰면 완전히 상관없는 가맹점을
    잡아버린다("카카오 골프"가 "카카오T택시"를 가져가버린 사례). "101호"처럼
    숫자+호/동/단/층으로만 된 토큰도 같은 이유로 뺀다 - 아파트 전기세 두
    개를 "101호"/"102호"로 구분하려고 이름에 넣어둔 건데, 전혀 다른 청구서
    (예: "중산마을1단 0103동0101호" 관리비)의 동·호수 표기에 우연히 "101호"가
    그대로 들어있어서 가맹점명이 하나도 안 겹치는 별개 결제가 잘못 묶인
    실제 사고가 있었다."""
    tokens = re.split(r"[\s/()·\-]+", name or "")
    return [
        t for t in tokens
        if len(t) >= 3 and t.lower() not in _NAME_TOKEN_STOPWORDS
        and t.lower() not in _CONGLOMERATE_STOPWORDS
        and not re.match(r"^\d+(호|동|단|층)$", t)
    ]


def _try_auto_match(conn, inbox_id, fk_column, table, amount_column, name_column):
    """받은 알림 하나를 활성 항목(고정지출 또는 보험)에 자동으로 묶는다.

    1차: 금액이 정확히 같고(결제수단에 카드 뒷자리가 있으면 그것까지 일치하는)
    항목이 '정확히 하나'일 때 매칭 - 구독료처럼 매달 같은 금액인 경우. 금액(+카드)
    까지 같은 후보가 둘 이상이면(서로 다른 구독이 우연히 같은 금액인 경우 -
    "골프존"과 "카카오 골프"가 둘 다 9,900원인 사례) 가맹점명으로 한 번 더
    좁혀본다(_name_tokens, 3자 이상만) - 이미 금액으로 걸러진 상태라 비교적
    느슨하게 봐도 안전하다.
    2차(1차에서 금액이 맞는 후보가 하나도 없을 때만): 가맹점명에 항목 이름이나
    별칭(billing_alias)의 단어가 포함되는 항목이 '정확히 하나'일 때 매칭 -
    도시가스/전기세처럼 매달 청구 금액이 바뀌는 고정지출은 금액으로는 절대 못
    잡고, 보험료처럼 금액은 고정이어도 카드/계좌 알림엔 상품명이 아니라 전혀
    다른 이름으로 찍히는 경우도 있어서 이름만으론 못 잡으므로 - 그래서 둘 다
    같이 본다. 이때는 _safe_name_tokens(금액 검증이 전혀 없는 상태라 훨씬
    엄격함 - 3자 미만 단어, 카카오/삼성/LG 같은 대기업 집단명 제외)를 쓴다.
    두 경우 다 후보가 여러 개면 애매한 거라 손대지 않고 사용자가 직접 고르게 둔다.

    주의: 여기선 inbox_items.status를 절대 바꾸지 않는다(recurring_id/insurance_id만
    추가로 달아둠). status를 건드리면 "받은 알림" 혜택배정 큐에서 사라지고, 전월실적
    자동집계(status가 assigned/no_benefit인 것만 합산)에서도 빠지는 문제가
    생기기 때문 - 카드로 결제한 고정지출/보험료도 전월실적엔 그대로 잡혀야 한다."""
    item = conn.execute(
        "SELECT * FROM inbox_items WHERE id = ? AND recurring_id IS NULL AND insurance_id IS NULL "
        "AND automatch_excluded = 0",
        (inbox_id,),
    ).fetchone()
    if not item or not item["amount"]:
        return False

    def is_cash_only(rp):
        # 결제수단이 "현금"이고 연결된 카드도 없는 항목(예: "고양시청 납부")은
        # 애초에 카드/계좌 알림·엑셀로는 절대 들어올 수 없는 결제다. last4가
        # 없다는 이유로 last4_ok가 "제한 없음"으로 통과시켜버리면, 우연히 금액이나
        # 이름이 비슷한 전혀 다른 결제가 자동으로 붙어버리는 문제가 있었다
        # (실제로 "현금"인 고정지출에 네이버페이/지방세 알림이 잘못 매칭된 사례).
        # 그래서 이런 항목은 자동매칭 후보에서 아예 뺀다 - 해당되면 사람이
        # 직접 "+ 기록"으로 넣어야 한다.
        try:
            return not rp["card_id"] and (rp["payment_method"] or "").strip() == "현금"
        except (IndexError, KeyError):
            return False

    active_rows = [
        rp for rp in conn.execute(f"SELECT * FROM {table} WHERE active = 1").fetchall()
        if not is_cash_only(rp)
    ]

    def rp_last4(rp):
        # card_id로 등록된 카드를 결제수단으로 고른 항목은 payment_method
        # 텍스트가 비어있으므로(카드탭에서 실시간으로 끌어오는 방식이라),
        # 거기서 뒷자리를 뽑던 예전 방식으로는 카드 구분이 전혀 안 됐다
        # (last4_ok가 항상 "제한 없음"으로 통과돼서, 다른 카드로 결제된
        # 알림까지 섞여 들어오는 문제가 있었다). card_id가 있으면 카드탭에서
        # 직접 뒷자리를 찾는다.
        if rp["card_id"]:
            card = conn.execute("SELECT last4 FROM cards WHERE id = ?", (rp["card_id"],)).fetchone()
            return (card["last4"] if card else "") or ""
        return _extract_method_last4(rp["payment_method"])

    def last4_ok(rp):
        method_last4 = rp_last4(rp)
        return not (method_last4 and item["last4"] and not card_codes_match(method_last4, item["last4"]))

    def is_variable_amount(rp):
        # 도시가스/전기세처럼 매달 청구 금액이 바뀌는 고정지출(카테고리
        # "주거/관리비", 또는 전기세를 따로 모아둔 "마귀" 분류)은 등록된
        # amount가 그냥 마지막으로 확인한 예시값일 뿐이라, 그 숫자와 우연히
        # 같은 아무 결제(예: "우주정거장" 3,000원이 "HL 삼천리 도시가스"의
        # 등록 금액 3,000원과 우연히 같아서 잘못 묶인 실제 사고)가 금액만
        # 보고 잘못 묶이면 안 된다. 그래서 이런 항목은 금액 매칭 후보에서
        # 아예 빼고 이름/별칭 매칭만 쓴다. (insurances 테이블에는 category
        # 컬럼이 없으므로 보험료는 이 예외 대상이 아니다.)
        try:
            return rp["category"] in ("주거/관리비", "마귀")
        except (IndexError, KeyError):
            return False

    def merchant_excluded(rp):
        # "내역"에서 특정 건을 "해제"하면 그 가맹점명을 이 항목의 제외
        # 목록에 적어둔다(unlink_match 참고) - 그래야 다음 달에 똑같은
        # 가맹점에서 또 결제가 들어와도(새 알림, 새 id라서 automatch_excluded
        # 플래그만으론 못 막음) 다시 자동으로 안 붙는다.
        if not rp["excluded_merchants"] or not item["merchant"]:
            return False
        excluded = [m for m in rp["excluded_merchants"].split("\n") if m]
        return _merchant_base(item["merchant"]) in excluded

    amount_matches = [
        rp for rp in active_rows
        if not is_variable_amount(rp) and rp[amount_column] == item["amount"]
        and last4_ok(rp) and not merchant_excluded(rp)
    ]
    if len(amount_matches) > 1 and item["merchant"]:
        # 금액(+카드)까지 같은 게 두 개 이상이면(예: 서로 다른 구독 두 개가
        # 우연히 똑같이 9,900원인 경우 - "골프존"과 "카카오 골프"처럼), 가맹점명에
        # 이름/별칭 단어가 들어있는 쪽으로 좁혀본다. 여기서는 길이 3자 미만인
        # 짧은 단어는 후보에서 뺀다("카카오 골프"의 "골프"가 "골프존" 가맹점명
        # 에도 우연히 들어있어서, 2자 기준이면 오히려 둘 다 걸려서 안 좁혀짐).
        # 그렇게 해서 정확히 하나로 좁혀지면 그걸 쓰고, 안 좁혀지면(둘 다 걸리거나
        # 하나도 안 걸리면) 그대로 애매한 걸로 둔다.
        # "LG  U+통신요금  자동"처럼 OCR/알림 문구에 공백이 여러 개 끼는
        # 경우가 있어서, 토큰 비교에서 공백 차이로 안 걸리는 걸 막으려고
        # 괄호뿐 아니라 공백도 전부 지운다(토큰 자체는 원래 공백 기준으로
        # 쪼개서 만들어지므로 토큰 안엔 공백이 없음 - 안전).
        merchant_lower = re.sub(r"[()\s]", "", item["merchant"].lower())
        narrowed = [
            rp for rp in amount_matches
            if any(
                len(tok) >= 3 and tok.lower() in merchant_lower
                for tok in _name_tokens(rp[name_column]) + _name_tokens(rp["billing_alias"])
            )
        ]
        if len(narrowed) == 1:
            amount_matches = narrowed
    if len(amount_matches) == 1:
        label = _match_time_card_label(conn, item, table, amount_matches[0]["id"])
        conn.execute(
            f"UPDATE inbox_items SET {fk_column} = ?, matched_card_label = ? WHERE id = ?",
            (amount_matches[0]["id"], label, inbox_id),
        )
        return True
    if amount_matches:
        return False

    if not item["merchant"]:
        return False
    merchant_lower = re.sub(r"[()\s]", "", item["merchant"].lower())
    name_matches = [
        rp for rp in active_rows
        if last4_ok(rp) and not merchant_excluded(rp) and any(
            tok.lower() in merchant_lower
            for tok in _safe_name_tokens(rp[name_column]) + _safe_name_tokens(rp["billing_alias"])
        )
    ]
    if len(name_matches) > 1:
        # 이름 토큰(3자 이상)까지 같은 게 두 개 이상이면(예: "한국전력
        # 101호"/"102호"가 둘 다 "한국전력"에서 걸림, last4도 없는 계좌
        # 자동이체 알림이라 카드로도 못 가름) billing_alias를 "토큰"이
        # 아니라 "통째로 한 덩어리"로 봐서 한 번 더 좁힌다 - 예: 101호에
        # "저압"을 alias로 넣어두면("저압"은 2자라 토큰 기준에서 걸러지는
        # 글자라 위 1차 매칭엔 안 쓰였음) "한국전력 저압 자동이체" 알림만
        # 정확히 집어낼 수 있다.
        narrowed = [
            rp for rp in name_matches
            if rp["billing_alias"] and re.sub(r"\s", "", rp["billing_alias"].lower()) in merchant_lower
        ]
        if len(narrowed) == 1:
            name_matches = narrowed
    if len(name_matches) != 1:
        return False

    label = _match_time_card_label(conn, item, table, name_matches[0]["id"])
    conn.execute(
        f"UPDATE inbox_items SET {fk_column} = ?, matched_card_label = ? WHERE id = ?",
        (name_matches[0]["id"], label, inbox_id),
    )
    return True


def apply_startup_data_fixes():
    """매번 update.py를 따로 보내서 실행하던 "데이터 보정"(사용자 실제
    데이터를 한 번 바로잡는 것 - 예: 틀린 금액 고치기, 구분용 별칭
    등록하기)도, 이제 코드처럼 서버 시작할 때마다 자동으로 실행되게
    한다(launcher.py의 self_update()가 이 app.py 자체를 깃허브에서 받아
    오므로, 여기에 새 보정을 추가하고 푸시만 하면 다음에 껐다 켤 때
    자동으로 적용됨 - update.py를 따로 안 받아도 됨). 각 보정은 전부
    "지금 값이 다를 때만 고친다" 식으로 짜여 있어서(이미 맞으면 아무
    일도 안 함) 매번 실행돼도 안전하다. 실패해도(예: 이미 지워진 행)
    서버 시작 자체가 막히면 안 되므로 전체를 try로 감싼다."""
    try:
        conn = get_conn()
    except Exception:
        return
    try:
        placeholder_memo = "금액/결제일/결제카드 직접 입력해주세요"
        conn.execute(
            "UPDATE recurring_payments SET memo = '' WHERE memo = ? "
            "AND name IN ('넷플릭스', '네이버멤버십', '쿠팡와우')",
            (placeholder_memo,),
        )

        hanjeon_101 = conn.execute(
            "SELECT id, billing_alias FROM recurring_payments WHERE name = 'HL 한국전력 101호'"
        ).fetchone()
        if hanjeon_101 and hanjeon_101["billing_alias"] != "저압":
            conn.execute(
                "UPDATE recurring_payments SET billing_alias = '저압' WHERE id = ?",
                (hanjeon_101["id"],),
            )

        # 실제 데이터 확인 결과, 이름/가맹점이 전혀 무관한데 금액만 우연히
        # 같아서(또는 예전 느슨한 로직으로) 잘못 묶인 매칭이 여러 군데서
        # 발견됨 - 발견될 때마다 여기 추가해서 해제한다. 매칭만 풀고
        # (알림 자체/상태는 안 건드림) 가맹점명을 제외목록에 넣어서 다시
        # 자동으로 안 붙게 한다(기존 "해제" 버튼과 동일한 방식).
        def _unlink_bad_matches(recurring_name, merchant_like_patterns):
            row = conn.execute(
                "SELECT id, excluded_merchants FROM recurring_payments WHERE name = ?", (recurring_name,)
            ).fetchone()
            if not row:
                return
            where_merchant = " OR ".join(["merchant LIKE ?"] * len(merchant_like_patterns))
            bad_items = conn.execute(
                f"SELECT id, merchant FROM inbox_items WHERE recurring_id = ? AND ({where_merchant})",
                (row["id"], *[f"%{p}%" for p in merchant_like_patterns]),
            ).fetchall()
            if not bad_items:
                return
            existing = [m for m in (row["excluded_merchants"] or "").split("\n") if m]
            for bad_item in bad_items:
                conn.execute("UPDATE inbox_items SET recurring_id = NULL WHERE id = ?", (bad_item["id"],))
                merchant_key = _merchant_base(bad_item["merchant"])
                if merchant_key not in existing:
                    existing.append(merchant_key)
            conn.execute(
                "UPDATE recurring_payments SET excluded_merchants = ? WHERE id = ?",
                ("\n".join(existing), row["id"]),
            )

        # 'HL 삼천리 도시가스 달달'에 잘못 묶였던 배달의민족(우아한형제들)
        # 12,000원 결제 - 지금 코드로 재현 테스트해봤지만 안 묶여서
        # (버그 아님) 예전에 잘못 묶인 채 안 풀린 과거 데이터로 보임.
        _unlink_bad_matches("HL 삼천리 도시가스 달달", ["배달의민족", "우아한형제들"])

        # 'LGU+ 통신요금 인터넷'에 잘못 묶였던 "인터넷상거래-(주" 5건 -
        # 그냥 온라인 쇼핑몰 결제 표기일 뿐 LGU+ 인터넷 요금이 아님.
        _unlink_bad_matches("LGU+ 통신요금 인터넷", ["인터넷상거래"])

        # 'LGU+ 통신요금 휴대폰'에 잘못 묶였던 "(주)포스코와이드" 75,000원 -
        # LG U+랑 무관한 회사, 금액만 우연히 겹쳐서 묶인 걸로 보임.
        _unlink_bad_matches("LGU+ 통신요금 휴대폰", ["포스코와이드"])

        # 'LGU+ 통신요금 인터넷' 결제카드를 이번 달부터 KB에서 씨티로
        # 바꿨는데, 카드가 등록 안 돼 있던(last4가 안 찍힌) 지난 내역들이
        # 전부 "지금 등록된 카드"로 보이는 바람에 지난달 것까지 전부
        # 씨티로 보이던 문제(사용자 확인) - 위에서 추가한 matched_card_label
        # 스냅샷 기능으로 앞으로는 안 생기지만, 이미 매칭된 과거 내역에는
        # 스냅샷이 없어서 소급 채워준다. 이번 달 것만 빼고(이번 달은 실제로
        # 씨티로 낸 게 맞음) 전부 KB로 채운다.
        inet = conn.execute(
            "SELECT id FROM recurring_payments WHERE name LIKE 'LGU+%인터넷%'"
        ).fetchone()
        if inet:
            this_ym = current_year_month()
            conn.execute(
                "UPDATE inbox_items SET matched_card_label = 'KB' "
                "WHERE recurring_id = ? AND matched_card_label = '' AND substr(occurred_at, 1, 7) != ?",
                (inet["id"], this_ym),
            )

        # 하나증권 "종합위탁" 계좌 보유종목 4개 신규 등록. 종목코드는
        # WebSearch로 실제 확인(추측 안 함). 매수가는 캡처에 안 나와서
        # 평가금액/등락률로 역산(= 평가금액 / (1+등락률/100)) - 다른
        # 보유종목들도 같은 방식으로 등록해둔 전례 있음(정확한 매수가는
        # 아니고 추정치).
        new_stocks = [
            ("해성디에스", "195870", 47825, 1),
            ("명인제약", "317450", 57989, 1),
            ("KB제29호스팩", "478390", 2001, 5),
            ("팬오션", "028670", 5478, 1),
        ]
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) AS m FROM stocks").fetchone()["m"]
        stocks_added = 0
        for name, code, buy_price, qty in new_stocks:
            exists = conn.execute("SELECT 1 FROM stocks WHERE name = ?", (name,)).fetchone()
            if exists:
                continue
            max_order += 1
            conn.execute(
                "INSERT INTO stocks (name, code, buy_price, quantity, broker, active, memo, sort_order) "
                "VALUES (?, ?, ?, ?, '하나증권', 1, '매수가는 평가금액/등락률로 역산한 추정치', ?)",
                (name, code, buy_price, qty, max_order),
            )
            stocks_added += 1
        if stocks_added:
            print(f"하나증권 종합위탁 계좌 보유종목 {stocks_added}개 신규 등록")

        # 미래에셋증권 보유종목 등록할 때 '노타'가 빠졌던 걸 사용자가
        # 지적해서 확인함(코드 486990, WebSearch로 실제 확인) - 매수가는
        # 역시 평가금액/등락률로 역산한 추정치.
        nota_exists = conn.execute("SELECT 1 FROM stocks WHERE name = '노타'").fetchone()
        if not nota_exists:
            max_order2 = conn.execute("SELECT COALESCE(MAX(sort_order), -1) AS m FROM stocks").fetchone()["m"]
            conn.execute(
                "INSERT INTO stocks (name, code, buy_price, quantity, broker, active, memo, sort_order) "
                "VALUES ('노타', '486990', 9103, 1, '미래에셋증권', 1, "
                "'등록 누락됐던 거 소급 추가, 매수가는 평가금액/등락률로 역산한 추정치', ?)",
                (max_order2 + 1,),
            )
            print("미래에셋증권 '노타' 등록 누락분 추가")

        # 2026-11: "디딤씨앗 적립예금"(보험 탭)이랑 "고양시청 납부"(고정지출
        # 탭)가 실제로는 같은 결제(디딤씨앗은 고양시청을 통해 납부)를
        # 가리키는 중복 등록이었다. 사용자 요청으로 고정지출 쪽 하나로
        # 합친다: 보험 쪽의 실제 결제수단/적립액을 고정지출로 옮기고
        # (고정지출은 원래 결제수단이 "현금"이라 자동매칭 대상에서
        # 빠져있었음), 이 보험에 연결돼있던 받은알림들도 고정지출로
        # 다시 연결한 뒤, 보험 항목은 지운다. 이미 합쳐진 뒤에는 보험
        # 항목이 없어서 다시 실행해도 안전함(멱등).
        didimssi = conn.execute(
            "SELECT * FROM insurances WHERE product_name = '디딤씨앗 적립예금'"
        ).fetchone()
        goyang = conn.execute(
            "SELECT id FROM recurring_payments WHERE name = '고양시청 납부'"
        ).fetchone()
        if didimssi and goyang:
            conn.execute(
                "UPDATE recurring_payments SET payment_method = ?, card_id = ?, "
                "current_balance = current_balance + ? WHERE id = ?",
                (didimssi["payment_method"], didimssi["card_id"], didimssi["current_balance"], goyang["id"]),
            )
            conn.execute(
                "UPDATE inbox_items SET recurring_id = ?, insurance_id = NULL WHERE insurance_id = ?",
                (goyang["id"], didimssi["id"]),
            )
            conn.execute("DELETE FROM insurances WHERE id = ?", (didimssi["id"],))
            print("'디딤씨앗 적립예금'(보험)을 '고양시청 납부'(고정지출)로 통합")

        # 등록할 때 설명용으로 자동으로 적어넣었던 메모들이 그대로 계속
        # 보여서 거슬린다는 지적 - 실제 메모 내용(사용자가 따로 적은 게
        # 있으면 그건 보존)과 섞여 있어도 이 문구들만 제거한다. 발견되는
        # 대로 이 목록에 추가하면 된다.
        loan_placeholder_memos = [
            "갚은 금액이 화면에 안 보여서 잔액을 원금과 같게 등록함 - 실제와 다르면 수정 필요",
            "마이너스통장형이라 '원금'은 대출한도, '현재 잔액'은 지금 당겨쓴 금액. "
            "이자는 매달 금액이 달라서(매월 실제로 내는 금액은 최근 이자 기준 추정치).",
        ]
        for placeholder_text in loan_placeholder_memos:
            loans_with_placeholder = conn.execute(
                "SELECT id, memo FROM loans WHERE memo LIKE ?", (f"%{placeholder_text}%",)
            ).fetchall()
            for loan_row in loans_with_placeholder:
                cleaned_memo = loan_row["memo"].replace(placeholder_text, "").strip(" \n-")
                conn.execute("UPDATE loans SET memo = ? WHERE id = ?", (cleaned_memo, loan_row["id"]))

        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def _bump_didimssi_on_match(conn, inbox_id):
    """"고양시청 납부"(디딤씨앗 적립예금, 매달 내가 5만원 입금) 알림이 새로
    매칭될 때마다 정부도 5만원 매칭해준다고 보고 현재 적립액에 10만원(내
    5만원+정부 5만원)씩 자동으로 더한다. 기준점(current_balance)은 "내
    적립액"을 2배(정부매칭 반영) 해서 잡아둔 값이라, 매칭될 때마다 같은
    방식으로 10만원씩 늘려야 일관된다. 원래는 보험 탭에 "디딤씨앗 적립예금"
    이라는 별도 항목(결제수단 계좌이체)으로 등록돼 있었는데, "고양시청
    납부" 고정지출이랑 같은 실제 결제를 가리키는 중복 등록이라 사용자
    요청으로 고정지출 쪽 하나로 합치고 보험 쪽은 지웠다(그래서 "고양시청
    납부"의 결제수단도 그때 "현금"에서 실제 결제수단인 계좌이체로 같이
    바뀜 - 안 바꾸면 현금 항목은 is_cash_only에 걸려 애초에 자동매칭
    대상에서 빠짐). 이자/운용 수익까지 정확히 추적하는 건 포기하고
    (사용자가 "정확하지 않아도 된다"고 함) 매달 자동으로 커지게만 해둔다.
    _try_auto_match_recurring이 방금 실제로 새로 매칭했을 때만 호출되므로
    (이미 매칭된 건 다시 안 걸림) 한 건당 한 번만 더해진다."""
    item = conn.execute("SELECT recurring_id FROM inbox_items WHERE id = ?", (inbox_id,)).fetchone()
    if not item or not item["recurring_id"]:
        return
    rp = conn.execute(
        "SELECT name FROM recurring_payments WHERE id = ?", (item["recurring_id"],)
    ).fetchone()
    if not rp or rp["name"] != "고양시청 납부":
        return
    conn.execute(
        "UPDATE recurring_payments SET current_balance = current_balance + 100000 WHERE id = ?",
        (item["recurring_id"],),
    )


def _try_auto_match_recurring(conn, inbox_id):
    matched = _try_auto_match(conn, inbox_id, "recurring_id", "recurring_payments", "amount", "name")
    if matched:
        _bump_didimssi_on_match(conn, inbox_id)
    return matched


def _try_auto_match_insurance(conn, inbox_id):
    return _try_auto_match(conn, inbox_id, "insurance_id", "insurances", "monthly_premium", "product_name")
    return matched


def _nearest_payment_day_gap(payment_day, occurred_date):
    """occurred_date가 속한 달(과 전/다음달)의 payment_day 후보들 중
    occurred_date와 가장 가까운 날짜까지 며칠 차이나는지 구한다. 상환일이
    주말/공휴일이면 은행이 앞뒤로 날짜를 당기거나 미루는 걸 감안하기 위함."""
    day = min(max(int(payment_day or 1), 1), 31)
    best = None
    for delta in (-1, 0, 1):
        y, m = occurred_date.year, occurred_date.month + delta
        while m < 1:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        last_day = calendar.monthrange(y, m)[1]
        candidate = date(y, m, min(day, last_day))
        gap = abs((candidate - occurred_date).days)
        if best is None or gap < best:
            best = gap
    return best


def _mark_loan_paid_from_inbox(conn, loan_id, inbox_id, amount=None):
    """대출 상환 알림이 자동매칭되면, 그 달 "당월 상환 금액" 체크만 켜고
    끝내던 걸 수정 - 실제 알림으로 확인된 결제니까 "내역"(loan_payments)에도
    남기고 잔액도 반영해야 한다(안 그러면 수동으로 "+기록" 누른 것만
    내역/잔액에 반영되고, 자동으로 확인된 건 체크박스만 켜진 채 내역엔
    아무것도 안 남아서 사용자 입장에서 "확인은 됐는데 기록이 없다"가 됨).
    이자만 내는 "만기일시상환"은 원금이 안 줄어서 잔액 그대로 기록한다.
    원리금균등/원금균등 대출은 "등록된 월상환액 기준 예상 상환표"가 아니라
    이번에 실제로 들어온 알림 금액(amount)에서 이자(잔액×금리로 계산,
    결제수단과 무관하게 항상 같음)를 뺀 나머지를 원금 상환분으로 본다 -
    잔액이 부족해서 일부만 빠지고 나중에 나머지가 재입금되는 경우처럼
    실제 금액이 등록된 월상환액과 다를 수 있어서, 등록값 기준 예상표를
    그대로 믿고 잔액을 줄이면 실제보다 더 줄이거나 덜 줄이는 사고가 날 수
    있다(실제로 한 번 그렇게 틀렸음 - 등록값 기준으로 계산했다가 분할
    입금된 금액을 전체 월상환액인 것처럼 잘못 처리한 적 있음). 알림 금액이
    이자도 못 채우면(그만큼도 못 미치는 분할 입금 등) 원금은 안 줄이고
    금액만 기록한다."""
    conn.execute(
        "UPDATE loans SET paid_check_month = ? WHERE id = ?",
        (current_year_month(), loan_id),
    )
    conn.execute("UPDATE inbox_items SET status = 'no_benefit', card_id = NULL WHERE id = ?", (inbox_id,))
    loan = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
    inbox_item = conn.execute("SELECT occurred_at FROM inbox_items WHERE id = ?", (inbox_id,)).fetchone()
    paid_amount = amount if amount is not None else (loan["monthly_payment"] or 0)
    paid_at = (inbox_item["occurred_at"] or "")[:10] if inbox_item and inbox_item["occurred_at"] else date.today().isoformat()
    rtype = loan["repayment_type"] or ""
    balance = loan["current_balance"] or 0
    rate = loan["interest_rate"] or 0
    if rtype in ("원리금균등분할상환", "거치식원금균등분할", "원금균등상환") and balance > 0 and rate > 0:
        interest = round(balance * rate / 100 / 12)
        principal = max(0, min(balance, paid_amount - interest))
        balance_after = balance - principal
    else:
        balance_after = balance
    conn.execute(
        "INSERT INTO loan_payments (loan_id, paid_at, amount, balance_after, memo, source_inbox_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (loan_id, paid_at, paid_amount, balance_after, "알림 자동확인", inbox_id),
    )
    if balance_after != balance:
        conn.execute("UPDATE loans SET current_balance = ? WHERE id = ?", (balance_after, loan_id))


def _account_digits(s):
    """"하나은행 6681"처럼 은행명을 같이 적어서 사람이 알아보기 쉽게 적어둔
    출금계좌값에서 숫자만 뽑아낸다. 알림 원문엔 은행명 없이 계좌번호
    뒷자리만 찍히는 경우가 많아서, 은행명이 섞인 문자열 그대로는 알림
    본문과 절대 안 맞는다 - 숫자만 따로 비교해야 매칭이 된다."""
    digits = re.sub(r"\D", "", s or "")
    return digits


def _is_loan_reminder_text(text):
    """"내일 2026.11.20은 사잇돌대출(1980) 원금과 이자 45,729원을 갚는
    날이에요"/"계좌 잔액이 45,650원 부족해요. 계좌를 채워주세요"처럼,
    은행 앱이 실제 출금 전에 미리 보내는 안내/경고 알림은 결제가 아직
    일어나지도 않은 상태를 알려주는 거라, 대출 상환으로 자동 기록하면
    안 된다(실제로 "토스뱅크 사잇돌대출"에서 이 알림 두 건이 진짜 상환인
    것처럼 두 번 잘못 기록돼서 잔액이 틀어진 사고가 있었음). 이런 문구가
    있으면 금액/계좌가 맞아도 매칭 자체를 하지 않는다."""
    return any(
        k in text
        for k in ("부족해요", "부족합니다", "채워주세요", "채워주시", "갚는 날", "내는 날", "출금 예정", "결제 예정")
    )


def _try_auto_match_loan_account_text(conn, inbox_id):
    """"내 광주은행 통장 → 원리금441779"처럼 카드 결제가 아니라 계좌에서
    바로 빠져나가는 대출 원리금 자동이체 알림은, 혜택을 받을 수 있는
    거래가 아니라서 혜택배정 큐에 남아있을 이유가 없다. "통장"이 들어간
    알림(계좌 거래 알림의 특징적인 표현 - 카드 결제 알림과 구분하기 위함)
    에서만 시도하고, 등록된 대출의 출금계좌가 알림 문구에 들어있는 경우가
    정확히 하나뿐이면 그 대출로 매칭한다(금액이 아예 안 찍히는 계좌 알림도
    처리 가능). 출금계좌가 같아서(같은 통장에서 여러 대출이 같이 빠지는
    경우, 예: 케이뱅크 대출 2개가 같은 듀얼K통장에서 빠짐) 후보가 둘 이상
    나오면, 알림 문구의 "이자상환"/"원리금상환"/"원금상환" 표현으로 대출 종류
    (만기일시=이자만 나감 / 원리금균등 등=원금+이자 같이 나감)를 구분해서
    한 번 더 좁힌다. 계좌/문구로 특정되는 가장 확실한 매칭이라 제일 먼저
    시도한다."""
    item = conn.execute(
        "SELECT * FROM inbox_items WHERE id = ? AND status = 'pending'", (inbox_id,)
    ).fetchone()
    if not item:
        return False
    text = f"{item['merchant'] or ''} {item['raw_text'] or ''}"
    # "통장"/"계좌"라고 안 써 있어도 "출금 28,801원 대출이자결산 잔액
    # -3,000,000원"처럼 "출금"/"잔액"만 쓰는 은행 알림 형식도 있다. "대출"은
    # 여기 안 넣는다 - "장기카드대출(카드론)"/"단기카드대출(현금서비스)"
    # 처럼 카드 결제 알림에도 나오는 단어라 넣으면 카드 거래가 대출로
    # 잘못 매칭 시도될 수 있다.
    if not any(k in text for k in ("통장", "계좌", "출금", "잔액")):
        return False
    if _is_loan_reminder_text(text):
        return False
    loans = conn.execute(
        "SELECT * FROM loans WHERE withdrawal_account != ''"
    ).fetchall()
    # 우리은행처럼 "1002-254-781***계좌"같이 계좌번호를 하이픈으로 끊어서
    # 찍는 은행도 있다. 등록해둔 출금계좌 숫자가 하이픈 때문에 원문에서
    # 떨어져 보여서 그냥 in text로는 안 걸리니, 원문 쪽도 숫자만 남겨서
    # 비교한다(마스킹 *은 어차피 숫자가 아니라 제거돼도 비교에 지장 없음).
    text_digits = _account_digits(text)
    # 출금계좌에 숫자가 하나도 없으면(계좌번호 없이 은행명만 등록한 경우,
    # 예: "KB국민은행") 아래 l["withdrawal_account"] in text가 그 은행명을
    # 언급하는 모든 알림과 매칭돼버려 금액/날짜 무관 오매칭이 난다. 숫자가
    # 있는 경우에만 매칭을 시도한다.
    # 계좌번호를 전체로 등록해도, 은행 알림 자체가 어디를 마스킹하는지가
    # 은행마다 다르다(케이뱅크는 뒷 4자리만 보여주고, 하나은행/IBK는 뒷
    # 5자리 이상을 보여줌). 고정된 한 길이로만 비교하면 둘 다를 동시에
    # 만족시킬 수 없어서(4자리로 하면 케이뱅크는 맞지만 하나은행 알림의
    # "(구)" 옛 계좌 뒷자리랑 다른 대출 계좌 뒷 4자리가 우연히 겹쳐
    # 혼선이 남; 5자리로 하면 그 충돌은 없어지지만 케이뱅크는 아예 못
    # 찾음 - 둘 다 실제로 재현해서 확인함), 대출마다 "그 알림에서 실제로
    # 맞아떨어지는 가장 긴 뒷자리 수"를 구해서, 더 길게(더 구체적으로)
    # 맞은 대출을 우선한다 - 같은 알림에서 어떤 대출은 뒷 5자리까지
    # 맞고 다른 대출은 우연히 뒷 4자리만 맞았다면, 5자리까지 맞은 쪽만
    # 진짜로 보고 4자리 우연의 일치는 무시한다.
    def _account_match_score(registered, digits):
        if registered in text or digits in text_digits:
            return 100
        for n in range(9, 3, -1):
            if len(digits) > n and digits[-n:] in text_digits:
                return n
        return None

    scored = []
    for l in loans:
        digits = _account_digits(l["withdrawal_account"])
        if not digits:
            continue
        score = _account_match_score(l["withdrawal_account"], digits)
        if score is not None:
            scored.append((l, score))
    matches = []
    if scored:
        best = max(s for _, s in scored)
        matches = [l for l, s in scored if s == best]
    if len(matches) > 1:
        if "이자상환" in text:
            narrowed = [l for l in matches if (l["repayment_type"] or "") == "만기일시상환"]
            if len(narrowed) == 1:
                matches = narrowed
        elif "원리금상환" in text or "원금상환" in text:
            narrowed = [l for l in matches if (l["repayment_type"] or "") != "만기일시상환"]
            if len(narrowed) == 1:
                matches = narrowed
    if len(matches) != 1:
        return False
    _mark_loan_paid_from_inbox(conn, matches[0]["id"], inbox_id, item["amount"])
    return True


def _loan_amount_matches(loan, amount):
    """월상환액이 고정인 원리금균등/원금균등 대출은 등록값과 1원까지
    맞아야 진짜 그 결제가 맞는다고 본다. 반대로 만기일시상환(이자만
    내는 대출)은 매달 실제 이자가 일수·잔액 변동에 따라 조금씩 달라져서,
    등록해둔 월상환액은 처음 등록할 때의 추정치일 뿐이다 - 1원까지
    맞아야 한다면 사실상 영원히 자동매칭이 안 되는 걸 실제로 확인함
    (메리츠화재 보험계약대출: 등록값 7,000원인데 실제 알림은 7,326원).
    "거치식" 대출(거치식원금균등분할/거치식_원리금균등분할상환 등,
    표기가 통일돼있지 않아 접두사로만 확인)도 같은 이유로 매달 금액이
    달라질 수 있다고 사용자가 직접 확인해줌(신한카드(할부금융) 일반사업자
    사업자금대출: 등록값 430,000원인데 실제 알림은 428,170원) - 거치
    기간 종료 여부와 무관하게 항상 변동 가능하다고 봐서 구분 안 하고
    같이 넓힌다. 등록값의 20%까지는 같은 결제로 본다(그 이상 벌어지면
    다른 대출과 헷갈릴 위험이 커서 더는 안 봐줌)."""
    if not loan["monthly_payment"]:
        return False
    diff = abs(loan["monthly_payment"] - amount)
    rtype = loan["repayment_type"] or ""
    if rtype == "만기일시상환" or rtype.startswith("거치식"):
        return diff <= max(1, loan["monthly_payment"] * 0.2)
    return diff < 1


def _try_auto_match_loan_amount_date(conn, inbox_id):
    """출금계좌 텍스트로 특정이 안 될 때 쓰는, 좀 더 느슨한 매칭이다.
    금액이 그 대출 월 상환액과 (거의) 같고(_loan_amount_matches), 알림
    발생일이 그 대출 상환일 근처(상환일이 주말이면 은행이 앞뒤로 날짜를
    당기거나 미룰 수 있다고 보고 최대 3일까지)인 대출이 정확히 하나뿐이면
    매칭. 이건 보험료 등 다른 계좌 자동이체랑 금액이 우연히 겹칠 위험이
    있어서, 호출하는 쪽에서 반드시 고정지출/보험 매칭을 먼저 시도해
    실패한 경우에만(더 구체적인 가맹점명 매칭이 없을 때만) 마지막
    수단으로 불러야 한다.

    금액 허용오차를 20%로 넓힌 뒤(만기일시상환 대출 실제 이자가 매달
    변동하는 문제 때문), "메리츠화재 보험계약대출"(월상환액 7,000원)에서
    실제로 재현된 사고: 날짜+금액만 보니 그 좁은 구간(5,600~8,400원) 안에
    우연히 들어오는 전혀 무관한 거래(주유소 카드결제, 계좌간 이체, 저금통
    자동이체)까지 "대출 상환"으로 잘못 기록됨 - 둘 다 "통장"/"잔액" 같은
    계좌 알림 특유의 단어는 쓰지만 실제 대출 이자/상환과는 무관했다.
    대출 상환 알림은 실제로는 거의 항상 "이자"/"원금"/"원리금"/"상환" 중
    하나를 포함하므로(메리츠화재이자/흥국융자이자/대출이자납입/
    대출이자결산/이자상환/원금·이자 자동이체 - 진단 텍스트로 전부 직접
    확인함), 이 단어가 하나도 없으면 매칭하지 않는다.

    다만 "신한할부금융 428,170원"처럼 그 네 단어가 전혀 없어도(실제로
    재현됨) 매칭을 허용해야 하는 경우가 있다 - 이때 "대출 이름의 기관명
    (앞 2글자)만 알림에 있으면 허용"하는 식으로 한 번 짰다가 지적받고
    바로 되돌림: 이 사용자가 실제로 "신한카드" 신용카드도 따로 쓰고
    있어서(실제 카드결제 알림이 "신한카드(6982)승인..." 식으로 옴), 같은
    기관명 "신한"이 이 대출이랑도 겹치고 그 신한카드의 "카드대금 결제
    출금" 알림(이것도 "[출금] ...원 신한카드... 잔액..." 식으로 올 수
    있음)이랑도 겹쳐서, 기관명 2글자 하나만으로는 전혀 무관한 신한카드
    대금결제를 이 대출 상환으로 잘못 매칭시킬 위험이 그대로 있었다.
    그래서 기관명 2글자만으론 안 되고, "할부금융"/"캐피탈"/"여신전문"/
    "대부" 같은 - 카드결제·카드대금결제완 전혀 안 겹치는, 제2금융권
    대출기관 특유의 업종명이 "같이" 있을 때만("신한" + "할부금융"처럼
    둘 다 있어야) 허용한다. 일반 카드결제는 "할부"만 쓰지 이 업종명
    자체는 절대 안 쓴다."""
    item = conn.execute(
        "SELECT * FROM inbox_items WHERE id = ? AND status = 'pending'", (inbox_id,)
    ).fetchone()
    if not item or not item["amount"]:
        return False
    text = f"{item['merchant'] or ''} {item['raw_text'] or ''}"
    if not any(k in text for k in ("통장", "계좌", "출금", "잔액")):
        return False
    if _is_loan_reminder_text(text):
        return False
    try:
        occurred_date = date.fromisoformat((item["occurred_at"] or "")[:10])
    except ValueError:
        return False
    loans = conn.execute("SELECT * FROM loans").fetchall()
    matches = [
        l for l in loans
        if _loan_amount_matches(l, item["amount"])
        and _nearest_payment_day_gap(l["payment_day"], occurred_date) <= 3
    ]
    if len(matches) != 1:
        return False
    loan = matches[0]
    has_generic_keyword = any(k in text for k in ("이자", "원금", "원리금", "상환"))
    institution_tokens = [
        w for w in re.findall(r"[가-힣A-Za-z0-9]{4,}", loan["name"] or "")
        if w not in _LOAN_NAME_GENERIC_WORDS
    ]
    has_institution_hint = (
        bool(institution_tokens)
        and institution_tokens[0][:2] in text
        and any(k in text for k in ("할부금융", "캐피탈", "여신전문", "대부"))
    )
    if not (has_generic_keyword or has_institution_hint):
        return False
    _mark_loan_paid_from_inbox(conn, loan["id"], inbox_id, item["amount"])
    return True


def _try_auto_match_all(conn, inbox_id):
    """대출/고정지출/보험 자동매칭 전체를 정해진 우선순위로 한 번에
    시도한다: 대출 은행명 텍스트(가장 확실) → 고정지출 → 보험(가맹점명까지
    보는 구체적 매칭) → 대출 금액+날짜(가장 느슨함, 보험료 등과 금액이
    우연히 겹칠 수 있어서 마지막 수단). 실시간 알림 처리/엑셀 업로드/
    대출·고정지출·보험 탭의 "자동 매칭" 버튼이 전부 이 함수 하나만 쓴다 -
    예전엔 호출하는 곳마다 순서가 다르거나(엑셀 업로드는 대출 매칭 자체가
    빠져있었음) 자기 탭만 보고 재시도해서, 같은 알림이 어느 경로로
    처리되느냐에 따라 다르게(엉뚱하게) 배정될 수 있었다. 입구를 하나로
    합쳐서 그런 사고를 구조적으로 막는다."""
    return (
        _try_auto_match_loan_account_text(conn, inbox_id)
        or _try_auto_match_recurring(conn, inbox_id)
        or _try_auto_match_insurance(conn, inbox_id)
        or _try_auto_match_loan_amount_date(conn, inbox_id)
    )


def _auto_match_all_pending(conn):
    """큐에 남아있는(아직 뭔가에 매칭 안 된) 알림 전체에 _try_auto_match_all을
    한 번씩 다시 돌린다. 대출/고정지출/보험 탭의 "자동 매칭" 버튼이 전부
    이 함수를 호출한다 - 어느 버튼을 눌러도 결과가 같다. 이미 매칭된
    항목은 각 하위 매칭 함수가 자체적으로 걸러내므로(상태/FK 확인), 범위를
    넓게 잡아도 안전하다."""
    ids = [
        r["id"]
        for r in conn.execute("SELECT id FROM inbox_items WHERE status != 'cancelled' AND deleted_at IS NULL")
    ]
    matched = 0
    for iid in ids:
        if _try_auto_match_all(conn, iid):
            matched += 1
    conn.commit()
    return matched


def _next_payment_date(payment_day):
    """payment_day(1~31)를 기준으로 오늘 이후 가장 가까운 상환일을 구한다.
    31일처럼 그 달에 없는 날이면(예: 4월 31일) calendar.monthrange로 그 달의
    실제 말일로 자른다 - 무조건 28일로 자르면 29~31일로 등록한 대출은 실제
    상환일보다 최대 3일 이르게 계산된다."""
    day = min(max(int(payment_day or 1), 1), 31)
    today = date.today()
    last_day = calendar.monthrange(today.year, today.month)[1]
    candidate = date(today.year, today.month, min(day, last_day))
    if candidate <= today:
        y, m = today.year, today.month + 1
        if m > 12:
            y += 1
            m = 1
        last_day = calendar.monthrange(y, m)[1]
        candidate = date(y, m, min(day, last_day))
    return candidate


def _add_month(d, day):
    y, m = d.year, d.month + 1
    if m > 12:
        y += 1
        m = 1
    last_day = calendar.monthrange(y, m)[1]
    return date(y, m, min(max(int(day or 1), 1), last_day))


def _installments_paid_before(start_date_str, payment_day, before_date):
    """대출 시작일(start_date) 다음 상환일부터 payment_day 주기로 세어
    before_date(배타) 전까지 몇 번 냈는지 구한다. 첫 상환일은 보통 실행일
    한 달 뒤로 본다. 이걸로 "실제 몇 회차"인지 계산(앱 안 쓰기 시작한
    날짜부터가 아니라 대출 자체가 시작된 날부터 세야 맞음)."""
    try:
        start = date.fromisoformat(start_date_str)
    except (ValueError, TypeError):
        return None
    day = min(max(int(payment_day or 1), 1), 31)
    cur = _add_month(start, day)
    count = 0
    while cur < before_date:
        count += 1
        cur = _add_month(cur, day)
        if count > 600:
            return None
    return count


def _amortization_equal_principal(loan):
    """거치식원금균등분할(또는 거치기간 끝나고 원금균등으로 전환되는 대출):
    repayment_start_date(실제 원금상환 시작일, 거치기간 종료 안내문 등으로
    확인된 날짜)부터 end_date까지 남은 회차로 현재 잔액을 똑같이 나눠서
    원금을 갚고, 이자는 매달 그때그때 남은 잔액 기준으로 계산한다(그래서
    총 납입액은 점점 줄어듦). repayment_start_date를 모르면(등록 안 돼
    있으면) 언제부터 원금을 갚기 시작하는지 추측할 수 없어서 아예 계산
    안 한다."""
    balance = loan["current_balance"] or 0
    rate = loan["interest_rate"] or 0
    if balance <= 0 or rate <= 0 or not loan["repayment_start_date"]:
        return None
    try:
        repay_start = date.fromisoformat(loan["repayment_start_date"])
        end = date.fromisoformat(loan["end_date"])
    except (ValueError, TypeError):
        return None
    day = loan["payment_day"]
    months = 0
    cur = repay_start
    while cur <= end:
        months += 1
        cur = _add_month(cur, day)
        if months > 600:
            return None
    if months <= 0:
        return None

    next_date = _next_payment_date(day)
    if next_date < repay_start:
        return None  # 아직 거치기간 중(원금상환 전) - 보여줄 표가 없음
    base = 0
    cur = repay_start
    while cur < next_date:
        base += 1
        cur = _add_month(cur, day)

    monthly_rate = rate / 100 / 12
    principal_per = round(balance / months)
    rows = []
    for seq in range(base + 1, months + 1):
        interest = round(balance * monthly_rate)
        principal = principal_per if seq < months else balance
        if principal > balance:
            principal = balance
        balance -= principal
        rows.append({
            "seq": seq, "date": next_date.isoformat(),
            "interest": interest, "principal": principal,
            "balance": balance, "payment": principal + interest,
        })
        next_date = _add_month(next_date, day)
    return rows


def _amortization_equal_installment(loan):
    """원리금균등분할상환 대출만, 현재 등록된 잔액/금리/월상환액이 그대로
    유지된다고 가정하고 완납까지 회차별 이자/원금/잔액을 추정한다.
    - 금리나 월상환액이 없거나, 월상환액이 이자도 못 갚는 경우(거치기간 등
      실제로는 다른 방식으로 상환 중일 가능성)는 추정이 틀릴 수 있어 아예
      보여주지 않는다(없는 데이터로 추측 금지).
    """
    if (loan["repayment_type"] or "") != "원리금균등분할상환":
        return None
    balance = loan["current_balance"] or 0
    rate = loan["interest_rate"] or 0
    installment = loan["monthly_payment"] or 0
    if balance <= 0 or rate <= 0 or installment <= 0:
        return None
    monthly_rate = rate / 100 / 12
    next_date = _next_payment_date(loan["payment_day"])
    already_paid = _installments_paid_before(loan["start_date"], loan["payment_day"], next_date)
    base = already_paid if already_paid is not None else 0
    rows = []
    for seq in range(1, 481):
        real_seq = base + seq
        interest = round(balance * monthly_rate)
        if installment <= interest:
            return None
        principal = installment - interest
        if principal >= balance:
            principal = balance
            balance = 0
            rows.append({
                "seq": real_seq, "date": next_date.isoformat(),
                "interest": interest, "principal": principal,
                "balance": balance, "payment": principal + interest,
            })
            break
        balance -= principal
        rows.append({
            "seq": real_seq, "date": next_date.isoformat(),
            "interest": interest, "principal": principal,
            "balance": balance, "payment": installment,
        })
        next_date = _add_month(next_date, loan["payment_day"])
    else:
        return None
    return rows


def _amortization_table(loan):
    rtype = loan["repayment_type"] or ""
    if rtype == "원리금균등분할상환":
        return _amortization_equal_installment(loan)
    if rtype in ("거치식원금균등분할", "원금균등상환"):
        return _amortization_equal_principal(loan)
    return None


def serialize_loans(conn):
    rows = conn.execute("SELECT * FROM loans ORDER BY sort_order ASC, id ASC").fetchall()
    result = []
    for loan in rows:
        payments = conn.execute(
            "SELECT * FROM loan_payments WHERE loan_id = ? ORDER BY paid_at DESC, id DESC",
            (loan["id"],),
        ).fetchall()

        principal = loan["principal"] or 0
        balance = loan["current_balance"] or 0
        percent_paid = None
        if principal > 0:
            percent_paid = max(0, min(100, round((principal - balance) / principal * 100)))

        days_left = None
        if loan["end_date"]:
            try:
                days_left = (date.fromisoformat(loan["end_date"]) - date.today()).days
            except ValueError:
                days_left = None

        # 매달이 아니라 특정 달에만 내는 대출(예: 분기납 1,4,7,10월)은
        # payment_months에 그 달들이 들어있다. 비어있으면 매달 내는 걸로 본다.
        payment_months = [m.strip() for m in (loan["payment_months"] or "").split(",") if m.strip()]
        due_this_month = (not payment_months) or (str(date.today().month) in payment_months)
        current_ym = date.today().strftime("%Y-%m")
        paid_this_month = loan["paid_check_month"] == current_ym

        amortization = _amortization_table(loan)
        # 거치기간 끝나고 원금균등으로 전환되거나 하면 실제 납입액이 매달
        # 바뀌어서(원리금균등은 고정) 등록된 monthly_payment 하나로는 못
        # 따라감. 예상 상환표가 있으면 그 다음 회차 납입액을 "당월 상환
        # 금액"으로 쓴다 - 매번 수동으로 고쳐줄 필요 없이 항상 최신으로
        # 맞게. 상환표가 없는 대출(만기일시 등)은 등록된 값 그대로 둠.
        due_amount = amortization[0]["payment"] if amortization else loan["monthly_payment"]

        attachments = conn.execute(
            "SELECT * FROM loan_attachments WHERE loan_id = ? ORDER BY id DESC",
            (loan["id"],),
        ).fetchall()

        result.append({
            "id": loan["id"],
            "name": loan["name"],
            "loan_type": loan["loan_type"],
            "principal": principal,
            "current_balance": balance,
            "interest_rate": loan["interest_rate"],
            "repayment_type": loan["repayment_type"],
            "start_date": loan["start_date"],
            "end_date": loan["end_date"],
            "payment_day": loan["payment_day"],
            "monthly_payment": loan["monthly_payment"],
            "due_amount": due_amount,
            "payment_months": loan["payment_months"],
            "due_this_month": due_this_month,
            "withdrawal_account": loan["withdrawal_account"],
            "paid_this_month": paid_this_month,
            "memo": loan["memo"],
            "percent_paid": percent_paid,
            "days_left": days_left,
            "sort_order": loan["sort_order"],
            "payments": [dict(p) for p in payments],
            "amortization": amortization,
            "attachments": [dict(a) for a in attachments],
        })
    return result


def serialize_unassigned_attachments(conn):
    """한꺼번에 올렸는데 어느 대출인지 자동으로 못 가려낸 파일들 -
    대출 탭 위쪽에 따로 보여주고 수동으로 배정하게 한다."""
    rows = conn.execute(
        "SELECT * FROM loan_attachments WHERE loan_id IS NULL ORDER BY id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


_LOAN_ATTACHMENT_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp"}


def _ocr_pil_image(pil_img):
    from PIL import Image
    import numpy as np
    target_width = 1600
    if pil_img.width < target_width:
        scale = target_width / pil_img.width
        resample = getattr(Image, "Resampling", Image).LANCZOS
        pil_img = pil_img.resize((target_width, round(pil_img.height * scale)), resample)
    reader_ocr = _get_ocr_reader()
    detections = reader_ocr.readtext(np.array(pil_img), paragraph=False)
    return "\n".join(d[1] for d in detections)


def _reraise_if_fatal(e):
    """진짜 중단 신호(Ctrl+C, 인터프리터 종료)만 그대로 올리고 나머지는
    여기서 처리한다. pypdf가 내부에서 쓰는 cryptography 패키지의 러스트
    바인딩이 깨져 있으면(예: _cffi_backend 미설치) import만 해도 보통의
    Exception이 아니라 BaseException만 상속한 pyo3_runtime.PanicException이
    터진다 - 기존 "except Exception"으로는 하나도 못 잡혀서 "매칭 진단"
    버튼까지 포함해 요청 전체가 그대로 500으로 죽는 걸 실제로 재현해서
    확인함. 패키지 하나가 깨졌다고 전체 매칭/진단 기능이 죽으면 안 되니
    이런 panic도 같이 잡아서 그 파일/패키지만 건너뛰게 한다."""
    if isinstance(e, (KeyboardInterrupt, SystemExit, GeneratorExit)):
        raise e


def _extract_text_from_loan_attachment(filename, file_bytes):
    """금융거래확인서 등 첨부파일에서 어느 대출인지 가려낼 글자를 뽑는다.
    PDF는 먼저 pypdf로 시도하고(실제 글자 정보가 있는 PDF), 글자가 하나도
    안 나오면(스캔해서 이미지로만 들어있는 PDF, 또는 특정 페이지에서
    pypdf가 실패하는 PDF) 페이지를 그림으로 변환해서 OCR로 다시
    읽는다. 사진은 처음부터 OCR(기존 영수증 인식에 쓰는 easyocr
    리더를 그대로 재사용).

    실제로 글자 정보가 있는 정상 PDF인데도 pypdf 버전에 따라 특정
    페이지에서 예외가 나는 경우가 있었다(기존엔 pypdf 시도와 OCR
    fallback이 같은 try 블록 안에 있어서, 한 페이지 실패로 전체가
    바로 빈 문자열이 되고 OCR까지 가보지도 못했음) - pypdf는 페이지
    단위로, 전체 pypdf 시도와 OCR fallback도 서로 독립적으로
    실패하게 나눠서, 한쪽이 깨져도 다른 쪽은 계속 시도되게 한다."""
    from PIL import Image
    ext = os.path.splitext(filename.lower())[1]
    if ext == ".pdf":
        text = ""
        try:
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(file_bytes))
            parts = []
            for page in reader.pages:
                try:
                    parts.append(page.extract_text() or "")
                except BaseException as e:
                    _reraise_if_fatal(e)
                    continue
            text = "\n".join(parts)
        except BaseException as e:
            _reraise_if_fatal(e)
            text = ""
        if text.strip():
            return text
        try:
            import fitz
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            parts = []
            for page in doc:
                try:
                    pix = page.get_pixmap(dpi=200)
                    pil_img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    parts.append(_ocr_pil_image(pil_img))
                except BaseException as e:
                    _reraise_if_fatal(e)
                    continue
            return "\n".join(parts)
        except BaseException as e:
            _reraise_if_fatal(e)
            return ""
    if ext in _LOAN_ATTACHMENT_IMAGE_EXTS:
        try:
            pil_img = Image.open(BytesIO(file_bytes)).convert("RGB")
            return _ocr_pil_image(pil_img)
        except BaseException as e:
            _reraise_if_fatal(e)
            return ""
    return ""


def _get_or_extract_attachment_text(conn, attachment_id, cached_text, filename, file_bytes):
    """이미 뽑아둔 글자가 있으면 그걸 그대로 쓰고, 없을 때만 실제로
    추출한다(OCR은 파일 하나당 몇 초~몇십 초씩 걸릴 수 있어서, 매칭
    진단/재검증/재시도가 호출될 때마다 등록된 모든 첨부파일을 매번
    다시 OCR 돌리면 응답이 느려져서 브라우저 쪽 fetch가 타임아웃나는
    원인이 된다 - 실제로 "매칭 진단"이 "Failed to fetch"로 재현된 것도
    이 때문으로 보임). 추출 결과가 비어있으면(실패) 캐시에 저장하지
    않고 매번 다시 시도한다 - 그래야 나중에 pypdf/OCR 설치 문제를
    고치면 다음 호출에서 바로 다시 시도됨."""
    if cached_text:
        return cached_text
    text = _extract_text_from_loan_attachment(filename, file_bytes)
    if text:
        conn.execute(
            "UPDATE loan_attachments SET extracted_text = ? WHERE id = ?", (text, attachment_id)
        )
        conn.commit()
    return text


def _loan_match_tokens(withdrawal_account):
    """대출의 '출금계좌' 표기(예: "카카오뱅크 1234")에서 은행명 등
    글자 토큰과 계좌 뒤 4자리를 따로 뽑는다."""
    text = withdrawal_account or ""
    digits = re.sub(r"\D", "", text)
    last4 = digits[-4:] if len(digits) >= 4 else ""
    words = re.findall(r"[가-힣A-Za-z]{2,}", text)
    return words, last4


_DATE_LIKE_IN_MEMO_RE = re.compile(r"\d{4}[.\-]\s*\d{1,2}[.\-]\s*\d{1,2}")


def _account_last4_candidates_in_text(text):
    """글자 안에서 계좌번호처럼 보이는 숫자 토큰(중간에 "-" 있어도 됨)의
    뒤 4자리를 전부 뽑는다. 메모에 전체 계좌번호를 적어둔 경우("계좌
    310-107-277677")와, 뒤 4자리만 따로 적어둔 경우("7677") 둘 다
    된다 - 대출 계좌번호를 "출금계좌" 칸이 아니라 메모에 적어둔 경우를
    위함. 메모 속 "2026.11.17" 같은 날짜나 "2302026S51139"(보증번호)
    처럼 끝 4자리가 우연히 연도("2026")와 같은 긴 숫자를 그대로 계좌
    후보로 뽑으면, 그해에 쓴 아무 서류나 다 걸려버린다(실제로 이
    버그로 엉뚱한 서류 여러 개가 특정 대출에 잘못 붙었음) - 날짜
    모양은 먼저 지우고, 4자리보다 긴 숫자에서 뽑은 끝 4자리가
    연도처럼 보이면(1990~2099) 후보에서 뺀다. 메모에 뒤 4자리만 딱
    적어둔 경우("7677")는 길이가 정확히 4자리라 이 제외 대상이 아니라
    그대로 쓴다."""
    text = _DATE_LIKE_IN_MEMO_RE.sub(" ", text or "")
    candidates = set()
    for run in re.findall(r"[\d\-]{4,}", text):
        digits = re.sub(r"\D", "", run)
        if len(digits) < 4:
            continue
        last4 = digits[-4:]
        if len(digits) > 4 and 1990 <= int(last4) <= 2099:
            continue
        candidates.add(last4)
    return candidates


# "대출"/"신용" 같은 말은 거의 모든 대출명에 들어있어서 매칭 신호로
# 못 쓴다(이 단어만 겹쳐도 매칭됐다고 보면 전부 다 걸려버림).
_LOAN_NAME_GENERIC_WORDS = {
    "대출", "신용", "개인", "담보", "신용대출", "담보대출", "은행",
    # "개인사업자"/"사업자"는 은행/상품과 무관하게 금융서류 어디서나(사업자
    # 구분란 등) 흔히 나오는 분류용 말이라, 특정 대출명으로 못 쓴다 -
    # 실제로 KB국민카드 명세서가 이 단어 때문에 전혀 무관한 카카오뱅크
    # 대출과 매칭되는 사고가 있었음.
    "개인사업자", "사업자",
}


def _loan_name_tokens(name):
    # 3글자 단어는 우연히 다른 서류 글자와 겹치는 사고가 있었다(예:
    # 케이뱅크 서류가 엉뚱한 IBK 대출에 붙음) - 4글자 이상만 매칭
    # 신호로 쓴다. "비상금대출"/"사잇돌대출"처럼 은행마다 똑같이 쓰는
    # 상품명(업계 공통 상품명)은 다른 은행 서류와도 겹쳐버린다(실제로
    # 카카오뱅크 서류가 "하나은행 비상금대출"에 잘못 붙었음) - 대출명은
    # 보통 "은행명 상품명" 순서라, 제일 앞 단어(보통 은행/기관명)만
    # 매칭 신호로 쓰고 뒤에 오는 상품명 단어는 안 쓴다.
    words = re.findall(r"[가-힣A-Za-z0-9]{4,}", name or "")
    words = [w for w in words if w not in _LOAN_NAME_GENERIC_WORDS]
    return words[:1]


def _normalize_ocr_bank_suffix(text):
    """OCR이 "뱅크"의 "뱅"을 생김새가 비슷한 "방"/"빵"으로 잘못 읽는 걸
    실제 카카오뱅크 스캔 서류에서 확인함("카카오방크", "카카오빵크"로
    인식됨 - 정작 은행명 매칭에 쓰는 글자가 단 한 번도 정확히 안
    나와서 전혀 매칭이 안 됐었음). 카카오뱅크/케이뱅크/토스뱅크처럼
    등록된 대출명이 전부 "~뱅크"로 끝나는 경우가 많아, 이 OCR 오인식은
    카카오뱅크에만 국한된 문제가 아니라 "뱅크"가 들어간 모든 대출명에
    같은 식으로 나타날 수 있다 - 매칭용으로만 "방크"/"빵크"를 전부
    "뱅크"로 바꿔서 비교한다(원본 글자는 미리보기 등에 그대로 남겨서
    디버깅엔 지장 없음)."""
    return text.replace("방크", "뱅크").replace("빵크", "뱅크")


def _match_loans_for_attachment_text(loans, text):
    """첨부파일 글자 안에서 등록된 대출을 전부 찾는다 - (1)출금계좌 뒤
    4자리(+가능하면 은행명 등 글자까지)가 들어있거나, (2)대출 계좌번호를
    "출금계좌" 칸이 아니라 메모에 적어둔 경우를 위해 메모 안의 계좌번호
    뒤 4자리도 같이 보거나, (3)대출명의 구체적인 단어(은행명/상품명 등,
    "대출"/"신용" 같은 흔한 말 제외)가 그대로 들어있으면 그 대출로 본다.
    "금융거래확인서"는 한 은행의 여신 전체를 한 장에 같이 보여주는
    경우가 많아서(대출이 여러 개 같이 찍힘), 걸리는 대출이 여러 개면
    전부 돌려준다 - 그 파일이 실제로 그 대출들 전부에 대한 증빙이기
    때문에 하나만 추측해서 고르는 게 아니라 전부 다 첨부한다. 하나도
    안 걸리면 빈 리스트(미분류로 남김)."""
    text = _normalize_ocr_bank_suffix(text)
    compact_digits = re.sub(r"\D", "", text)
    candidates = []
    for loan in loans:
        words, last4 = _loan_match_tokens(loan["withdrawal_account"])
        account_match = bool(last4) and last4 in compact_digits and (not words or any(w in text for w in words))
        memo_last4s = _account_last4_candidates_in_text(loan["memo"])
        memo_match = bool(memo_last4s) and any(c in compact_digits for c in memo_last4s)
        name_words = _loan_name_tokens(loan["name"])
        name_match = bool(name_words) and any(w in text for w in name_words)
        if account_match or memo_match or name_match:
            candidates.append(loan)
    return candidates


def _display_month_bounds():
    """쿼리스트링의 ?month=YYYY-MM을 달력 월 경계([시작일, 종료일))로 바꾼다.
    없거나 형식이 틀리면 None. 상단 월 선택 바에서 과거/미래 달을 고르면
    "한도/잔여/전월실적"까지 전부 그 달 기준으로 같이 바뀐다(진짜 "그 달엔
    어떤 상태였는지"를 그대로 보여주기 위함 - 예전엔 사용내역 목록만 과거
    달로 바뀌고 한도는 항상 "진짜 오늘" 기준이라 헷갈린다는 피드백으로
    바꿈)."""
    display_month = (request.args.get("month") or "").strip()
    if not re.match(r"^\d{4}-\d{2}$", display_month):
        return None
    year, month = map(int, display_month.split("-"))
    if not (1 <= month <= 12):
        return None
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


def _serialize_cards(conn):
    cards = conn.execute(
        "SELECT * FROM cards ORDER BY sort_order ASC, id ASC"
    ).fetchall()
    display_bounds = _display_month_bounds()
    # 월 선택 바에서 고른 달이 있으면 그 달 1일을 기준일로 써서 카드 사이클
    # (reset_day가 1이 아닌 카드도 있음)/전월실적까지 전부 그 달 기준으로
    # 계산한다. 없으면 None = 평소처럼 진짜 오늘 기준.
    ref_date = display_bounds[0] if display_bounds else None

    result = []
    for card in cards:
        start, end = cycle_bounds(card["reset_day"], ref_date)
        log_start, log_end = start, end

        this_month = current_year_month(ref_date)
        prev_month = previous_year_month(ref_date)
        perf_threshold = card["perf_threshold"] or 0

        # 실적은 손으로 입력할 필요 없이, 그동안 받은 결제 알림(배정됐거나
        # "혜택 없음" 처리된 것 - 즉 실제로 확인된 결제) 금액을 합산해서 자동
        # 계산한다. 카드사 실적은 "지난달(전월)" 완결된 금액을 기준으로 이번 달
        # 혜택 구간이 정해지므로, 지난달 알림 합계를 그 기준으로 쓴다.
        # 안내장의 "전월 이용금액 제외 대상"(공과금·세금·상품권 등)은 혜택
        # 대상이어도 실적 합계에서는 뺀다.
        #
        # 고정지출(구독)/보험료로 매칭된 알림은 status가 'pending'인 채로
        # 남고 card_id도 안 채워진다(recurring_id/insurance_id만 채워짐) -
        # "카드로 결제한 구독/보험료도 실적엔 잡혀야 한다"는 원래 의도와
        # 다르게 이 함수가 card_id만 보고 있어서 그런 건들이 다 누락되고
        # 있었다. recurring_payments.card_id/insurances.card_id로 연결된
        # 것도 같이 잡아야 하는데, 그 card_id를 "지금 당장" 조회해서 쓰면
        # 안 된다 - 고정지출/보험의 결제카드를 나중에 바꾸면(예: "다음 달부터
        # A카드 대신 B카드로 낼게") 이미 지나간 달의 알림까지 전부 새 카드
        # 실적으로 둔갑해버리는 사고가 난다(실제로 발생 확인됨). 그래서
        # inbox_items.matched_card_label(매칭되던 "그 순간"에 찍어둔 카드
        # 이름 스냅샷 - 나중에 카드를 바꿔도 절대 안 바뀜)을 우선 쓰고,
        # 그 스냅샷이 없는 옛날 데이터만 지금 card_id를 보조로 쓴다.
        def _auto_spend(year_month):
            own_label = card["short_name"] or card["name"]
            rows = conn.execute(
                """SELECT inbox_items.amount, inbox_items.merchant, inbox_items.recurring_id,
                          inbox_items.insurance_id, inbox_items.matched_card_label,
                          recurring_payments.card_id AS recurring_card_id,
                          recurring_payments.payment_method AS recurring_payment_method,
                          insurances.card_id AS insurance_card_id,
                          insurances.payment_method AS insurance_payment_method
                   FROM inbox_items
                   LEFT JOIN recurring_payments ON recurring_payments.id = inbox_items.recurring_id
                   LEFT JOIN insurances ON insurances.id = inbox_items.insurance_id
                   WHERE substr(inbox_items.occurred_at, 1, 7) = ?
                   AND inbox_items.deleted_at IS NULL
                   AND (
                       (inbox_items.recurring_id IS NULL AND inbox_items.insurance_id IS NULL
                        AND inbox_items.card_id = ? AND inbox_items.status IN ('assigned', 'no_benefit'))
                       OR inbox_items.recurring_id IS NOT NULL
                       OR inbox_items.insurance_id IS NOT NULL
                   )""",
                (year_month, card["id"]),
            ).fetchall()

            def belongs_to_this_card(r):
                if not r["recurring_id"] and not r["insurance_id"]:
                    return True  # 직접배정/혜택없음 - 위 WHERE에서 이미 card_id로 걸러짐
                if r["matched_card_label"]:
                    return r["matched_card_label"] == own_label
                live_card_id = r["recurring_card_id"] if r["recurring_id"] else r["insurance_card_id"]
                if live_card_id:
                    return live_card_id == card["id"]
                # card_id FK도 없는 옛날 "텍스트로 직접 입력" 등록분은 뒷자리로 보조 매칭.
                method = r["recurring_payment_method"] if r["recurring_id"] else r["insurance_payment_method"]
                return bool(card["last4"]) and card_codes_match(_extract_method_last4(method), card["last4"])

            return sum(
                r["amount"] for r in rows
                if belongs_to_this_card(r) and not is_performance_excluded(r["merchant"], card["perf_excluded_keywords"])
            )

        auto_prev_spend = _auto_spend(prev_month)
        auto_this_month_spend = _auto_spend(this_month)

        manual_row = conn.execute(
            "SELECT total_spend FROM performance WHERE card_id = ? AND year_month = ?",
            (card["id"], prev_month),
        ).fetchone()
        perf_spend = manual_row["total_spend"] if manual_row else auto_prev_spend

        benefits = conn.execute(
            "SELECT * FROM benefits WHERE card_id = ? ORDER BY sort_order ASC, id ASC",
            (card["id"],),
        ).fetchall()

        benefit_list = []
        for b in benefits:
            used = conn.execute(
                """SELECT COALESCE(SUM(used_value), 0) AS total FROM usage_logs
                   WHERE benefit_id = ? AND used_at >= ? AND used_at < ?""",
                (b["id"], start.isoformat(), end.isoformat()),
            ).fetchone()["total"]

            logs = conn.execute(
                """SELECT usage_logs.*, inbox_items.occurred_at AS notif_occurred_at
                   FROM usage_logs
                   LEFT JOIN inbox_items ON inbox_items.id = usage_logs.source_inbox_id
                   WHERE usage_logs.benefit_id = ?
                   AND usage_logs.used_at >= ? AND usage_logs.used_at < ?
                   ORDER BY COALESCE(inbox_items.occurred_at, usage_logs.used_at) DESC, usage_logs.id DESC""",
                (b["id"], log_start.isoformat(), log_end.isoformat()),
            ).fetchall()
            # "사용"(used)은 한도에서 깎인 할인/적립액 합계고, payment_sum은 그
            # 할인을 받은 실제 결제금액 자체의 합계 - 서로 다른 숫자라 화면에
            # 둘 다 따로 보여준다. raw_value가 없는 옛날 기록(이 컬럼 생기기
            # 전)은 used_value로 대신한다.
            payment_sum = sum(
                (l["raw_value"] if l["raw_value"] is not None else l["used_value"]) for l in logs
            )

            tiered_limit = tier_limit_for_spend(b["tier_table"], perf_spend)
            # 구간표가 있는 혜택은 전월실적이 최저 구간에도 못 미치면 tiered_limit이
            # 0으로 나온다 - 이건 "한도 없음(무제한)"이 아니라 "이번 달 한도 0원
            # (혜택 대상 아님)"이라는 뜻이므로, 구간표가 아예 없는 혜택(limit_value가
            # 0=무제한이라는 뜻)과 구분해서 처리해야 한다. 안 그러면 실적 미달인데도
            # 화면에 "무제한 적립 가능"으로 잘못 보인다.
            if tiered_limit is not None:
                limit_value = tiered_limit
                unlimited = False
            else:
                limit_value = b["limit_value"] or 0
                unlimited = limit_value <= 0
            remaining = None if unlimited else limit_value - used
            if unlimited:
                percent = None
            elif limit_value > 0:
                percent = max(0, min(100, round(used / limit_value * 100)))
            else:
                percent = 100 if used > 0 else 0

            category_usage = None
            if b["calc_mode"] == "percent_discount" and b["rate_table"]:
                categories = parse_rate_table_categories(b["rate_table"])
                if categories:
                    today_str = date.today().isoformat()
                    this_month_str = today_str[:7]
                    counts = {}
                    today_counts = {}
                    month_earned = {}
                    for l in logs:
                        cat = l["rate_category"]
                        if cat:
                            counts[cat] = counts.get(cat, 0) + 1
                            if l["used_at"] == today_str:
                                today_counts[cat] = today_counts.get(cat, 0) + 1
                            if l["used_at"][:7] == this_month_str:
                                month_earned[cat] = month_earned.get(cat, 0) + (l["used_value"] or 0)
                    category_usage = [
                        {
                            "label": c["label"],
                            "count": counts.get(c["label"], 0),
                            "today_count": today_counts.get(c["label"], 0),
                            "daily_limit": c["daily_limit"],
                            "monthly_limit": c["monthly_limit"],
                            "monthly_point_cap": c["monthly_point_cap"],
                            "month_earned": month_earned.get(c["label"], 0),
                        }
                        for c in categories
                    ]

            # "기본적립"처럼 한도가 아니라 적립률(%) 자체가 전월실적 구간별로
            # 바뀌는 혜택은 percent_tier_table에 [[기준금액,%],...]로 등록해둔다.
            # 등록돼 있으면 "기본 할인율" 고정값 대신 이걸로 계산한 %를 쓴다
            # (tier_limit_for_spend는 한도/퍼센트 구분 없이 그냥 구간表 조회라
            # 그대로 재사용 가능 - 최저 구간 미달이면 0 돌려줌).
            effective_discount_percent = b["discount_percent"]
            if b["calc_mode"] == "percent_discount" and b["percent_tier_table"]:
                tiered_percent = tier_limit_for_spend(b["percent_tier_table"], perf_spend)
                if tiered_percent is not None:
                    effective_discount_percent = tiered_percent

            benefit_list.append({
                "id": b["id"],
                "card_id": b["card_id"],
                "name": b["name"],
                "limit_type": b["limit_type"],
                "limit_value": limit_value,
                "unlimited": unlimited,
                "tiered": tiered_limit is not None,
                "tier_table": b["tier_table"],
                "percent_tier_table": b["percent_tier_table"],
                "memo": b["memo"],
                "merchant_keywords": b["merchant_keywords"],
                "calc_mode": b["calc_mode"],
                "discount_percent": effective_discount_percent,
                "discount_percent_raw": b["discount_percent"],
                "per_txn_cap": b["per_txn_cap"],
                "rate_table": b["rate_table"],
                "start_hour": b["start_hour"],
                "end_hour": b["end_hour"],
                "category_usage": category_usage,
                "always_doubled": bool(b["always_doubled"]),
                "plan_day_double": bool(b["plan_day_double"]),
                "used": used,
                "payment_sum": payment_sum,
                "remaining": remaining,
                "percent": percent,
                "over_limit": (not unlimited) and remaining < 0,
                "logs": [
                    {**dict(l), "is_perf_excluded": is_performance_excluded(l["merchant"], card["perf_excluded_keywords"])}
                    for l in logs
                ],
            })

        result.append({
            "id": card["id"],
            "name": card["name"],
            "short_name": card["short_name"],
            "issuer": card["issuer"],
            "last4": card["last4"],
            "reset_day": card["reset_day"],
            "perf_threshold": perf_threshold,
            "perf_spend": perf_spend,
            "perf_month": prev_month,
            "perf_auto": manual_row is None,
            "perf_met": (auto_this_month_spend >= perf_threshold) if perf_threshold > 0 else None,
            "this_month_spend": auto_this_month_spend,
            "memo": card["memo"],
            "perf_excluded_keywords": card["perf_excluded_keywords"],
            "cycle_start": start.isoformat(),
            "cycle_end": (end - timedelta(days=1)).isoformat(),
            "benefits": benefit_list,
            "sort_order": card["sort_order"],
            "closed": bool(card["closed"]),
        })
    return result


@app.route("/")
def index():
    # 업데이트할 때마다 폰 브라우저가 예전에 캐시해둔 js/css를 계속
    # 쓰는 바람에 화면이 안 바뀐 것처럼 보이는 문제가 있어서, 파일이
    # 바뀔 때마다 값이 달라지는 버전 문자열을 붙여 강제로 새로 받게 한다.
    # 지출 로고 이미지(static/img/sub_logos/*)도 똑같은 문제(서버 파일은
    # 새로 받아졌는데 브라우저가 예전 흑백 이미지를 계속 캐시해서 보여줌)가
    # 있어서, 그 폴더 안에서 가장 최근에 바뀐 파일 시각도 같이 반영한다.
    js_path = os.path.join(app.static_folder, "js", "app.js")
    css_path = os.path.join(app.static_folder, "css", "style.css")
    mtimes = [os.path.getmtime(js_path), os.path.getmtime(css_path)]
    for name in ("logo.png", "wordmark.png", "icon-192.png", "icon-512.png", "apple-touch-icon.png"):
        path = os.path.join(app.static_folder, "img", name)
        if os.path.exists(path):
            mtimes.append(os.path.getmtime(path))
    logo_dir = os.path.join(app.static_folder, "img", "sub_logos")
    if os.path.isdir(logo_dir):
        for fname in os.listdir(logo_dir):
            try:
                mtimes.append(os.path.getmtime(os.path.join(logo_dir, fname)))
            except OSError:
                pass
    asset_version = str(int(max(mtimes)))
    resp = make_response(render_template("index.html", asset_version=asset_version))
    # 이 HTML 자체를 폰 브라우저가 캐시해버리면 새 버전 번호가 붙은
    # 링크조차 못 받아보게 되므로, 페이지 본문은 절대 캐시하지 않게 한다.
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.route("/api/state")
def api_state():
    return jsonify(serialize_full())


@app.route("/api/cards", methods=["POST"])
def create_card():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "카드 이름을 입력하세요."}), 400
    try:
        reset_day = int(data.get("reset_day") or 1)
    except (TypeError, ValueError):
        reset_day = 1
    try:
        perf_threshold = float(data.get("perf_threshold") or 0)
    except (TypeError, ValueError):
        perf_threshold = 0

    conn = get_conn()
    try:
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) AS m FROM cards").fetchone()["m"]
        conn.execute(
            """INSERT INTO cards (name, short_name, issuer, last4, reset_day, perf_threshold, memo, sort_order,
               perf_excluded_keywords, closed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                (data.get("short_name") or "").strip(),
                (data.get("issuer") or "").strip(),
                (data.get("last4") or "").strip(),
                reset_day,
                perf_threshold,
                (data.get("memo") or "").strip(),
                max_order + 1,
                (data.get("perf_excluded_keywords") or "").strip(),
                1 if data.get("closed") else 0,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/cards/<int:card_id>", methods=["PUT"])
def update_card(card_id):
    data = request.get_json(force=True) or {}
    conn = get_conn()
    try:
        card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not card:
            return jsonify({"error": "카드를 찾을 수 없습니다."}), 404

        name = (data.get("name") or card["name"]).strip()
        try:
            reset_day = int(data.get("reset_day", card["reset_day"]))
        except (TypeError, ValueError):
            reset_day = card["reset_day"]
        try:
            perf_threshold = float(data.get("perf_threshold", card["perf_threshold"]))
        except (TypeError, ValueError):
            perf_threshold = card["perf_threshold"]
        try:
            sort_order = int(data.get("sort_order", card["sort_order"]))
        except (TypeError, ValueError):
            sort_order = card["sort_order"]

        closed = 1 if data.get("closed", bool(card["closed"])) else 0

        conn.execute(
            """UPDATE cards SET name = ?, short_name = ?, issuer = ?, last4 = ?, reset_day = ?,
               perf_threshold = ?, memo = ?, sort_order = ?, perf_excluded_keywords = ?, closed = ? WHERE id = ?""",
            (
                name,
                (data.get("short_name", card["short_name"]) or "").strip(),
                (data.get("issuer", card["issuer"]) or "").strip(),
                (data.get("last4", card["last4"]) or "").strip(),
                reset_day,
                perf_threshold,
                (data.get("memo", card["memo"]) or "").strip(),
                sort_order,
                (data.get("perf_excluded_keywords", card["perf_excluded_keywords"]) or "").strip(),
                closed,
                card_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/cards/<int:card_id>/performance/<year_month>", methods=["DELETE"])
def delete_performance(card_id, year_month):
    """수기로 입력해둔 전월실적을 지워서 자동 계산(받은 알림 합산)으로
    되돌린다 - 한번 직접 입력하면 계속 그 값이 고정돼서 자동 계산으로
    돌아갈 방법이 없었음."""
    conn = get_conn()
    try:
        conn.execute(
            "DELETE FROM performance WHERE card_id = ? AND year_month = ?",
            (card_id, year_month),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/cards/<int:card_id>/performance", methods=["POST"])
def update_performance(card_id):
    """전월실적 계산용 "이번 달 총 사용액"을 직접 입력/수정한다.

    혜택별 사용기록과 달리, 전월실적은 보통 그 카드로 쓴 전체 금액을 보므로
    (스타벅스 할인 같은 특정 혜택에 해당 안 하는 결제도 포함) 카드사 앱/문자로
    확인한 "이번 달 실적" 숫자를 그대로 입력하는 방식으로 관리한다.
    """
    data = request.get_json(force=True) or {}
    try:
        total_spend = float(data.get("total_spend"))
    except (TypeError, ValueError):
        return jsonify({"error": "사용액을 입력하세요."}), 400
    if total_spend < 0:
        return jsonify({"error": "사용액은 0 이상이어야 합니다."}), 400

    year_month = (data.get("year_month") or "").strip() or previous_year_month()

    conn = get_conn()
    try:
        card = conn.execute("SELECT id FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not card:
            return jsonify({"error": "카드를 찾을 수 없습니다."}), 404
        conn.execute(
            """INSERT INTO performance (card_id, year_month, total_spend, updated_at)
               VALUES (?, ?, ?, datetime('now', 'localtime'))
               ON CONFLICT(card_id, year_month)
               DO UPDATE SET total_spend = excluded.total_spend, updated_at = excluded.updated_at""",
            (card_id, year_month, total_spend),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/cards/<int:card_id>", methods=["DELETE"])
def delete_card(card_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


# ---- 대출 ----


@app.route("/api/stocks", methods=["POST"])
def create_stock():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    code = (data.get("code") or "").strip()
    if not name:
        return jsonify({"error": "종목명을 입력하세요."}), 400
    try:
        buy_price = float(data.get("buy_price") or 0)
    except (TypeError, ValueError):
        buy_price = 0
    try:
        quantity = float(data.get("quantity") or 0)
    except (TypeError, ValueError):
        quantity = 0
    try:
        sell_price = float(data.get("sell_price") or 0)
    except (TypeError, ValueError):
        sell_price = 0

    conn = get_conn()
    try:
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) AS m FROM stocks").fetchone()["m"]
        conn.execute(
            """INSERT INTO stocks (name, code, broker, buy_price, quantity, sell_price, active, memo, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name, code, (data.get("broker") or "").strip(), buy_price, quantity, sell_price,
                1 if data.get("active", True) else 0,
                (data.get("memo") or "").strip(),
                max_order + 1,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/stocks/<int:stock_id>", methods=["PUT"])
def update_stock(stock_id):
    data = request.get_json(force=True) or {}
    conn = get_conn()
    try:
        stock = conn.execute("SELECT * FROM stocks WHERE id = ?", (stock_id,)).fetchone()
        if not stock:
            return jsonify({"error": "종목을 찾을 수 없습니다."}), 404
        name = (data.get("name") or stock["name"]).strip()
        try:
            buy_price = float(data.get("buy_price", stock["buy_price"]))
        except (TypeError, ValueError):
            buy_price = stock["buy_price"]
        try:
            quantity = float(data.get("quantity", stock["quantity"]))
        except (TypeError, ValueError):
            quantity = stock["quantity"]
        try:
            sort_order = int(data.get("sort_order", stock["sort_order"]))
        except (TypeError, ValueError):
            sort_order = stock["sort_order"]
        try:
            sell_price = float(data.get("sell_price", stock["sell_price"]))
        except (TypeError, ValueError):
            sell_price = stock["sell_price"]
        active = 1 if data.get("active", bool(stock["active"])) else 0

        # 매수가/수량을 사람이 직접 고치면 손익 부호가 달라질 수 있으니,
        # 다음 백그라운드 확인 때 "방금 플러스로 바뀜" 알림이 잘못 뜨지
        # 않도록 last_known_positive를 지금 값 기준으로 다시 계산해둔다.
        # 매도 처리(active=0)면 매도가 기준으로, 아니면 시세 기준으로.
        ref_price = sell_price if (not active and sell_price) else (stock["last_price"] or 0)
        profit = (ref_price - buy_price) * quantity if ref_price else 0
        last_known_positive = 1 if profit > 0 else 0

        conn.execute(
            """UPDATE stocks SET name=?, code=?, broker=?, buy_price=?, quantity=?, sell_price=?,
               active=?, memo=?, sort_order=?, last_known_positive=? WHERE id=?""",
            (
                name,
                (data.get("code", stock["code"]) or "").strip(),
                (data.get("broker", stock["broker"]) or "").strip(),
                buy_price,
                quantity,
                sell_price,
                active,
                (data.get("memo", stock["memo"]) or "").strip(),
                sort_order,
                last_known_positive,
                stock_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/stocks/<int:stock_id>", methods=["DELETE"])
def delete_stock(stock_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM stocks WHERE id = ?", (stock_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/stocks/refresh", methods=["POST"])
def refresh_stocks():
    """"지금 확인" 버튼 - 장 시간이 아니어도, 기다리지 않고 지금 바로
    전 종목 시세를 확인한다(플러스 전환이면 토스트 알림도 똑같이 뜸)."""
    _check_stocks_and_notify()
    return jsonify(serialize_full())


@app.route("/api/loans", methods=["POST"])
def create_loan():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "대출 이름을 입력하세요."}), 400
    try:
        principal = float(data.get("principal") or 0)
    except (TypeError, ValueError):
        principal = 0
    try:
        current_balance = float(
            data.get("current_balance") if data.get("current_balance") not in (None, "") else principal
        )
    except (TypeError, ValueError):
        current_balance = principal
    try:
        interest_rate = float(data.get("interest_rate") or 0)
    except (TypeError, ValueError):
        interest_rate = 0
    try:
        payment_day = int(data.get("payment_day") or 0)
    except (TypeError, ValueError):
        payment_day = 0
    try:
        monthly_payment = float(data.get("monthly_payment") or 0)
    except (TypeError, ValueError):
        monthly_payment = 0

    conn = get_conn()
    try:
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) AS m FROM loans").fetchone()["m"]
        conn.execute(
            """INSERT INTO loans (name, loan_type, principal, current_balance, interest_rate,
               repayment_type, start_date, end_date, payment_day, monthly_payment, payment_months,
               withdrawal_account, memo, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                (data.get("loan_type") or "").strip(),
                principal,
                current_balance,
                interest_rate,
                (data.get("repayment_type") or "").strip(),
                (data.get("start_date") or "").strip(),
                (data.get("end_date") or "").strip(),
                payment_day,
                monthly_payment,
                (data.get("payment_months") or "").strip(),
                (data.get("withdrawal_account") or "").strip(),
                (data.get("memo") or "").strip(),
                max_order + 1,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/loans/<int:loan_id>", methods=["PUT"])
def update_loan(loan_id):
    data = request.get_json(force=True) or {}
    conn = get_conn()
    try:
        loan = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
        if not loan:
            return jsonify({"error": "대출을 찾을 수 없습니다."}), 404

        name = (data.get("name") or loan["name"]).strip()
        try:
            principal = float(data.get("principal", loan["principal"]))
        except (TypeError, ValueError):
            principal = loan["principal"]
        try:
            current_balance = float(data.get("current_balance", loan["current_balance"]))
        except (TypeError, ValueError):
            current_balance = loan["current_balance"]
        try:
            interest_rate = float(data.get("interest_rate", loan["interest_rate"]))
        except (TypeError, ValueError):
            interest_rate = loan["interest_rate"]
        try:
            payment_day = int(data.get("payment_day", loan["payment_day"]))
        except (TypeError, ValueError):
            payment_day = loan["payment_day"]
        try:
            monthly_payment = float(data.get("monthly_payment", loan["monthly_payment"]))
        except (TypeError, ValueError):
            monthly_payment = loan["monthly_payment"]
        try:
            sort_order = int(data.get("sort_order", loan["sort_order"]))
        except (TypeError, ValueError):
            sort_order = loan["sort_order"]

        conn.execute(
            """UPDATE loans SET name=?, loan_type=?, principal=?, current_balance=?, interest_rate=?,
               repayment_type=?, start_date=?, end_date=?, payment_day=?, monthly_payment=?,
               payment_months=?, withdrawal_account=?, memo=?, sort_order=? WHERE id=?""",
            (
                name,
                (data.get("loan_type", loan["loan_type"]) or "").strip(),
                principal,
                current_balance,
                interest_rate,
                (data.get("repayment_type", loan["repayment_type"]) or "").strip(),
                (data.get("start_date", loan["start_date"]) or "").strip(),
                (data.get("end_date", loan["end_date"]) or "").strip(),
                payment_day,
                monthly_payment,
                (data.get("payment_months", loan["payment_months"]) or "").strip(),
                (data.get("withdrawal_account", loan["withdrawal_account"]) or "").strip(),
                (data.get("memo", loan["memo"]) or "").strip(),
                sort_order,
                loan_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/loans/<int:loan_id>/paid-check", methods=["PUT"])
def toggle_loan_paid_check(loan_id):
    """이번 달 상환했는지 체크/해제. 월이 바뀌면 자동으로 다시 미체크 상태가 된다."""
    data = request.get_json(force=True) or {}
    checked = bool(data.get("checked"))
    conn = get_conn()
    try:
        value = date.today().strftime("%Y-%m") if checked else ""
        conn.execute("UPDATE loans SET paid_check_month=? WHERE id=?", (value, loan_id))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/loans/<int:loan_id>", methods=["DELETE"])
def delete_loan(loan_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM loans WHERE id = ?", (loan_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/loans/<int:loan_id>/payments", methods=["POST"])
def log_loan_payment(loan_id):
    """상환 기록을 "이번에 얼마 갚았나"가 아니라 "상환 후 잔액이 얼마 남았나"로
    입력받는다 - 명세서에 찍힌 숫자를 그대로 넣으면 되고, 계산해서 뺄 필요가 없다."""
    data = request.get_json(force=True) or {}
    conn = get_conn()
    try:
        loan = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
        if not loan:
            return jsonify({"error": "대출을 찾을 수 없습니다."}), 404

        try:
            balance_after = float(data.get("balance_after"))
        except (TypeError, ValueError):
            return jsonify({"error": "상환 후 잔액을 입력하세요."}), 400
        if balance_after < 0:
            return jsonify({"error": "잔액은 0 이상이어야 합니다."}), 400

        paid_at = (data.get("paid_at") or "").strip()
        try:
            datetime.strptime(paid_at, "%Y-%m-%d")
        except ValueError:
            paid_at = date.today().isoformat()

        memo = (data.get("memo") or "").strip()
        amount = (loan["current_balance"] or 0) - balance_after

        conn.execute(
            "INSERT INTO loan_payments (loan_id, paid_at, amount, balance_after, memo) VALUES (?, ?, ?, ?, ?)",
            (loan_id, paid_at, amount, balance_after, memo),
        )
        conn.execute("UPDATE loans SET current_balance = ? WHERE id = ?", (balance_after, loan_id))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/loan-payments/<int:payment_id>", methods=["DELETE"])
def delete_loan_payment(payment_id):
    """상환 기록을 지우면, 그 대출의 현재 잔액을 "남아있는 기록 중 가장 최근 것의
    잔액"(기록이 하나도 없으면 원금)으로 다시 계산한다 - 중간 기록을 지워도
    최신 기록 기준으로 항상 맞게 유지하기 위해(삭제 순서와 무관하게 안전)."""
    conn = get_conn()
    try:
        payment = conn.execute(
            "SELECT loan_id, source_inbox_id FROM loan_payments WHERE id = ?", (payment_id,)
        ).fetchone()
        if payment:
            loan_id = payment["loan_id"]
            conn.execute("DELETE FROM loan_payments WHERE id = ?", (payment_id,))
            latest = conn.execute(
                "SELECT balance_after FROM loan_payments WHERE loan_id = ? ORDER BY paid_at DESC, id DESC LIMIT 1",
                (loan_id,),
            ).fetchone()
            if latest:
                new_balance = latest["balance_after"]
            else:
                principal_row = conn.execute("SELECT principal FROM loans WHERE id = ?", (loan_id,)).fetchone()
                new_balance = principal_row["principal"] if principal_row else 0
            # 이 상환 기록이 받은 알림 자동매칭으로 만들어진 거면(source_inbox_id
            # 있음), 그 알림도 같이 "받은 알림" 큐로 되돌린다 - 안 그러면 상환
            # 기록은 지워졌는데 원본 알림은 no_benefit 상태로 계속 숨어있고,
            # "당월 상환" 체크 표시도 틀린 채로 계속 켜져있는 반쪽만 풀린
            # 상태가 된다(recurring/insurance의 해제 버그랑 같은 유형).
            if payment["source_inbox_id"]:
                conn.execute(
                    "UPDATE inbox_items SET status = 'pending', card_id = NULL WHERE id = ?",
                    (payment["source_inbox_id"],),
                )
            still_paid_this_month = conn.execute(
                "SELECT 1 FROM loan_payments WHERE loan_id = ? AND substr(paid_at, 1, 7) = ?",
                (loan_id, current_year_month()),
            ).fetchone()
            if not still_paid_this_month:
                conn.execute("UPDATE loans SET paid_check_month = '' WHERE id = ?", (loan_id,))
            conn.execute("UPDATE loans SET current_balance = ? WHERE id = ?", (new_balance, loan_id))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/loan-attachments/<int:attachment_id>", methods=["DELETE"])
def delete_loan_attachment(attachment_id):
    """한 파일이 여러 대출(은행 여신 전체를 보여주는 서류 등)에 같이
    첨부돼 있을 수 있어서, 다른 대출이 아직 같은 파일을 쓰고 있으면
    실제 파일은 지우지 않고 이 대출과의 연결(행)만 지운다."""
    conn = get_conn()
    try:
        item = conn.execute(
            "SELECT file_name FROM loan_attachments WHERE id = ?", (attachment_id,)
        ).fetchone()
        conn.execute("DELETE FROM loan_attachments WHERE id = ?", (attachment_id,))
        if item and item["file_name"]:
            still_used = conn.execute(
                "SELECT 1 FROM loan_attachments WHERE file_name = ? LIMIT 1", (item["file_name"],)
            ).fetchone()
            if not still_used:
                path = os.path.join(UPLOAD_DIR, item["file_name"])
                if os.path.exists(path):
                    os.remove(path)
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


def _save_and_match_one_loan_attachment(conn, loans, uploaded):
    """파일 하나를 저장하고 매칭한다 - 은행 여신 전체를 한 장에 같이
    보여주는 서류(금융거래확인서 등)는 대출이 여러 개 걸릴 수 있는데,
    그 경우 추측해서 하나만 고르지 않고 걸리는 대출 전부에 같은 파일을
    첨부한다(실제로 그 대출들 전부의 증빙이 맞기 때문). 하나도 안
    걸리면 "미분류"로 남겨서 나중에 수동으로 배정하게 한다."""
    file_bytes = uploaded.read()
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    existing = conn.execute(
        "SELECT id, loan_id FROM loan_attachments WHERE file_hash = ?", (file_hash,)
    ).fetchall()
    text = _extract_text_from_loan_attachment(uploaded.filename, file_bytes)
    matched_loans = _match_loans_for_attachment_text(loans, text) if text else []
    already_loan_ids = {e["loan_id"] for e in existing if e["loan_id"] is not None}
    if existing:
        # 전에 못 가려서 미분류로 남아있던 파일을 같은 내용으로 다시
        # 올리면, 매칭 로직이 그 사이 고쳐졌을 수 있으니 다시 시도한다 -
        # 예전엔 그냥 "이미 있는 파일"로 보고 조용히 넘어가서, 로직을
        # 고쳐도 다시 안 올리는 한 영영 미분류로 남았었다. 이미 하나라도
        # 배정돼 있으면 그 파일(실제 디스크의 file_name)을 그대로 두고,
        # 새로 걸린 대출마다 행만 추가한다.
        unassigned_row = next((e for e in existing if e["loan_id"] is None), None)
        shared_file_name = conn.execute(
            "SELECT file_name FROM loan_attachments WHERE file_hash = ? LIMIT 1", (file_hash,)
        ).fetchone()["file_name"]
        new_loans = [l for l in matched_loans if l["id"] not in already_loan_ids]
        if unassigned_row and new_loans:
            conn.execute(
                "UPDATE loan_attachments SET loan_id = ?, extracted_text = COALESCE(extracted_text, ?) WHERE id = ?",
                (new_loans[0]["id"], text or None, unassigned_row["id"]),
            )
            new_loans = new_loans[1:]
        for loan in new_loans:
            conn.execute(
                "INSERT INTO loan_attachments (loan_id, file_name, original_name, file_hash, extracted_text) "
                "VALUES (?, ?, ?, ?, ?)",
                (loan["id"], shared_file_name, uploaded.filename, file_hash, text or None),
            )
        return
    ext = os.path.splitext(secure_filename(uploaded.filename))[1]
    file_name = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, file_name), "wb") as f:
        f.write(file_bytes)
    if matched_loans:
        for loan in matched_loans:
            conn.execute(
                "INSERT INTO loan_attachments (loan_id, file_name, original_name, file_hash, extracted_text) "
                "VALUES (?, ?, ?, ?, ?)",
                (loan["id"], file_name, uploaded.filename, file_hash, text or None),
            )
    else:
        conn.execute(
            "INSERT INTO loan_attachments (loan_id, file_name, original_name, file_hash, extracted_text) "
            "VALUES (?, ?, ?, ?, ?)",
            (None, file_name, uploaded.filename, file_hash, text or None),
        )


@app.route("/api/loans/attachments/auto", methods=["POST"])
def upload_loan_attachments_auto():
    """금융거래확인서 등을 한꺼번에 여러 장 올리면, 파일 안의 글자에서
    등록된 대출을 찾아 자동으로 넣는다."""
    files = request.files.getlist("file")
    if not files:
        return jsonify({"error": "파일을 선택하세요."}), 400
    failures = []
    conn = get_conn()
    try:
        loans = conn.execute("SELECT * FROM loans").fetchall()
        for uploaded in files:
            if not uploaded or not uploaded.filename:
                continue
            # 파일 하나 처리하다 예외가 나도(글자 인식 실패 등) 나머지
            # 파일이나 이미 처리된 파일까지 같이 날아가면 안 되니, 파일
            # 마다 따로 커밋한다. 예전엔 실패 사유를 서버 콘솔에만
            # print해서, 사용자 눈엔 그 파일이 아무 흔적도 없이 그냥
            # 사라진 것처럼 보였다(업로드 성공 메시지까지 떴음) - 응답에
            # 실패 목록을 실어서 화면에 그대로 보여준다.
            try:
                _save_and_match_one_loan_attachment(conn, loans, uploaded)
                conn.commit()
            except BaseException as e:
                _reraise_if_fatal(e)
                conn.rollback()
                print(f"[증빙 자동매칭] {uploaded.filename} 처리 실패: {e}")
                failures.append({"filename": uploaded.filename, "reason": str(e) or type(e).__name__})
    finally:
        conn.close()
    result = serialize_full()
    result["upload_failures"] = failures
    return jsonify(result)


@app.route("/api/loan-attachments/<int:attachment_id>/assign", methods=["PUT"])
def assign_loan_attachment(attachment_id):
    """자동 분류 못 한 첨부파일을 사용자가 직접 대출에 배정한다."""
    data = request.get_json(force=True) or {}
    try:
        loan_id = int(data.get("loan_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "대출을 선택하세요."}), 400
    conn = get_conn()
    try:
        loan = conn.execute("SELECT id FROM loans WHERE id = ?", (loan_id,)).fetchone()
        if not loan:
            return jsonify({"error": "대출을 찾을 수 없습니다."}), 404
        conn.execute(
            "UPDATE loan_attachments SET loan_id = ? WHERE id = ?", (loan_id, attachment_id)
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/loan-attachments/<int:attachment_id>/download")
def download_loan_attachment(attachment_id):
    conn = get_conn()
    try:
        item = conn.execute(
            "SELECT file_name, original_name FROM loan_attachments WHERE id = ?", (attachment_id,)
        ).fetchone()
        if not item or not item["file_name"]:
            return jsonify({"error": "파일이 없습니다."}), 404
        # as_attachment=True면 크롬/웨일이 http 주소에서 "안전하게 받을 수
        # 없음"이라고 막아버린다(코어박스 다운로드와 동일한 이유로 inline).
        return send_from_directory(
            UPLOAD_DIR, item["file_name"],
            as_attachment=False, download_name=item["original_name"] or item["file_name"],
        )
    finally:
        conn.close()


# ---- 고정지출(구독/자동이체) ----


@app.route("/api/recurring", methods=["POST"])
def create_recurring_payment():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "이름을 입력하세요."}), 400
    try:
        amount = float(data.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0
    try:
        payment_day = int(data.get("payment_day") or 0)
    except (TypeError, ValueError):
        payment_day = 0
    card_id = _parse_card_id(data.get("card_id"))
    try:
        current_balance = float(data.get("current_balance") or 0)
    except (TypeError, ValueError):
        current_balance = 0

    conn = get_conn()
    try:
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) AS m FROM recurring_payments").fetchone()["m"]
        conn.execute(
            """INSERT INTO recurring_payments (name, category, amount, payment_method, card_id,
               payment_day, billing_cycle, usage_period, active, memo, sort_order, billing_alias,
               current_balance)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                (data.get("category") or "").strip(),
                amount,
                (data.get("payment_method") or "").strip(),
                card_id,
                payment_day,
                (data.get("billing_cycle") or "월간").strip(),
                (data.get("usage_period") or "").strip(),
                1 if data.get("active", True) else 0,
                (data.get("memo") or "").strip(),
                max_order + 1,
                (data.get("billing_alias") or "").strip(),
                current_balance,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/recurring/<int:item_id>", methods=["PUT"])
def update_recurring_payment(item_id):
    data = request.get_json(force=True) or {}
    conn = get_conn()
    try:
        item = conn.execute("SELECT * FROM recurring_payments WHERE id = ?", (item_id,)).fetchone()
        if not item:
            return jsonify({"error": "항목을 찾을 수 없습니다."}), 404

        name = (data.get("name") or item["name"]).strip()
        try:
            amount = float(data.get("amount", item["amount"]))
        except (TypeError, ValueError):
            amount = item["amount"]
        try:
            payment_day = int(data.get("payment_day", item["payment_day"]))
        except (TypeError, ValueError):
            payment_day = item["payment_day"]
        active = 1 if data.get("active", bool(item["active"])) else 0
        card_id = _parse_card_id(data["card_id"]) if "card_id" in data else item["card_id"]
        try:
            current_balance = float(data.get("current_balance", item["current_balance"]))
        except (TypeError, ValueError):
            current_balance = item["current_balance"]

        conn.execute(
            """UPDATE recurring_payments SET name=?, category=?, amount=?, payment_method=?,
               card_id=?, payment_day=?, billing_cycle=?, usage_period=?, active=?, memo=?,
               billing_alias=?, current_balance=? WHERE id=?""",
            (
                name,
                (data.get("category", item["category"]) or "").strip(),
                amount,
                (data.get("payment_method", item["payment_method"]) or "").strip(),
                card_id,
                payment_day,
                (data.get("billing_cycle", item["billing_cycle"]) or "월간").strip(),
                (data.get("usage_period", item["usage_period"]) or "").strip(),
                active,
                (data.get("memo", item["memo"]) or "").strip(),
                (data.get("billing_alias", item["billing_alias"]) or "").strip(),
                current_balance,
                item_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/recurring/<int:item_id>", methods=["DELETE"])
def delete_recurring_payment(item_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM recurring_payments WHERE id = ?", (item_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


def _match_candidates(conn, table, item_id, name_column):
    """아직 다른 고정지출/보험에 안 묶인 알림들을 후보로 보여준다. 이름/별칭
    (billing_alias)에 들어있는 단어가 가맹점명에 포함되는 후보를 맨 위로
    올려서("이름 일치" 표시) 먼저 보이게 하고, 나머지는 최근 것부터 채운다.
    예전엔 그냥 "최근 100건"만 보여줘서, 알림이 많이 쌓인 상태에서 한 달에
    한 번만 결제되는 항목(카드/계좌 알림에 다른 이름으로 찍히는 경우도
    billing_alias를 등록해두는 이유가 바로 이거다)은 최근 100건 안에 아예
    안 들어가 후보에서 완전히 안 보이는 문제가 있었다."""
    card_label_for = _card_label_matcher(conn)
    rp = conn.execute(f"SELECT {name_column}, billing_alias FROM {table} WHERE id = ?", (item_id,)).fetchone()
    tokens = []
    if rp:
        tokens = [t.lower() for t in (_safe_name_tokens(rp[name_column]) + _safe_name_tokens(rp["billing_alias"]))]

    def is_name_match(merchant):
        merchant_lower = re.sub(r"\s", "", (merchant or "").lower())
        return bool(tokens) and any(tok in merchant_lower for tok in tokens)

    rows = conn.execute(
        """SELECT * FROM inbox_items
           WHERE recurring_id IS NULL AND insurance_id IS NULL
           ORDER BY occurred_at DESC"""
    ).fetchall()
    matched = [r for r in rows if is_name_match(r["merchant"])]
    others = [r for r in rows if not is_name_match(r["merchant"])]
    picked = (matched + others)[:150]
    return [
        {
            "id": r["id"],
            "amount": r["amount"],
            "merchant": r["merchant"],
            "last4": r["last4"],
            "card_label": card_label_for(r["last4"]),
            "occurred_at": r["occurred_at"],
            "status": r["status"],
            "name_matched": is_name_match(r["merchant"]),
        }
        for r in picked
    ]


@app.route("/api/recurring/<int:item_id>/match-candidates", methods=["GET"])
def recurring_match_candidates(item_id):
    """고정지출용 - _match_candidates 참고. 혜택배정 큐(status)는 전혀
    건드리지 않으므로, 이미 혜택에 배정됐거나 "혜택 없음" 처리된 알림도
    후보에 포함된다(고정지출 확인은 별개 작업)."""
    conn = get_conn()
    try:
        return jsonify(_match_candidates(conn, "recurring_payments", item_id, "name"))
    finally:
        conn.close()


def _parse_confirm_match_ids(data):
    """confirm-match 요청에서 선택된 알림 id 목록을 뽑는다. 예전처럼
    inbox_item_id 하나만 보내도 되고(단일 매칭, 하위호환), 여러 달치를
    한 번에 고를 수 있게 새로 추가한 inbox_item_ids(배열)도 받는다."""
    ids = []
    if "inbox_item_ids" in data and isinstance(data["inbox_item_ids"], list):
        for v in data["inbox_item_ids"]:
            try:
                ids.append(int(v))
            except (TypeError, ValueError):
                pass
    elif "inbox_item_id" in data:
        try:
            ids.append(int(data["inbox_item_id"]))
        except (TypeError, ValueError):
            pass
    return ids


@app.route("/api/recurring/<int:item_id>/confirm-match", methods=["POST"])
def confirm_recurring_match(item_id):
    data = request.get_json(force=True) or {}
    inbox_ids = _parse_confirm_match_ids(data)
    if not inbox_ids:
        return jsonify({"error": "결제 내역을 선택하세요."}), 400

    conn = get_conn()
    try:
        matched = 0
        for inbox_id in inbox_ids:
            item = conn.execute("SELECT * FROM inbox_items WHERE id = ?", (inbox_id,)).fetchone()
            if not item or item["recurring_id"] is not None:
                continue
            label = _match_time_card_label(conn, item, "recurring_payments", item_id)
            conn.execute(
                "UPDATE inbox_items SET recurring_id = ?, matched_card_label = ? WHERE id = ?",
                (item_id, label, inbox_id),
            )
            matched += 1
        conn.commit()
        if not matched:
            return jsonify({"error": "이미 처리되었거나 존재하지 않는 내역입니다."}), 404
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/recurring/<int:item_id>/unmatch", methods=["POST"])
def unmatch_recurring(item_id):
    """이번 달 매칭만 취소한다(알림의 status는 원래 손댄 적이 없으므로 그대로 둠)."""
    conn = get_conn()
    try:
        current_ym = current_year_month()
        conn.execute(
            """UPDATE inbox_items SET recurring_id = NULL
               WHERE recurring_id = ? AND substr(occurred_at, 1, 7) = ?""",
            (item_id, current_ym),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/match/<int:inbox_id>/unlink", methods=["POST"])
def unlink_match(inbox_id):
    """고정지출/보험 "내역" 패널에서 특정 건(이번 달이든 지난 달이든) 매칭만
    하나 풀어준다. 알림 자체(inbox_items 행)나 status는 그대로 둔다.

    두 가지를 같이 기록해서, 해제한 게 다시 자동으로 안 붙게 한다.
    automatch_excluded는 "이 알림 자체"를 영구히 자동매칭 대상에서 뺀다
    (재업로드/"자동 매칭 시도"가 같은 알림을 다시 건드리지 않게). 그런데
    다음 달에 같은 가맹점에서 또 결제가 오면 그건 새 알림(새 id)이라
    automatch_excluded만으론 못 막으므로, 매칭돼 있던 항목의
    excluded_merchants에도 이 가맹점명을 적어둔다 - 그래야 같은 가맹점이
    또 와도 그 항목으로는 다시 안 묶인다."""
    conn = get_conn()
    try:
        item = conn.execute(
            "SELECT recurring_id, insurance_id, merchant FROM inbox_items WHERE id = ?", (inbox_id,)
        ).fetchone()
        if item and item["merchant"]:
            merchant_key = _merchant_base(item["merchant"])
            for table, fk_id in (
                ("recurring_payments", item["recurring_id"]),
                ("insurances", item["insurance_id"]),
            ):
                if not fk_id:
                    continue
                rp = conn.execute(
                    f"SELECT excluded_merchants FROM {table} WHERE id = ?", (fk_id,)
                ).fetchone()
                existing = [m for m in (rp["excluded_merchants"] or "").split("\n") if m] if rp else []
                if merchant_key not in existing:
                    existing.append(merchant_key)
                    conn.execute(
                        f"UPDATE {table} SET excluded_merchants = ? WHERE id = ?",
                        ("\n".join(existing), fk_id),
                    )
        # 이 알림이 recurring_id/insurance_id 말고 benefit_id로도 같이
        # 배정돼 있었으면(혜택 내역에 usage_logs 행까지 있는 경우), 그것도
        # 같이 지워야 한다 - 안 지우면 "해제"로 알림 큐엔 돌아왔는데 혜택
        # 내역 목록에는 여전히 남아서 같은 알림이 두 군데(알림 큐 + 혜택
        # 내역)에 동시에 떠버리는 사고가 난다(완전히 풀리는 게 아니라
        # 반쪽만 풀리는 상태였음 - 실제로 지적받고 발견함).
        conn.execute("DELETE FROM usage_logs WHERE source_inbox_id = ?", (inbox_id,))
        # status/card_id/benefit_id도 같이 pending으로 되돌려야 "받은 알림"
        # 큐에 다시 뜬다(serialize_inbox가 status='pending'만 보여줌). 이걸
        # 안 돌려놓으면, 이 알림이 recurring_id 말고 benefit_id로도 같이
        # 배정돼 있던 경우(해외 1원짜리 사고처럼 두 군데 다 걸려있던 경우)
        # status가 'assigned'로 남아서 "해제"를 눌러도 큐에 다시 안 살아나고
        # 그냥 없어진 것처럼 보이는 버그가 있었다(실제로 발생 확인됨).
        conn.execute(
            "UPDATE inbox_items SET recurring_id = NULL, insurance_id = NULL, automatch_excluded = 1, "
            "status = 'pending', card_id = NULL, benefit_id = NULL WHERE id = ?",
            (inbox_id,),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/loans/auto-match", methods=["POST"])
def auto_match_all_loans():
    """큐에 남은 알림 전체에 대출/고정지출/보험 자동매칭을 한 번에 다시
    시도한다(대출 탭 버튼이지만 고정지출/보험 탭 버튼과 동작은 완전히
    같음 - _auto_match_all_pending 참고)."""
    conn = get_conn()
    try:
        matched = _auto_match_all_pending(conn)
    finally:
        conn.close()
    result = serialize_full()
    result["auto_match_count"] = matched
    return jsonify(result)


@app.route("/api/recurring/auto-match", methods=["POST"])
def auto_match_all_recurring():
    """큐에 남은 알림 전체에 대출/고정지출/보험 자동매칭을 한 번에 다시
    시도한다(고정지출 탭 버튼이지만 대출/보험 탭 버튼과 동작은 완전히
    같음 - _auto_match_all_pending 참고)."""
    conn = get_conn()
    try:
        matched = _auto_match_all_pending(conn)
    finally:
        conn.close()
    result = serialize_full()
    result["auto_match_count"] = matched
    return jsonify(result)


@app.route("/api/insurances", methods=["POST"])
def create_insurance():
    data = request.get_json(force=True) or {}
    company = (data.get("company") or "").strip()
    product_name = (data.get("product_name") or "").strip()
    if not product_name:
        return jsonify({"error": "상품명을 입력하세요."}), 400
    try:
        monthly_premium = float(data.get("monthly_premium") or 0)
    except (TypeError, ValueError):
        monthly_premium = 0
    try:
        payment_day = int(data.get("payment_day") or 0)
    except (TypeError, ValueError):
        payment_day = 0
    try:
        current_balance = float(data.get("current_balance") or 0)
    except (TypeError, ValueError):
        current_balance = 0
    try:
        interest_rate = float(data.get("interest_rate") or 0)
    except (TypeError, ValueError):
        interest_rate = 0
    card_id = _parse_card_id(data.get("card_id"))

    conn = get_conn()
    try:
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) AS m FROM insurances").fetchone()["m"]
        conn.execute(
            """INSERT INTO insurances (company, product_name, insured_name, monthly_premium,
               payment_day, payment_method, card_id, start_date, payment_end_date, maturity_date,
               current_balance, interest_rate, active, memo, sort_order, billing_alias)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                company,
                product_name,
                (data.get("insured_name") or "본인").strip(),
                monthly_premium,
                payment_day,
                (data.get("payment_method") or "").strip(),
                card_id,
                (data.get("start_date") or "").strip(),
                (data.get("payment_end_date") or "").strip(),
                (data.get("maturity_date") or "").strip(),
                current_balance,
                interest_rate,
                1 if data.get("active", True) else 0,
                (data.get("memo") or "").strip(),
                max_order + 1,
                (data.get("billing_alias") or "").strip(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/insurances/<int:item_id>", methods=["PUT"])
def update_insurance(item_id):
    data = request.get_json(force=True) or {}
    conn = get_conn()
    try:
        item = conn.execute("SELECT * FROM insurances WHERE id = ?", (item_id,)).fetchone()
        if not item:
            return jsonify({"error": "항목을 찾을 수 없습니다."}), 404

        product_name = (data.get("product_name") or item["product_name"]).strip()
        try:
            monthly_premium = float(data.get("monthly_premium", item["monthly_premium"]))
        except (TypeError, ValueError):
            monthly_premium = item["monthly_premium"]
        try:
            payment_day = int(data.get("payment_day", item["payment_day"]))
        except (TypeError, ValueError):
            payment_day = item["payment_day"]
        try:
            sort_order = int(data.get("sort_order", item["sort_order"]))
        except (TypeError, ValueError):
            sort_order = item["sort_order"]
        try:
            current_balance = float(data.get("current_balance", item["current_balance"]))
        except (TypeError, ValueError):
            current_balance = item["current_balance"]
        try:
            interest_rate = float(data.get("interest_rate", item["interest_rate"]))
        except (TypeError, ValueError):
            interest_rate = item["interest_rate"]
        active = 1 if data.get("active", bool(item["active"])) else 0
        card_id = _parse_card_id(data["card_id"]) if "card_id" in data else item["card_id"]

        conn.execute(
            """UPDATE insurances SET company=?, product_name=?, insured_name=?, monthly_premium=?,
               payment_day=?, payment_method=?, card_id=?, start_date=?, payment_end_date=?,
               maturity_date=?, current_balance=?, interest_rate=?, active=?, memo=?, sort_order=?,
               billing_alias=? WHERE id=?""",
            (
                (data.get("company", item["company"]) or "").strip(),
                product_name,
                (data.get("insured_name", item["insured_name"]) or "본인").strip(),
                monthly_premium,
                payment_day,
                (data.get("payment_method", item["payment_method"]) or "").strip(),
                card_id,
                (data.get("start_date", item["start_date"]) or "").strip(),
                (data.get("payment_end_date", item["payment_end_date"]) or "").strip(),
                (data.get("maturity_date", item["maturity_date"]) or "").strip(),
                current_balance,
                interest_rate,
                active,
                (data.get("memo", item["memo"]) or "").strip(),
                sort_order,
                (data.get("billing_alias", item["billing_alias"]) or "").strip(),
                item_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/insurances/<int:item_id>", methods=["DELETE"])
def delete_insurance(item_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM insurances WHERE id = ?", (item_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/insurances/<int:item_id>/match-candidates", methods=["GET"])
def insurance_match_candidates(item_id):
    """보험용 - _match_candidates 참고."""
    conn = get_conn()
    try:
        return jsonify(_match_candidates(conn, "insurances", item_id, "product_name"))
    finally:
        conn.close()


@app.route("/api/insurances/<int:item_id>/confirm-match", methods=["POST"])
def confirm_insurance_match(item_id):
    data = request.get_json(force=True) or {}
    inbox_ids = _parse_confirm_match_ids(data)
    if not inbox_ids:
        return jsonify({"error": "결제 내역을 선택하세요."}), 400

    conn = get_conn()
    try:
        matched = 0
        for inbox_id in inbox_ids:
            item = conn.execute("SELECT * FROM inbox_items WHERE id = ?", (inbox_id,)).fetchone()
            if not item or item["insurance_id"] is not None:
                continue
            label = _match_time_card_label(conn, item, "insurances", item_id)
            conn.execute(
                "UPDATE inbox_items SET insurance_id = ?, matched_card_label = ? WHERE id = ?",
                (item_id, label, inbox_id),
            )
            matched += 1
        conn.commit()
        if not matched:
            return jsonify({"error": "이미 처리되었거나 존재하지 않는 내역입니다."}), 404
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/insurances/<int:item_id>/unmatch", methods=["POST"])
def unmatch_insurance(item_id):
    conn = get_conn()
    try:
        current_ym = current_year_month()
        conn.execute(
            """UPDATE inbox_items SET insurance_id = NULL
               WHERE insurance_id = ? AND substr(occurred_at, 1, 7) = ?""",
            (item_id, current_ym),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/insurances/auto-match", methods=["POST"])
def auto_match_all_insurances():
    """큐에 남은 알림 전체에 대출/고정지출/보험 자동매칭을 한 번에 다시
    시도한다(보험 탭 버튼이지만 대출/고정지출 탭 버튼과 동작은 완전히
    같음 - _auto_match_all_pending 참고)."""
    conn = get_conn()
    try:
        matched = _auto_match_all_pending(conn)
    finally:
        conn.close()
    result = serialize_full()
    result["auto_match_count"] = matched
    return jsonify(result)


@app.route("/api/medical-expenses/<int:item_id>/claim", methods=["POST"])
def toggle_medical_claim(item_id):
    """병원비/약국 결제 하나를 실손 등으로 청구했는지 체크/해제한다."""
    data = request.get_json(force=True, silent=True) or {}
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE inbox_items SET medical_claimed = ? WHERE id = ?",
            (1 if data.get("claimed") else 0, item_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/medical-expenses/bulk-exclude", methods=["POST"])
def bulk_exclude_medical_expenses():
    """병원비 목록에서 여러 건을 한 번에 "이건 병원비 아님"으로 제외한다.
    inbox_items 자체는 안 지우고 medical_excluded만 켜서, 이 목록에서만 다시
    안 보이게 한다(혜택배정/고정지출·보험 매칭에는 영향 없음)."""
    data = request.get_json(force=True) or {}
    ids = data.get("ids") or []
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return jsonify({"error": "제외할 항목을 선택하세요."}), 400
    if not ids:
        return jsonify({"error": "제외할 항목을 선택하세요."}), 400

    conn = get_conn()
    try:
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE inbox_items SET medical_excluded = 1 WHERE id IN ({placeholders})", ids
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


def _clean_tier_table(raw):
    """[[기준금액,한도], ...] 형태인지 검증하고 정돈된 JSON 문자열(또는 '')을 돌려준다."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        tiers = json.loads(raw)
        if not isinstance(tiers, list):
            raise ValueError
        cleaned = [[float(t[0]), float(t[1])] for t in tiers]
    except (ValueError, TypeError, IndexError, KeyError):
        raise ValueError("구간표 형식이 올바르지 않습니다. 예: [[400000,5000],[800000,10000]]")
    return json.dumps(cleaned)


def _clean_rate_table(raw):
    """[["키워드1,키워드2",할인율], ...] 또는 [["키워드",할인율,건당한도], ...] 또는
    [["키워드",할인율,건당한도,일한도,월한도], ...] 또는
    [["키워드",할인율,건당한도,일한도,월한도,업종별월적립한도], ...] 형태인지 검증하고
    정돈된 JSON 문자열(또는 '')을 돌려준다."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        rows = json.loads(raw)
        if not isinstance(rows, list):
            raise ValueError
        cleaned = []
        for r in rows:
            if len(r) not in (2, 3, 4, 5, 6):
                raise ValueError
            row = [str(r[0]), float(r[1])]
            for extra in r[2:]:
                row.append(float(extra))
            cleaned.append(row)
    except (ValueError, TypeError, IndexError, KeyError):
        raise ValueError(
            "업종별 할인율 형식이 올바르지 않습니다. "
            '예: [["쇼핑몰,백화점,이커머스",10],["버스,지하철,택시",5]] '
            '(건당한도는 3번째 값, 일/월 횟수한도는 4·5번째 값, 업종별 월 적립(포인트) '
            '한도는 6번째 값으로 추가 가능: [["쇼핑",10,50000,1,5,10000]])'
        )
    return json.dumps(cleaned, ensure_ascii=False)


@app.route("/api/cards/<int:card_id>/benefits", methods=["POST"])
def create_benefit(card_id):
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "혜택 이름을 입력하세요."}), 400
    try:
        limit_value = float(data.get("limit_value") or 0)
    except (TypeError, ValueError):
        limit_value = 0
    limit_type = data.get("limit_type") if data.get("limit_type") in ("amount", "count") else "amount"
    calc_mode = data.get("calc_mode") if data.get("calc_mode") in ("raw", "change_under_1000", "percent_discount") else "raw"
    try:
        discount_percent = float(data.get("discount_percent") or 0)
    except (TypeError, ValueError):
        discount_percent = 0
    try:
        per_txn_cap = float(data.get("per_txn_cap") or 0)
    except (TypeError, ValueError):
        per_txn_cap = 0
    try:
        tier_table = _clean_tier_table(data.get("tier_table"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    try:
        percent_tier_table = _clean_tier_table(data.get("percent_tier_table"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    try:
        rate_table = _clean_rate_table(data.get("rate_table"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    always_doubled = 1 if data.get("always_doubled") else 0
    try:
        start_hour = int(data.get("start_hour")) if data.get("start_hour") not in (None, "") else None
    except (TypeError, ValueError):
        start_hour = None
    try:
        end_hour = int(data.get("end_hour")) if data.get("end_hour") not in (None, "") else None
    except (TypeError, ValueError):
        end_hour = None

    conn = get_conn()
    try:
        card = conn.execute("SELECT id FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not card:
            return jsonify({"error": "카드를 찾을 수 없습니다."}), 404
        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS m FROM benefits WHERE card_id = ?", (card_id,)
        ).fetchone()["m"]
        conn.execute(
            """INSERT INTO benefits (card_id, name, limit_type, limit_value, memo, merchant_keywords, tier_table,
               calc_mode, discount_percent, per_txn_cap, rate_table, always_doubled, sort_order, percent_tier_table,
               start_hour, end_hour)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                card_id, name, limit_type, limit_value,
                (data.get("memo") or "").strip(),
                (data.get("merchant_keywords") or "").strip(),
                tier_table,
                calc_mode,
                discount_percent,
                per_txn_cap,
                rate_table,
                always_doubled,
                max_order + 1,
                percent_tier_table,
                start_hour,
                end_hour,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/benefits/<int:benefit_id>", methods=["PUT"])
def update_benefit(benefit_id):
    data = request.get_json(force=True) or {}
    conn = get_conn()
    try:
        b = conn.execute("SELECT * FROM benefits WHERE id = ?", (benefit_id,)).fetchone()
        if not b:
            return jsonify({"error": "혜택을 찾을 수 없습니다."}), 404

        name = (data.get("name") or b["name"]).strip()
        try:
            limit_value = float(data.get("limit_value", b["limit_value"]))
        except (TypeError, ValueError):
            limit_value = b["limit_value"]
        limit_type = data.get("limit_type") if data.get("limit_type") in ("amount", "count") else b["limit_type"]
        calc_mode = data.get("calc_mode") if data.get("calc_mode") in ("raw", "change_under_1000", "percent_discount") else b["calc_mode"]
        try:
            discount_percent = float(data.get("discount_percent", b["discount_percent"]))
        except (TypeError, ValueError):
            discount_percent = b["discount_percent"]
        try:
            per_txn_cap = float(data.get("per_txn_cap", b["per_txn_cap"]))
        except (TypeError, ValueError):
            per_txn_cap = b["per_txn_cap"]
        try:
            tier_table = _clean_tier_table(data.get("tier_table", b["tier_table"]))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        try:
            percent_tier_table = _clean_tier_table(data.get("percent_tier_table", b["percent_tier_table"]))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        try:
            rate_table = _clean_rate_table(data.get("rate_table", b["rate_table"]))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        always_doubled = 1 if data.get("always_doubled", b["always_doubled"]) else 0
        new_memo = (data.get("memo", b["memo"]) or "").strip()
        # 사용자가 설명(memo)을 실제로 바꾼 경우에만 "직접 수정함" 표시를 남긴다.
        # 이 표시가 있어야 update.py가 나중에 안내장 기준 설명으로 덮어쓰지 않고
        # 사용자가 쓴 내용을 지켜준다.
        memo_edited_by_user = 1 if new_memo != (b["memo"] or "") else b["memo_edited_by_user"]
        try:
            start_hour = int(data.get("start_hour", b["start_hour"])) if data.get("start_hour", b["start_hour"]) not in (None, "") else None
        except (TypeError, ValueError):
            start_hour = b["start_hour"]
        try:
            end_hour = int(data.get("end_hour", b["end_hour"])) if data.get("end_hour", b["end_hour"]) not in (None, "") else None
        except (TypeError, ValueError):
            end_hour = b["end_hour"]

        conn.execute(
            """UPDATE benefits SET name = ?, limit_type = ?, limit_value = ?, memo = ?, merchant_keywords = ?,
               tier_table = ?, calc_mode = ?, discount_percent = ?, per_txn_cap = ?, rate_table = ?,
               always_doubled = ?, memo_edited_by_user = ?, percent_tier_table = ?, start_hour = ?, end_hour = ?
               WHERE id = ?""",
            (
                name, limit_type, limit_value,
                new_memo,
                (data.get("merchant_keywords", b["merchant_keywords"]) or "").strip(),
                tier_table,
                calc_mode,
                discount_percent,
                per_txn_cap,
                rate_table,
                always_doubled,
                memo_edited_by_user,
                percent_tier_table,
                start_hour,
                end_hour,
                benefit_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/benefits/<int:benefit_id>", methods=["DELETE"])
def delete_benefit(benefit_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM benefits WHERE id = ?", (benefit_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


def _duplicate_merchant_today(conn, benefit_id, used_at, merchant):
    """더모아형 혜택은 "동일 가맹점 1일 1회"만 적립되므로, 같은 날 같은
    가맹점으로 이미 기록이 있으면 그 뒤 결제는 적립 대상에서 제외한다."""
    if not merchant:
        return False
    row = conn.execute(
        """SELECT 1 FROM usage_logs WHERE benefit_id = ? AND used_at = ? AND merchant = ?
           LIMIT 1""",
        (benefit_id, used_at, merchant),
    ).fetchone()
    return row is not None


def _category_usage_count(conn, benefit_id, category, used_at, monthly=False, before_log_id=None):
    """rate_table 업종(category)별로 오늘(또는 이번 달) 몇 번 사용됐는지 센다.
    Daily Plan처럼 "업종별 일 1회, 월 5회까지"인 경우 한도 초과 여부 확인에 쓴다.
    before_log_id는 "다시 계산"(_recalculate_benefit_logs)에서 쓴다 - 그냥
    자기 자신만 빼고 세면(id != 자신), 같은 날 같은 업종으로 이미 등록된
    다른 기록 두 건을 재계산할 때 서로가 서로를 "이미 썼음"으로 세서 둘 다
    막혀버리는 모순이 생긴다(실제로 그렇게 됨을 테스트로 확인). 기록 당시엔
    "그보다 먼저 들어온(id가 더 작은) 기록"만 보고 판단했으므로, 재계산도
    그 시점 그대로 "자신보다 id가 작은 것만" 세야 원래 순서(선착순)가
    재현된다."""
    params = [benefit_id, category]
    if monthly:
        params.append(used_at[:7])
        date_clause = "substr(used_at, 1, 7) = ?"
    else:
        params.append(used_at)
        date_clause = "used_at = ?"
    id_clause = ""
    if before_log_id is not None:
        id_clause = " AND id < ?"
        params.append(before_log_id)
    row = conn.execute(
        f"SELECT COUNT(*) AS c FROM usage_logs WHERE benefit_id = ? AND rate_category = ? AND {date_clause}{id_clause}",
        params,
    ).fetchone()
    return row["c"]


def _category_month_earned(conn, benefit_id, category, used_at, before_log_id=None):
    """rate_table 업종(category)별로 이번 달 이미 적립된 금액(포인트) 합계를 돌려준다.
    씨티 리워드처럼 "업종별 월 1만 포인트까지만"처럼 횟수가 아니라 적립액 자체를
    업종별로 깎는 카드의 한도 체크에 쓴다. before_log_id는 _category_usage_count와
    같은 이유(선착순 순서 재현)로 "다시 계산" 시 자신보다 먼저 들어온 기록까지만
    합산하기 위함."""
    params = [benefit_id, category, used_at[:7]]
    id_clause = ""
    if before_log_id is not None:
        id_clause = " AND id < ?"
        params.append(before_log_id)
    row = conn.execute(
        f"""SELECT COALESCE(SUM(used_value), 0) AS total FROM usage_logs
           WHERE benefit_id = ? AND rate_category = ? AND substr(used_at, 1, 7) = ?{id_clause}""",
        params,
    ).fetchone()
    return row["total"]


def _plan_day_slot_used(conn, benefit_id, used_at, before_log_id=None):
    """신한 Discount Plan+ "Plan Day 서비스"(매월 1일, Time Plan·Daily Plan 각각
    그날 첫 번째 할인 거래 1건에 할인율 2배) - 업종별이 아니라 혜택 전체
    기준으로 하루에 딱 1번만 적용되므로, 이 혜택에 오늘 이미 Plan Day
    보너스가 적용된 기록이 있는지 확인한다. before_log_id는 "다시 계산"
    시 자신보다 먼저 들어온 기록 중에만 있었는지로 판단하기 위함(선착순
    순서 재현 - _category_usage_count 설명 참고)."""
    if before_log_id is not None:
        row = conn.execute(
            "SELECT 1 FROM usage_logs WHERE benefit_id = ? AND used_at = ? AND memo LIKE '%Plan Day 2배%' AND id < ?",
            (benefit_id, used_at, before_log_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM usage_logs WHERE benefit_id = ? AND used_at = ? AND memo LIKE '%Plan Day 2배%'",
            (benefit_id, used_at),
        ).fetchone()
    return row is not None


def _perf_spend_for_card(conn, card, ref_date=None):
    """카드의 "전월실적" 합계 (_serialize_cards와 같은 기준: 수동 입력이 있으면
    그걸 쓰고, 없으면 지난달 확인된 결제 알림 합계에서 실적 제외 항목을 뺀 자동
    집계). 구간표(tier_table)가 있는 혜택이 전월실적 미달로 혜택 대상이 아닌지
    판단할 때 재사용한다.

    ref_date를 넘기면 "오늘" 대신 그 날짜가 속한 달의 전월실적을 본다 - 밀린
    과거 결제(예: 8월 거래)를 지금(11월) 기록할 때, "오늘 기준 지난달(10월)"
    실적으로 판단해버리면 엉뚱한 기준이 된다. 그 결제가 일어난 달(8월) 기준
    지난달(7월) 실적으로 판단해야 맞다."""
    prev_month = previous_year_month(ref_date)
    manual_row = conn.execute(
        "SELECT total_spend FROM performance WHERE card_id = ? AND year_month = ?",
        (card["id"], prev_month),
    ).fetchone()
    if manual_row:
        return manual_row["total_spend"]
    rows = conn.execute(
        """SELECT amount, merchant FROM inbox_items
           WHERE card_id = ? AND status IN ('assigned', 'no_benefit')
           AND substr(occurred_at, 1, 7) = ?""",
        (card["id"], prev_month),
    ).fetchall()
    return sum(
        r["amount"] for r in rows
        if not is_performance_excluded(r["merchant"], card["perf_excluded_keywords"])
    )


def _apply_calc_mode(conn, benefit_id, benefit, raw_value, doubled, memo, merchant="", used_at="", category_override="", occurred_at="", exclude_log_id=None, is_online=False):
    """혜택의 calc_mode에 따라 입력값(결제금액 등)을 실제 한도 소진값으로 변환한다.

    "raw"는 입력값을 그대로 쓰고, 나머지는 결제금액을 자동으로 실제
    혜택금액(잔돈/할인액)으로 환산한다 - 결제금액 총액이 그대로 한도를
    깎아먹지 않도록. (used_value, memo, rate_category, error) 를 돌려준다.
    전월실적 미달/할인 적용 시간대 밖 결제/업종별 일·월 횟수 초과/업종별
    월적립한도 초과처럼 "이 혜택 대상이긴 한데 조건을 못 채운" 경우는 기록
    자체를 막지 않고 0원으로 기록하면서 메모에 사유를 남긴다(큐에 계속
    남겨두지 않고, 어떤 결제가 왜 할인을 못 받았는지 내역에서 바로 보이게).
    error는 그 외의 문제에만 쓴다.
    category_override가 있으면(네이버페이/토스페이처럼 가맹점명으로 업종을 알 수
    없어서 사용자가 직접 고른 경우) 가맹점명 매칭 대신 그 업종 값을 그대로 쓴다.
    occurred_at(알림/엑셀의 실제 결제 시각, "YYYY-MM-DD HH:MM:SS")이 있으면
    Time Plan의 DAY/NIGHT처럼 "이 시간대 결제만" 적용되는 업종 조건을 확인하는 데
    쓴다 - 수기로 기록할 땐(occurred_at 없음) 시간 정보가 없어서 이 조건을
    건너뛴다(차단하지 않음).
    exclude_log_id는 "다시 계산"(이미 기록된 usage_logs 행을 최신 전월실적/
    구간표 기준으로 재계산)에서만 쓴다 - 일/월 횟수·업종별 월한도·Plan Day·
    혜택 전체 월한도를 셀 때 그 행 자기 자신을 "이미 쓴 걸로" 중복으로 세지
    않기 위함(새로 기록할 땐 그 행이 아직 없으므로 항상 None).
    """
    hour = None
    if occurred_at and len(occurred_at) >= 13:
        try:
            hour = int(occurred_at[11:13])
        except ValueError:
            hour = None

    block_reason = None

    # 혜택 전체에 거는 시간대 조건(업종별 요율표 없이 단일 할인율인 혜택용 -
    # LADY CLASSIC "브런치 할인"처럼 "오전 11시~오후 2시만" 적용되는 경우).
    try:
        b_start_hour, b_end_hour = benefit["start_hour"], benefit["end_hour"]
    except (IndexError, KeyError):
        b_start_hour = b_end_hour = None
    if b_start_hour is not None and b_end_hour is not None and hour is not None:
        if not (b_start_hour <= hour < b_end_hour):
            block_reason = "시간외 결제"

    effective_discount_percent = benefit["discount_percent"]
    if benefit["tier_table"] or (benefit["calc_mode"] == "percent_discount" and benefit["percent_tier_table"]):
        card = conn.execute("SELECT * FROM cards WHERE id = ?", (benefit["card_id"],)).fetchone()
        try:
            ref_date = datetime.strptime(used_at, "%Y-%m-%d").date() if used_at else None
        except ValueError:
            ref_date = None
        if benefit["tier_table"] and card and tier_limit_for_spend(benefit["tier_table"], _perf_spend_for_card(conn, card, ref_date)) == 0:
            block_reason = block_reason or "전월실적 미달"
        if benefit["calc_mode"] == "percent_discount" and benefit["percent_tier_table"] and card:
            tiered_percent = tier_limit_for_spend(benefit["percent_tier_table"], _perf_spend_for_card(conn, card, ref_date))
            if tiered_percent is not None:
                effective_discount_percent = tiered_percent

    if benefit["calc_mode"] == "change_under_1000":
        doubled = doubled or bool(benefit["always_doubled"])
        earned = 0 if block_reason else compute_change_earned(raw_value, doubled)
        tag = f"({block_reason})" if block_reason else ('(2배)' if doubled else '')
        parts = [f"결제 {raw_value:,.0f}원 → 잔돈 {earned:,.0f}원{tag}"]
        if memo:
            parts.append(memo)
        return earned, " · ".join(parts), "", None

    if benefit["calc_mode"] == "percent_discount":
        if category_override:
            percent, cap, daily_limit, monthly_limit, monthly_point_cap, time_blocked = rate_table_entry_by_label(
                benefit["rate_table"], category_override, effective_discount_percent, benefit["per_txn_cap"], hour
            )
            category = category_override
            if time_blocked:
                block_reason = block_reason or "시간외 결제"
        else:
            percent, cap, daily_limit, monthly_limit, monthly_point_cap, category = match_rate_table(
                merchant, benefit["rate_table"], effective_discount_percent, benefit["per_txn_cap"], hour
            )
            if category == TIME_BLOCKED_LABEL:
                block_reason = block_reason or "시간외 결제"
                category = ""
            elif not category and is_foreign_merchant(merchant) and any(
                c["label"] == "해외" for c in parse_rate_table_categories(benefit["rate_table"])
            ):
                # ANTHROPIC/PayPal처럼 가맹점명만 봐서는 "해외" 키워드가 전혀
                # 안 걸리는 해외 결제는, is_foreign_merchant로 확실히 해외라고
                # 판단되면 "해외" 업종으로 한 번 더 찾는다 - 안 그러면 할인율은
                # 우연히 맞아도(기본 할인율과 같아서) 업종 추적(일/월 횟수,
                # 업종별 월적립한도)이 전혀 안 걸려서 기록에 업종이 안 남는다.
                percent, cap, daily_limit, monthly_limit, monthly_point_cap, time_blocked = rate_table_entry_by_label(
                    benefit["rate_table"], "해외", effective_discount_percent, benefit["per_txn_cap"], hour
                )
                category = "해외"
                if time_blocked:
                    block_reason = block_reason or "시간외 결제"
            # "잡화:다이소"처럼 매장 할인 키워드가 온라인몰 결제명에도 그대로
            # 걸리는데, 실제론 매장 결제만 그 할인 대상이고 온라인은 완전히
            # 해당없음인 카드가 있다(사용자 확인: 신한 Discount Plan+의
            # "잡화" 10%는 오프라인 전용, 온라인은 0%). 다른 미매칭 가맹점이
            # 기본 할인율(effective_discount_percent)로 떨어지는 것과 달리,
            # 여기는 "그 어떤 업종에도 해당 안 됨"이 맞으므로 기본 할인율이
            # 아니라 완전히 0으로 보내야 한다(effective_discount_percent를
            # 썼더니 "잡화 10%" 대신 혜택의 기본 5%로 잘못 떨어진 걸 실제
            # 화면에서 확인함 - 사용자가 원한 건 0%였음).
            if category == "잡화" and is_online:
                percent, cap, daily_limit, monthly_limit, monthly_point_cap = 0, 0, 0, 0, 0
                category = ""
        capped_note = ""
        if category and used_at and not block_reason:
            if daily_limit and _category_usage_count(conn, benefit_id, category, used_at, before_log_id=exclude_log_id) >= daily_limit:
                block_reason = "일/월 횟수 초과"
            elif monthly_limit and _category_usage_count(conn, benefit_id, category, used_at, monthly=True, before_log_id=exclude_log_id) >= monthly_limit:
                block_reason = "일/월 횟수 초과"
            elif monthly_point_cap:
                already = _category_month_earned(conn, benefit_id, category, used_at, before_log_id=exclude_log_id)
                remaining = max(0, monthly_point_cap - already)
                if remaining <= 0:
                    block_reason = "월적립한도 초과"

        if block_reason:
            category_label = f"{category} " if category else ""
            parts = [f"결제 {raw_value:,.0f}원 → 할인 0원({category_label}{block_reason})"]
            if memo:
                parts.append(memo)
            return 0, " · ".join(parts), (category or ""), None

        # Plan Day(매월 1일, Time Plan·Daily Plan 각각 그날 첫 거래 1건 2배) -
        # 해외 영역은 PDF 안내상 명시적으로 제외 대상이다.
        plan_day_bonus = False
        if (
            benefit["plan_day_double"]
            and category
            and not category.startswith("해외")
            and used_at
            and used_at.endswith("-01")
            and not _plan_day_slot_used(conn, benefit_id, used_at, before_log_id=exclude_log_id)
        ):
            percent *= 2
            plan_day_bonus = True

        earned = compute_percent_discount(raw_value, percent, cap)
        if category and used_at and monthly_point_cap:
            already = _category_month_earned(conn, benefit_id, category, used_at, before_log_id=exclude_log_id)
            remaining = max(0, monthly_point_cap - already)
            if earned > remaining:
                earned = remaining
                capped_note = f"(업종별 월한도 {monthly_point_cap:,.0f}P 초과분 제외)"

        # 업종별 한도와 별개로, 혜택 전체의 이번 달 한도(고정 limit_value 또는
        # tier_table로 정해지는 전월실적별 한도)도 넘지 않게 막는다. 예전엔
        # 건당 계산만 하고 월 누적 한도는 전혀 확인을 안 해서, 한도를 이미 다
        # 쓴 뒤에도 결제마다 할인이 그대로 계속 기록되어(카드사 실제 지급액보다
        # 많이 쌓임) 화면엔 뒤늦게 "초과"로만 표시되는 문제가 있었다.
        if earned > 0 and used_at and benefit["limit_type"] != "count":
            card_row = conn.execute("SELECT * FROM cards WHERE id = ?", (benefit["card_id"],)).fetchone()
            if card_row:
                try:
                    cycle_ref = datetime.strptime(used_at, "%Y-%m-%d").date()
                except ValueError:
                    cycle_ref = None
                cyc_start, cyc_end = cycle_bounds(card_row["reset_day"], cycle_ref)
                if benefit["tier_table"]:
                    overall_limit = tier_limit_for_spend(benefit["tier_table"], _perf_spend_for_card(conn, card_row, cycle_ref))
                else:
                    overall_limit = benefit["limit_value"] or 0
                if overall_limit and overall_limit > 0:
                    # 여기도 위와 같은 이유(선착순 순서 재현)로 "자신보다 id가
                    # 작은 것만" 합산한다 - 그냥 자기 자신만 빼면, 같은 달에
                    # 재계산 대상이 여러 건일 때 서로가 서로의 한도를 깎아먹는
                    # 모순이 생긴다.
                    id_clause = " AND id < ?" if exclude_log_id is not None else ""
                    params = [benefit_id, cyc_start.isoformat(), cyc_end.isoformat()]
                    if exclude_log_id is not None:
                        params.append(exclude_log_id)
                    already_used = conn.execute(
                        f"""SELECT COALESCE(SUM(used_value), 0) AS total FROM usage_logs
                           WHERE benefit_id = ? AND used_at >= ? AND used_at < ?{id_clause}""",
                        params,
                    ).fetchone()["total"]
                    overall_remaining = max(0, overall_limit - already_used)
                    if earned > overall_remaining:
                        earned = overall_remaining
                        capped_note += f"(이번 달 잔여한도 {overall_remaining:,.0f}원까지만)"
        category_label = f"{category} " if category else ""
        plan_day_tag = " · Plan Day 2배" if plan_day_bonus else ""
        parts = [f"결제 {raw_value:,.0f}원 → 할인 {earned:,.0f}원({category_label}{percent:g}%){capped_note}{plan_day_tag}"]
        if memo:
            parts.append(memo)
        return earned, " · ".join(parts), (category or ""), None

    return raw_value, memo, "", None


def _recalculate_benefit_logs(conn, benefit_id):
    """전월실적/구간표가 나중에 바뀌어도(예: 밀린 달치를 뒤늦게 백필하다가
    아직 그 달 실적을 안 적어놔서 "전월실적 미달 0원"으로 찍힌 뒤, 나중에
    실적을 등록/정정하는 경우), 이미 기록된 usage_logs는 그 당시 계산값에
    그대로 멈춰있다 - 기록 시점에 딱 한 번 계산해서 저장해두는 구조라서다.
    이 함수는 그 혜택의 모든 기록을 지금 등록된 최신 데이터(전월실적/
    구간표/업종별 키워드 등)로 다시 계산해서, 값이 달라진 것만 갱신한다.
    원래 매칭됐던 업종(rate_category)은 그대로 쓰고(재계산 때 가맹점명으로
    새로 찾지 않음 - 업종 자체를 바꾸는 건 이 기능의 목적이 아니라
    "업종 직접 선택" 수정으로 따로 할 일이다), 자유 메모(사용자가 직접
    적어둔 텍스트)는 원래 계산 설명문과 합쳐져 저장돼 있어서 분리해 복원할
    방법이 없어 재계산하면 비워진다."""
    benefit = conn.execute("SELECT * FROM benefits WHERE id = ?", (benefit_id,)).fetchone()
    if not benefit or benefit["calc_mode"] != "percent_discount":
        return 0
    logs = conn.execute(
        "SELECT * FROM usage_logs WHERE benefit_id = ? ORDER BY used_at ASC, id ASC", (benefit_id,)
    ).fetchall()
    changed = 0
    for log in logs:
        raw_value = log["raw_value"] if log["raw_value"] is not None else log["used_value"]
        occurred_at = ""
        if log["source_inbox_id"]:
            inbox_row = conn.execute(
                "SELECT occurred_at FROM inbox_items WHERE id = ?", (log["source_inbox_id"],)
            ).fetchone()
            if inbox_row:
                occurred_at = inbox_row["occurred_at"] or ""
        new_value, new_memo, new_category, error = _apply_calc_mode(
            conn, benefit_id, benefit, raw_value, False, "",
            merchant=log["merchant"] or "", used_at=log["used_at"],
            category_override=log["rate_category"] or "",
            occurred_at=occurred_at, exclude_log_id=log["id"],
        )
        if error:
            continue
        if new_value != log["used_value"] or new_memo != log["memo"]:
            conn.execute(
                "UPDATE usage_logs SET used_value = ?, memo = ?, rate_category = ? WHERE id = ?",
                (new_value, new_memo, new_category, log["id"]),
            )
            changed += 1
    return changed


@app.route("/api/benefits/<int:benefit_id>/recalculate", methods=["POST"])
def recalculate_benefit(benefit_id):
    conn = get_conn()
    try:
        changed = _recalculate_benefit_logs(conn, benefit_id)
        conn.commit()
    finally:
        conn.close()
    result = serialize_full()
    result["recalculate_changed"] = changed
    return jsonify(result)


@app.route("/api/benefits/<int:benefit_id>/use", methods=["POST"])
def log_usage(benefit_id):
    data = request.get_json(force=True) or {}
    conn = get_conn()
    try:
        b = conn.execute("SELECT * FROM benefits WHERE id = ?", (benefit_id,)).fetchone()
        if not b:
            return jsonify({"error": "혜택을 찾을 수 없습니다."}), 404

        try:
            raw_value = float(data.get("used_value"))
        except (TypeError, ValueError):
            raw_value = 1 if b["limit_type"] == "count" else 0
        if raw_value == 0:
            return jsonify({"error": "사용 금액(또는 횟수)을 입력하세요."}), 400

        used_at = (data.get("used_at") or "").strip()
        try:
            datetime.strptime(used_at, "%Y-%m-%d")
        except ValueError:
            used_at = date.today().isoformat()

        merchant = (data.get("merchant") or "").strip()
        memo = (data.get("memo") or "").strip()
        category_override = (data.get("category") or "").strip()

        if b["calc_mode"] == "change_under_1000" and _duplicate_merchant_today(conn, benefit_id, used_at, merchant):
            return jsonify({
                "error": f"같은 날 \"{merchant}\"에 이미 기록이 있습니다. "
                         "동일 가맹점은 1일 1회만 적립되므로(가장 먼저 결제한 건만 인정), "
                         "이 결제는 적립 대상이 아닙니다."
            }), 400

        used_value, memo, rate_category, error = _apply_calc_mode(
            conn, benefit_id, b, raw_value, bool(data.get("doubled")), memo, merchant, used_at, category_override
        )
        if error:
            return jsonify({"error": error}), 400

        conn.execute(
            """INSERT INTO usage_logs (benefit_id, used_value, used_at, merchant, memo, rate_category, raw_value)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (benefit_id, used_value, used_at, merchant, memo, rate_category, raw_value),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/logs/<int:log_id>", methods=["DELETE"])
def delete_log(log_id):
    conn = get_conn()
    try:
        log = conn.execute("SELECT source_inbox_id FROM usage_logs WHERE id = ?", (log_id,)).fetchone()
        conn.execute("DELETE FROM usage_logs WHERE id = ?", (log_id,))
        if log and log["source_inbox_id"]:
            # 받은 알림에서 배정되어 만들어진 기록이면, 삭제할 때 알림을
            # 없애버리지 않고 "받은 결제 알림" 목록으로 되돌린다(다시 배정 가능).
            # recurring_id/insurance_id도 같이 지운다 - 안 지우면 pending으로
            # 돌아간 알림이 고정지출/보험 내역 쪽에는 여전히 남아서 "알림 큐
            # + 내역"에 동시에 떠버린다(unlink_match의 반대 방향 같은 문제).
            conn.execute(
                "UPDATE inbox_items SET status = 'pending', card_id = NULL, benefit_id = NULL, "
                "recurring_id = NULL, insurance_id = NULL WHERE id = ?",
                (log["source_inbox_id"],),
            )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


# ---- 폰 결제 알림 자동수집 (MacroDroid 등에서 웹훅으로 호출) ----


def _get_or_create_inbox_token(conn):
    token = get_setting(conn, "inbox_token")
    if not token:
        token = secrets.token_hex(4)
        set_setting(conn, "inbox_token", token)
        conn.commit()
    return token


def _inbox_authorized(req, expected_token):
    supplied = req.args.get("token") or req.headers.get("X-Inbox-Token")
    if not supplied:
        supplied = (req.get_json(silent=True) or {}).get("token")
    return supplied == expected_token


@app.route("/api/settings/inbox", methods=["GET"])
def get_inbox_settings():
    conn = get_conn()
    try:
        token = _get_or_create_inbox_token(conn)
    finally:
        conn.close()
    return jsonify({"token": token, "webhook_path": "/api/inbox"})


@app.route("/api/settings/inbox/regenerate", methods=["POST"])
def regenerate_inbox_token():
    conn = get_conn()
    try:
        token = secrets.token_hex(4)
        set_setting(conn, "inbox_token", token)
        conn.commit()
    finally:
        conn.close()
    return jsonify({"token": token, "webhook_path": "/api/inbox"})


def _cancel_inbox_item(conn, target_id):
    """알림 하나를 실제로 취소 처리한다(공용) - 이미 혜택이 배정돼 적립
    기록(usage_logs)까지 있었다면 그 기록도 같이 지운다(안 지우면 취소된
    결제로 받은 적립이 그대로 남아있게 됨). 지운 뒤에는 그 혜택의 다른
    기록들이 그동안 이 기록 때문에 월 한도 계산이 왜곡됐을 수 있어서(예:
    이 기록이 한도를 먼저 차지해서 뒤에 들어온 멀쩡한 결제가 "한도초과"로
    깎였을 수 있음) 다시 계산까지 돌린다."""
    affected_benefit_ids = {
        row["benefit_id"] for row in
        conn.execute("SELECT benefit_id FROM usage_logs WHERE source_inbox_id = ?", (target_id,)).fetchall()
    }
    conn.execute("DELETE FROM usage_logs WHERE source_inbox_id = ?", (target_id,))
    conn.execute(
        "UPDATE inbox_items SET status = 'cancelled', recurring_id = NULL, insurance_id = NULL, "
        "card_id = NULL, benefit_id = NULL WHERE id = ?",
        (target_id,),
    )
    for benefit_id in affected_benefit_ids:
        _recalculate_benefit_logs(conn, benefit_id)


def _try_cancel_by_approval_no(conn, approval_no, exclude_id=None):
    """엑셀 업로드 때 쓰는 _resolve_cancellation과 같은 방식 - 승인번호가
    똑같은(아직 취소 처리 안 된) 알림을 정확히 찾아서 취소 처리한다.
    승인번호가 있으면 이 방법이 금액+카드 뒷자리 추측보다 훨씬 확실하다
    (후보가 여러 개라 추측으로는 아예 못 잡는 경우도 승인번호면 바로
    잡힘)."""
    if not approval_no:
        return False
    target = conn.execute(
        "SELECT id FROM inbox_items WHERE approval_no = ? AND status != 'cancelled' AND deleted_at IS NULL",
        (approval_no,),
    ).fetchone()
    if not target or target["id"] == exclude_id:
        return False
    _cancel_inbox_item(conn, target["id"])
    return True


def _try_cancel_matching_notification(conn, last4, amount, exclude_id=None, approval_no=""):
    """폰 알림으로 "매출취소" 같은 문구가 그대로 들어온 경우, 승인번호가
    있으면(카드사 알림 형식에 따라 찍혀 있을 수 있음) 그걸로 정확히
    매칭하고(_try_cancel_by_approval_no), 승인번호가 없거나 그걸로 못
    찾으면 카드+금액이 똑같은, 아직 취소 처리 안 된 알림이 딱 하나뿐일
    때만 그걸 취소 처리한다(후보가 여러 개면 어느 걸 취소해야 할지
    애매해서 손대지 않고 사용자가 직접 판단하게 둔다 - 엉뚱한 결제를
    잘못 취소시키는 것보다 안전).

    취소는 당일이 아니라 보름/한달 뒤에 들어오는 경우가 흔해서(그 사이에
    이미 혜택 배정까지 끝났을 가능성이 높음), "아직 미처리(pending)"인
    것만 찾으면 거의 못 찾는다. 그래서 상태 상관없이(배정 완료/혜택없음
    처리된 것 포함) 찾는다."""
    if _try_cancel_by_approval_no(conn, approval_no, exclude_id=exclude_id):
        return True
    if not amount:
        return False
    candidates = conn.execute(
        "SELECT id, last4, benefit_id FROM inbox_items WHERE amount = ? AND status != 'cancelled' "
        "AND deleted_at IS NULL",
        (amount,),
    ).fetchall()
    matches = [
        r for r in candidates
        if r["id"] != exclude_id and (not last4 or not r["last4"] or card_codes_match(last4, r["last4"]))
    ]
    if len(matches) != 1:
        return False
    _cancel_inbox_item(conn, matches[0]["id"])
    return True


@app.route("/api/inbox", methods=["POST"])
def create_inbox_item():
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        # 유효한 JSON이 아니거나 "text" 필드가 없으면, 본문 전체를 알림 원문으로
        # 취급한다. MacroDroid 등에서 알림 텍스트를 JSON으로 정확히 감싸서
        # 보내기 번거로운 경우, 그냥 알림 텍스트를 body에 그대로 넣어도 되게
        # 하기 위함(줄바꿈/따옴표 이스케이프를 신경 쓸 필요가 없어짐).
        text = (request.get_data(as_text=True) or "").strip()

    conn = get_conn()
    try:
        expected_token = _get_or_create_inbox_token(conn)
        if not _inbox_authorized(request, expected_token):
            return jsonify({"error": "인증 토큰이 올바르지 않습니다."}), 401

        if not text:
            return jsonify({"error": "text가 비어 있습니다."}), 400

        parsed = parse_notification(text)
        occurred_at = (data.get("occurred_at") or "").strip() or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # MacroDroid 등에서 알림 하나에 트리거가 두 번 발동하는 경우가 있어서,
        # 같은 문구가 60초 안에 다시 들어오면 중복으로 보고 건너뛴다.
        recent_dup = conn.execute(
            """SELECT 1 FROM inbox_items WHERE raw_text = ?
               AND created_at >= datetime('now', 'localtime', '-60 seconds') LIMIT 1""",
            (text,),
        ).fetchone()
        if not recent_dup:
            # "매출취소"/"승인취소"처럼 취소 자체를 알리는 문자는 혜택배정
            # 큐에 멀쩡한 결제인 것처럼 쌓이면 안 된다. 알림 문구에 승인번호가
            # 찍혀 있으면(카드사 알림 형식에 따라 다름) 그걸로 정확히 매칭하고,
            # 없으면 카드+금액이 똑같은 미처리 알림이 하나뿐일 때만 그걸
            # 취소 처리한다.
            is_cancel = is_cancelled_status(text)
            if is_cancel:
                _try_cancel_matching_notification(
                    conn, parsed["last4"], parsed["amount"], approval_no=parsed["approval_no"]
                )
            cur = conn.execute(
                """INSERT INTO inbox_items
                   (raw_text, amount, last4, issuer, merchant, occurred_at, approval_no, source, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'notification', ?)""",
                (text, parsed["amount"], parsed["last4"], parsed["issuer"], parsed["merchant"], occurred_at,
                 parsed["approval_no"], "cancelled" if is_cancel else "pending"),
            )
            if not is_cancel:
                _try_auto_match_all(conn, cur.lastrowid)
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/inbox/<int:item_id>/assign", methods=["POST"])
def assign_inbox_item(item_id):
    data = request.get_json(force=True) or {}
    try:
        benefit_id = int(data.get("benefit_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "혜택을 선택하세요."}), 400

    conn = get_conn()
    try:
        item = conn.execute("SELECT * FROM inbox_items WHERE id = ?", (item_id,)).fetchone()
        if not item or item["status"] != "pending":
            return jsonify({"error": "이미 처리되었거나 존재하지 않는 알림입니다."}), 404

        benefit = conn.execute("SELECT * FROM benefits WHERE id = ?", (benefit_id,)).fetchone()
        if not benefit:
            return jsonify({"error": "혜택을 찾을 수 없습니다."}), 404

        try:
            raw_value = float(data.get("amount") if data.get("amount") not in (None, "") else item["amount"])
        except (TypeError, ValueError):
            raw_value = 0
        if not raw_value:
            return jsonify({"error": "금액을 확인할 수 없습니다. 직접 입력해주세요."}), 400

        used_at = (data.get("used_at") or "").strip() or (item["occurred_at"] or "")[:10]
        try:
            datetime.strptime(used_at, "%Y-%m-%d")
        except ValueError:
            used_at = date.today().isoformat()

        merchant = (data.get("merchant") or item["merchant"] or "").strip()
        memo = (data.get("memo") or "").strip()
        category_override = (data.get("category") or "").strip()

        if benefit["calc_mode"] == "change_under_1000" and _duplicate_merchant_today(conn, benefit_id, used_at, merchant):
            return jsonify({
                "error": f"같은 날 \"{merchant}\"에 이미 기록이 있습니다. "
                         "동일 가맹점은 1일 1회만 적립되므로(가장 먼저 결제한 건만 인정), "
                         "이 결제는 적립 대상이 아닙니다. 알림을 무시 처리해주세요."
            }), 400

        used_value, memo, rate_category, error = _apply_calc_mode(
            conn, benefit_id, benefit, raw_value, bool(data.get("doubled")), memo, merchant, used_at, category_override,
            item["occurred_at"] or "", is_online=bool(item["raw_text"] and "온라인" in item["raw_text"])
        )
        if error:
            return jsonify({"error": error}), 400

        conn.execute(
            """INSERT INTO usage_logs (benefit_id, used_value, used_at, merchant, memo, source_inbox_id, rate_category, raw_value)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (benefit_id, used_value, used_at, merchant, memo, item_id, rate_category, raw_value),
        )
        conn.execute(
            "UPDATE inbox_items SET status = 'assigned', card_id = ?, benefit_id = ? WHERE id = ?",
            (benefit["card_id"], benefit_id, item_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/inbox/<int:item_id>/mark-no-benefit", methods=["POST"])
def mark_no_benefit(item_id):
    """해당하는 혜택이 없는(그러나 실제로 결제는 한) 알림을 "받은 결제 알림"
    목록에서 빼되, 완전히 삭제(=이 알림 무시)하지 않고 별도 목록에 남겨둔다."""
    data = request.get_json(force=True, silent=True) or {}
    card_id = data.get("card_id") or None

    conn = get_conn()
    try:
        item = conn.execute("SELECT * FROM inbox_items WHERE id = ?", (item_id,)).fetchone()
        if not item or item["status"] != "pending":
            return jsonify({"error": "이미 처리되었거나 존재하지 않는 알림입니다."}), 404

        conn.execute(
            "UPDATE inbox_items SET status = 'no_benefit', card_id = ?, benefit_id = NULL WHERE id = ?",
            (card_id, item_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/inbox/<int:item_id>/reopen", methods=["POST"])
def reopen_inbox_item(item_id):
    """"혜택 없음"으로 처리했던 알림을 다시 "받은 결제 알림" 목록으로 되돌린다."""
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE inbox_items SET status = 'pending', card_id = NULL, benefit_id = NULL WHERE id = ?",
            (item_id,),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/inbox/<int:item_id>", methods=["DELETE"])
def discard_inbox_item(item_id):
    """완전삭제 대신 휴지통으로 보낸다(deleted_at만 찍음) - "복원" 버튼으로
    언제든 되돌릴 수 있게 실제 행은 지우지 않는다."""
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE inbox_items SET deleted_at = datetime('now', 'localtime') WHERE id = ?",
            (item_id,),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/inbox/<int:item_id>/restore", methods=["POST"])
def restore_inbox_item(item_id):
    """휴지통에서 복원 - deleted_at을 지우면 status는 그대로 보존돼 있었으니
    큐든 혜택없음 처리 내역이든 원래 있던 자리로 그대로 돌아간다."""
    conn = get_conn()
    try:
        conn.execute("UPDATE inbox_items SET deleted_at = NULL WHERE id = ?", (item_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/inbox/<int:item_id>/resolve-cancellation", methods=["POST"])
def resolve_cancellation_notice(item_id):
    """"매출취소" 알림이 코드 업데이트 전에 이미 큐에 들어가버린 경우(또는
    자동 처리 시점엔 후보가 여러 개라 건너뛰었던 경우)를 위해, 수동으로
    다시 시도할 수 있게 한다. _try_cancel_matching_notification과 똑같은
    로직을 쓰되, 이 알림 자신은 후보에서 빼고 찾는다. 원결제를 찾았든
    못 찾았든 이 알림 자체는 취소 처리해서 큐에서 뺀다(사용자가 직접
    "이건 취소 문자다"라고 확인한 거라서)."""
    conn = get_conn()
    try:
        item = conn.execute("SELECT * FROM inbox_items WHERE id = ?", (item_id,)).fetchone()
        if not item:
            return jsonify({"error": "알림을 찾을 수 없습니다."}), 404
        matched = _try_cancel_matching_notification(
            conn, item["last4"], item["amount"], exclude_id=item_id, approval_no=item["approval_no"]
        )
        conn.execute(
            "UPDATE inbox_items SET status = 'cancelled', recurring_id = NULL, insurance_id = NULL, "
            "card_id = NULL, benefit_id = NULL WHERE id = ?",
            (item_id,),
        )
        conn.commit()
    finally:
        conn.close()
    result = serialize_full()
    result["cancellation_matched"] = matched
    return jsonify(result)


@app.route("/api/inbox/<int:item_id>/dup-ignore", methods=["POST"])
def dismiss_suspected_duplicate(item_id):
    """"중복 의심" 묶음에서 "중복 아님" 처리 - 이 알림은 앞으로 중복 의심
    목록 계산에서 빼준다(실제로는 같은 날 같은 가맹점에 금액이 비슷한
    별개의 결제였던 경우를 위함)."""
    conn = get_conn()
    try:
        conn.execute("UPDATE inbox_items SET dup_ignored = 1 WHERE id = ?", (item_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/inbox/bulk-no-benefit", methods=["POST"])
def bulk_mark_no_benefit():
    """받은 알림 리스트에서 여러 개를 한 번에 "혜택 없음" 처리한다(완전 삭제와
    달리 "혜택없음 처리 내역"에 남아서 전월실적 자동집계엔 그대로 들어감)."""
    data = request.get_json(force=True) or {}
    ids = data.get("ids") or []
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return jsonify({"error": "처리할 항목을 선택하세요."}), 400
    if not ids:
        return jsonify({"error": "처리할 항목을 선택하세요."}), 400

    conn = get_conn()
    try:
        # card_id를 안 채워주면 전월실적 자동집계(_auto_spend, card_id가 있는
        # 'assigned'/'no_benefit' 건만 합산)에서 이 건들이 전부 빠진다 - last4로
        # 카드를 찾을 수 있으면 같이 채워준다(단일 건 처리인 mark_no_benefit은
        # 모달에서 이미 고른 카드를 받아오지만, 여기는 모달 없이 여러 카드가
        # 섞인 채로 선택될 수 있어서 항목마다 last4로 직접 찾아야 함).
        cards_with_code = conn.execute(
            "SELECT id, last4 FROM cards WHERE last4 != '' AND closed = 0"
        ).fetchall()
        placeholders = ",".join("?" for _ in ids)
        items = conn.execute(
            f"SELECT id, last4, card_id FROM inbox_items WHERE id IN ({placeholders}) AND status = 'pending'",
            ids,
        ).fetchall()
        for item in items:
            card_id = item["card_id"]
            if not card_id and item["last4"]:
                for c in cards_with_code:
                    if card_codes_match(c["last4"], item["last4"]):
                        card_id = c["id"]
                        break
            conn.execute(
                "UPDATE inbox_items SET status = 'no_benefit', benefit_id = NULL, card_id = ? WHERE id = ?",
                (card_id, item["id"]),
            )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/inbox/bulk-delete", methods=["POST"])
def bulk_delete_inbox_items():
    """받은 알림/혜택없음 처리 내역 리스트에서 여러 개를 한 번에 휴지통으로
    보낸다(완전삭제 아님 - deleted_at만 찍고 실제 행은 남긴다). 과거
    카드내역을 몇 년치 업로드했을 때 필요없는 알림을 한 번에 치우는 용도."""
    data = request.get_json(force=True) or {}
    ids = data.get("ids") or []
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return jsonify({"error": "삭제할 항목을 선택하세요."}), 400
    if not ids:
        return jsonify({"error": "삭제할 항목을 선택하세요."}), 400

    conn = get_conn()
    try:
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE inbox_items SET deleted_at = datetime('now', 'localtime') WHERE id IN ({placeholders})",
            ids,
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


@app.route("/api/inbox/bulk-restore", methods=["POST"])
def bulk_restore_inbox_items():
    """휴지통에서 여러 개를 한 번에 복원한다."""
    data = request.get_json(force=True) or {}
    ids = data.get("ids") or []
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return jsonify({"error": "복원할 항목을 선택하세요."}), 400
    if not ids:
        return jsonify({"error": "복원할 항목을 선택하세요."}), 400

    conn = get_conn()
    try:
        placeholders = ",".join("?" for _ in ids)
        conn.execute(f"UPDATE inbox_items SET deleted_at = NULL WHERE id IN ({placeholders})", ids)
        conn.commit()
    finally:
        conn.close()
    return jsonify(serialize_full())


# ---- 지난/누락 내역 백업(엑셀 다운로드) · 일괄 업로드 ----


def _xlsx_response(wb, filename):
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=filename, mimetype=XLSX_MIME)


def _set_column_widths(ws, widths):
    for col, width in zip("ABCDEFGH", widths):
        ws.column_dimensions[col].width = width


def _card_diag_lines(conn, card):
    """카드 한 장에 대한 실적 진단 텍스트(줄 리스트)를 만든다 - 단일 카드용
    라우트와 전체 카드용 라우트가 같이 쓴다. _auto_spend와 완전히 똑같은
    조건으로 "이번 달 실적에 잡힌 알림"을 전부 한 줄씩 나열하고 합계를
    보여줘서, 어느 알림이 실적엔 잡히는데 혜택별 목록(결제금액)에는 안
    보이는지(예: 사이클 기준 vs 달력 기준 경계 차이, 배정된 혜택이 삭제된
    경우 등) 바로 알 수 있게 한다."""
    card_id = card["id"]
    ref_date = date.today()
    this_month = current_year_month(ref_date)
    prev_month = previous_year_month(ref_date)
    cyc_start, cyc_end = cycle_bounds(card["reset_day"], ref_date)

    def spend_rows(year_month):
        base = """SELECT inbox_items.id, inbox_items.amount, inbox_items.merchant,
                      inbox_items.occurred_at, inbox_items.status, inbox_items.benefit_id,
                      inbox_items.recurring_id, inbox_items.insurance_id,
                      recurring_payments.name AS recurring_name,
                      recurring_payments.card_id AS recurring_card_id,
                      recurring_payments.payment_method AS recurring_payment_method,
                      insurances.company AS insurance_company,
                      insurances.card_id AS insurance_card_id,
                      insurances.payment_method AS insurance_payment_method,
                      benefits.name AS benefit_name
               FROM inbox_items
               LEFT JOIN recurring_payments ON recurring_payments.id = inbox_items.recurring_id
               LEFT JOIN insurances ON insurances.id = inbox_items.insurance_id
               LEFT JOIN benefits ON benefits.id = inbox_items.benefit_id
               WHERE substr(inbox_items.occurred_at, 1, 7) = ?
               AND inbox_items.deleted_at IS NULL"""
        # 1) card_id/상태로 직접 잡히는 것 + recurring/insurance가 card_id FK로
        # 바로 연결된 것(_auto_spend의 1차 쿼리와 동일).
        direct = conn.execute(
            base + """
               AND (
                   (inbox_items.recurring_id IS NULL AND inbox_items.insurance_id IS NULL
                    AND inbox_items.card_id = ? AND inbox_items.status IN ('assigned', 'no_benefit'))
                   OR (inbox_items.recurring_id IS NOT NULL AND recurring_payments.card_id = ?)
                   OR (inbox_items.insurance_id IS NOT NULL AND insurances.card_id = ?)
               )
               ORDER BY inbox_items.occurred_at""",
            (year_month, card_id, card_id, card_id),
        ).fetchall()
        # 2) card_id FK가 비어서(결제수단을 텍스트로 직접 입력해둔 옛날 등록분)
        # 카드 뒷자리로 보조 매칭하는 것 - SQL 함수가 아니라 파이썬에서 직접
        # card_codes_match를 돌려야 해서 _auto_spend처럼 따로 조회한다.
        text_candidates = conn.execute(
            base + """
               AND (
                   (inbox_items.recurring_id IS NOT NULL AND recurring_payments.card_id IS NULL)
                   OR (inbox_items.insurance_id IS NOT NULL AND insurances.card_id IS NULL)
               )
               ORDER BY inbox_items.occurred_at""",
            (year_month,),
        ).fetchall()
        text_matched = [
            r for r in text_candidates
            if card["last4"] and card_codes_match(
                _extract_method_last4(r["recurring_payment_method"] or r["insurance_payment_method"]),
                card["last4"],
            )
        ]
        return list(direct) + text_matched

    lines = []
    lines.append(f"카드: {card['name']} (id={card_id}, last4={card['last4']})")
    lines.append(f"결제일(reset_day): {card['reset_day']}")
    lines.append(f"이번 결제 사이클(혜택별 '결제금액' 목록 기준): {cyc_start} ~ {cyc_end}")
    lines.append(f"실적 제외 키워드: {card['perf_excluded_keywords'] or DEFAULT_PERFORMANCE_EXCLUDED_KEYWORDS}")
    lines.append("")

    for label, ym in (("당월(이번 달, 달력 기준)", this_month), ("전월", prev_month)):
        rows = spend_rows(ym)
        lines.append(f"=== {label} {ym} - 실적에 잡히는 알림 목록 ===")
        total = 0
        for r in rows:
            excluded = is_performance_excluded(r["merchant"], card["perf_excluded_keywords"])
            link = "직접배정" if (not r["recurring_id"] and not r["insurance_id"]) else (
                f"고정지출:{r['recurring_name']}" if r["recurring_id"] else f"보험:{r['insurance_company']}"
            )
            benefit_part = f", 혜택:{r['benefit_name']}" if r["benefit_name"] else (
                ", 혜택:(삭제됨 또는 없음)" if r["benefit_id"] else ""
            )
            mark = "(실적 제외 키워드에 걸려서 합계에선 빠짐)" if excluded else ""
            lines.append(
                f"  {r['occurred_at']} · {r['merchant']} · {r['amount']:,.0f}원 "
                f"· status={r['status']} · 연결={link}{benefit_part} {mark}"
            )
            if not excluded:
                total += r["amount"] or 0
        lines.append(f"  -> 합계: {total:,.0f}원 (건수 {len(rows)})")
        lines.append("")

    benefits = conn.execute(
        "SELECT * FROM benefits WHERE card_id = ? ORDER BY sort_order ASC, id ASC", (card_id,)
    ).fetchall()
    lines.append("=== 혜택별 목록(화면에 보이는 '결제금액')에 잡히는 사이클 기준 사용내역 합계 ===")
    for b in benefits:
        used = conn.execute(
            "SELECT COALESCE(SUM(raw_value), 0) AS total FROM usage_logs WHERE benefit_id = ? "
            "AND used_at >= ? AND used_at < ?",
            (b["id"], cyc_start.isoformat(), cyc_end.isoformat()),
        ).fetchone()["total"]
        lines.append(f"  {b['name']}: {used:,.0f}원")
    lines.append("")

    # "설명 보기"에 적힌 안내 문구(예: "육아 5%: 학원·서점·병원·의원·약국")는
    # 그냥 사람이 따로 써둔 memo 글자라서, 실제 rate_table JSON에 등록된
    # 업종별 키워드와 다를 수 있다 - 둘이 안 맞으면 설명과 다르게 매칭돼서
    # "왜 분명 적혀있는데 저 퍼센트로 안 잡히냐"는 혼란이 생긴다. 실제로
    # 매칭에 쓰이는 원본 키워드를 그대로 보여주고, 샘플 가맹점명 하나를
    # 돌려서 실제로 몇 %/어느 업종으로 잡히는지까지 바로 확인시킨다.
    lines.append("=== 업종별(rate_table) 혜택의 실제 등록 키워드 ===")
    percent_benefits = [b for b in benefits if b["calc_mode"] == "percent_discount" and b["rate_table"]]
    if not percent_benefits:
        lines.append("  (업종별 혜택 없음)")
    for b in percent_benefits:
        lines.append(f"  혜택: {b['name']}")
        try:
            rows = json.loads(b["rate_table"] or "[]")
        except (ValueError, TypeError):
            rows = []
        if not isinstance(rows, list) or not rows:
            lines.append("    (rate_table이 비어있거나 형식이 깨짐)")
            continue
        for entry in rows:
            if not isinstance(entry, (list, tuple)) or not entry:
                continue
            lines.append(f"    {entry}")
        sample_merchant = "의료법인일산복음의료"
        result = match_rate_table(sample_merchant, b["rate_table"])
        lines.append(
            f"    -> 샘플 가맹점명 \"{sample_merchant}\"로 테스트: "
            f"퍼센트={result[0]}, 업종라벨={result[5] or '(매칭 안 됨 - 기본값 적용)'}"
        )

    return lines


@app.route("/api/export/card-diag/<int:card_id>.txt")
def export_card_diag(card_id):
    """카드 한 장의 실적 진단 파일 - 버튼 한 번으로 바로 다운로드."""
    conn = get_conn()
    try:
        card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not card:
            return "카드를 찾을 수 없습니다.", 404
        text = "\n".join(_card_diag_lines(conn, card))
    finally:
        conn.close()

    resp = make_response(text)
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="card_{card_id}_diag.txt"'
    return resp


@app.route("/api/export/all-cards-diag.txt")
def export_all_cards_diag():
    """카드마다 하나씩 따로 받으러 다니지 않게, 등록된 카드 전부를 한
    파일에 순서대로 이어붙여서 한 번에 받는 버전."""
    conn = get_conn()
    try:
        cards = conn.execute("SELECT * FROM cards WHERE closed = 0 ORDER BY sort_order ASC, id ASC").fetchall()
        blocks = []
        for card in cards:
            blocks.append("\n".join(_card_diag_lines(conn, card)))
        text = ("\n" + "=" * 60 + "\n\n").join(blocks)
    finally:
        conn.close()

    resp = make_response(text)
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"
    resp.headers["Content-Disposition"] = 'attachment; filename="all_cards_diag.txt"'
    return resp


def _build_loan_diag_text():
    """"왜 이 금액이 이 대출로 잡혔냐"를 캡처 주고받으며 하나씩 확인하는
    대신, 버튼 한 번으로 등록된 대출 전부의 매칭 조건(월상환액/결제일/
    출금계좌)이랑 최근 상환 내역을 한 파일로 바로 받게 한다. loan_payments
    엔 원본 알림을 직접 가리키는 FK가 없어서(상환 처리될 때 inbox_items는
    그냥 status만 'no_benefit'으로 바뀌고 끝), 같은 날짜+금액인 inbox_items를
    찾아 원본 알림 문구도 최대한 같이 보여준다(여러 건이면 전부 나열 -
    어느 게 진짜인지 추측 안 함)."""
    conn = get_conn()
    try:
        diag_lines = ["[증빙파일 자동매칭 진단]"]
        for pkg in ("pypdf", "fitz", "easyocr", "PIL", "numpy"):
            try:
                __import__(pkg)
                diag_lines.append(f"  {pkg}: 설치됨")
            except BaseException as e:
                _reraise_if_fatal(e)
                diag_lines.append(f"  {pkg}: 설치 안 됨/실패 - {e}")
        loans = conn.execute("SELECT * FROM loans ORDER BY sort_order ASC, id ASC").fetchall()
        attachments = conn.execute(
            "SELECT id, loan_id, file_name, original_name, extracted_text FROM loan_attachments ORDER BY id"
        ).fetchall()
        for a in attachments:
            path = os.path.join(UPLOAD_DIR, a["file_name"])
            diag_lines.append(f"  첨부 id={a['id']} 파일={a['original_name']} 배정된 대출id={a['loan_id']}")
            try:
                with open(path, "rb") as f:
                    file_bytes = f.read()
                text = _get_or_extract_attachment_text(
                    conn, a["id"], a["extracted_text"], a["original_name"] or a["file_name"], file_bytes
                )
                if not text:
                    diag_lines.append("    -> 글자를 하나도 못 뽑음(추출 실패)")
                else:
                    # 150자였을 땐 "대출과목 계좌번호 이율..." 표 헤더
                    # 직후, 실제 계좌번호 숫자가 나오기도 전에 잘려서
                    # 왜 매칭이 안 되는지 미리보기만으론 알 수가 없었다.
                    preview = text.replace("\n", " / ")[:1000]
                    diag_lines.append(f"    -> 뽑은 글자 미리보기: {preview}")
                    matched = _match_loans_for_attachment_text(loans, text)
                    matched_names = ", ".join(l["name"] for l in matched) or "(없음)"
                    diag_lines.append(f"    -> 지금 기준으로 매칭되는 대출: {matched_names}")
            except BaseException as e:
                _reraise_if_fatal(e)
                diag_lines.append(f"    -> 처리 중 오류: {e}")
        diag_lines.append("")

        blocks = []
        for loan in loans:
            lines = []
            lines.append(f"대출: {loan['name']} (id={loan['id']})")
            lines.append(f"상환방식: {loan['repayment_type'] or '(미입력)'}")
            lines.append(f"월상환액(등록값, 금액+날짜 느슨한 매칭에 쓰임): {loan['monthly_payment']:,.0f}원")
            lines.append(f"결제일(등록값, 이 날짜 ±3일 안이어야 느슨한 매칭 대상): {loan['payment_day']}일")
            lines.append(f"출금계좌(등록값, 알림 문구에 이 숫자가 그대로 있으면 금액 상관없이 매칭): {loan['withdrawal_account'] or '(미입력)'}")
            lines.append(f"메모(증빙파일 자동매칭이 계좌번호를 여기서도 찾음): {loan['memo'] or '(비어있음)'}")
            lines.append("최근 상환 내역(최대 10건) 및 그 날짜+금액으로 찾은 원본 알림 후보:")
            payments = conn.execute(
                "SELECT * FROM loan_payments WHERE loan_id = ? ORDER BY paid_at DESC, id DESC LIMIT 10",
                (loan["id"],),
            ).fetchall()
            if not payments:
                lines.append("  (상환 내역 없음)")
            for p in payments:
                lines.append(f"  {p['paid_at']} · {p['amount']:,.0f}원 · {p['memo']} · 상환후잔액 {p['balance_after']:,.0f}원")
                candidates = conn.execute(
                    "SELECT raw_text, occurred_at FROM inbox_items "
                    "WHERE amount = ? AND substr(occurred_at, 1, 10) = ? ORDER BY id DESC LIMIT 5",
                    (p["amount"], p["paid_at"]),
                ).fetchall()
                if not candidates:
                    lines.append("    (같은 날짜+금액의 원본 알림을 못 찾음 - 알림이 이미 지워졌을 수 있음)")
                for c in candidates:
                    raw_preview = (c["raw_text"] or "").replace("\n", " / ")[:200]
                    lines.append(f"    후보 알림({c['occurred_at']}): {raw_preview}")
            blocks.append("\n".join(lines))
        text = "\n".join(diag_lines) + "\n" + ("\n" + "=" * 60 + "\n\n").join(blocks)
    finally:
        conn.close()
    return text


@app.route("/api/export/loan-diag.txt")
def export_loan_diag():
    resp = make_response(_build_loan_diag_text())
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"
    resp.headers["Content-Disposition"] = 'attachment; filename="loan_diag.txt"'
    return resp


@app.route("/api/loans/diag")
def loan_diag_json():
    """위와 같은 내용을 JSON으로 돌려준다 - 화면 안 "매칭 진단" 모달이 이걸
    쓴다. launcher.py의 _ensure_tailscale_https 주석에 이미 적혀있듯, 일부
    브라우저(특히 Tailscale의 http://100.x.x.x 같은 암호화 안 된 주소)는
    .txt 확장자나 파일 다운로드로 보이는 응답 자체를 fetch 단계에서부터
    차단해버린다(실제로 "TypeError: Failed to fetch"로 재현됨, 위
    export_loan_diag의 Content-Disposition을 떼어내도 여전히 실패했음 -
    .txt 확장자가 붙은 경로 자체를 보고 차단하는 것으로 보임). 확장자도
    없고 attachment 헤더도 없는 평범한 JSON API로 내려주면 이 차단을
    피할 수 있다."""
    return jsonify({"text": _build_loan_diag_text()})


@app.route("/api/export/usage.xlsx")
def export_usage():
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT c.name AS card_name, b.name AS benefit_name, l.used_at, l.used_value, l.memo
               FROM usage_logs l
               JOIN benefits b ON b.id = l.benefit_id
               JOIN cards c ON c.id = b.card_id
               ORDER BY l.used_at DESC, l.id DESC"""
        ).fetchall()
    finally:
        conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "사용내역"
    ws.append(["카드명", "혜택명", "날짜", "금액(또는 횟수)", "메모"])
    for r in rows:
        ws.append([r["card_name"], r["benefit_name"], r["used_at"], r["used_value"], r["memo"]])
    _set_column_widths(ws, [18, 22, 12, 16, 26])

    return _xlsx_response(wb, "카드혜택_사용내역.xlsx")


@app.route("/api/export/template.xlsx")
def export_template():
    conn = get_conn()
    try:
        combos = conn.execute(
            """SELECT c.name AS card_name, b.name AS benefit_name
               FROM benefits b JOIN cards c ON c.id = b.card_id
               ORDER BY c.sort_order, b.sort_order"""
        ).fetchall()
    finally:
        conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "업로드양식"
    ws.append(["카드명", "혜택명", "날짜(YYYY-MM-DD)", "금액(또는 횟수)", "메모(선택)"])
    for c in combos:
        ws.append([c["card_name"], c["benefit_name"], "", "", ""])
    _set_column_widths(ws, [18, 22, 18, 16, 26])

    return _xlsx_response(wb, "카드혜택_업로드양식.xlsx")


def _import_own_template(ws):
    """이 앱이 만든 "빈 양식"(카드명/혜택명 헤더)을 그대로 채워 올린 경우.

    카드명·혜택명이 정확히 일치하는 행은 바로 usage_logs로 들어간다(혜택이
    이미 정해져 있으므로 사람이 다시 고를 필요가 없음).
    """
    conn = get_conn()
    try:
        benefit_lookup = {}
        for row in conn.execute(
            """SELECT b.id AS benefit_id, c.name AS card_name, b.name AS benefit_name
               FROM benefits b JOIN cards c ON c.id = b.card_id"""
        ):
            benefit_lookup[(row["card_name"].strip(), row["benefit_name"].strip())] = row["benefit_id"]

        # 같은 양식을 다시 올려도 중복으로 안 쌓이도록, 이미 있는 (혜택, 날짜,
        # 금액, 메모) 조합을 미리 읽어둔다.
        seen_combo = {
            (r["benefit_id"], r["used_at"], r["used_value"], r["memo"] or "")
            for r in conn.execute("SELECT benefit_id, used_at, used_value, memo FROM usage_logs")
        }

        added = 0
        skipped = []
        for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if row is None or all(v in (None, "") for v in row):
                continue
            card_name, benefit_name, used_at, used_value = (list(row) + [None] * 4)[:4]
            memo = row[4] if len(row) > 4 else None
            memo_str = str(memo).strip() if memo else ""

            key = ((str(card_name).strip() if card_name else ""), (str(benefit_name).strip() if benefit_name else ""))
            if key not in benefit_lookup:
                skipped.append(f"{i}행: 카드/혜택 이름을 찾을 수 없음 ({key[0]} / {key[1]})")
                continue

            used_value = parse_amount_cell(used_value)
            if not used_value:
                skipped.append(f"{i}행: 금액(또는 횟수)이 올바르지 않음")
                continue

            used_at_str = parse_date_cell(used_at)
            if not used_at_str:
                skipped.append(f"{i}행: 날짜가 올바르지 않음 (YYYY-MM-DD)")
                continue

            benefit_id = benefit_lookup[key]
            combo = (benefit_id, used_at_str, used_value, memo_str)
            if combo in seen_combo:
                skipped.append(f"{i}행: 같은 혜택·날짜·금액 내역이 이미 있어 중복으로 보고 건너뜀")
                continue
            seen_combo.add(combo)

            conn.execute(
                "INSERT INTO usage_logs (benefit_id, used_value, used_at, memo, raw_value) VALUES (?, ?, ?, ?, ?)",
                (benefit_id, used_value, used_at_str, memo_str, used_value),
            )
            added += 1
        conn.commit()
    finally:
        conn.close()

    result = serialize_full()
    result["import_result"] = {"mode": "template", "added": added, "skipped": skipped}
    return jsonify(result)


def _import_statement(ws, header_info):
    """카드사에서 그대로 다운로드한 엑셀(이용일자/승인금액 등)을 인식해서
    "받은 결제 알림"(inbox_items) 목록에 대량으로 쌓는다.

    이 표에는 어떤 혜택인지에 대한 정보가 없으므로, 자동수집 알림과 똑같이
    사람이 앱에서 "혜택 선택"으로 하나씩 배정해야 한다.
    """
    date_col = header_info["date_col"]
    amount_col = header_info["amount_col"]
    merchant_col = header_info["merchant_col"]
    cardno_col = header_info["cardno_col"]
    approval_col = header_info.get("approval_col")
    usage_type_col = header_info.get("usage_type_col")

    conn = get_conn()
    try:
        # 엑셀에 있는 행은 원칙적으로 다 DB에 넣는다(취소거래/혜택 미등록
        # 카드라고 여기서 걸러서 빼지 않는다) - 화면에 뭘 보여줄지는
        # serialize_inbox 등 조회 쪽에서 결정한다. 다만 금액·날짜 자체를
        # 못 읽은 행은 저장할 최소한의 정보가 없어서 어쩔 수 없이 건너뛴다.

        # 중복 판단: 승인번호가 있으면 승인번호로 우선 확인하고(단, 승인번호가
        # 서로 다르면 별개 결제로 본다), 그 다음 금액이 같은 기존 기록들과
        # 카드번호·시간을 비교한다. 카드번호는 폰 알림은 4자리("8390"),
        # 카드사 엑셀은 "본인839*"처럼 3자리("839")로 마스킹돼 나오는 등
        # 자릿수가 다를 수 있어서 card_codes_match(접두 비교)로 확인한다.
        # 시간은 양쪽 다 시간(분초)까지 있으면 5분 이내를 같은 결제로 보고
        # (폰 알림 수신 시각과 카드사 승인 시각이 몇 분 정도는 어긋날 수
        # 있어서), 한쪽이라도 시간이 없으면 날짜만 비교한다. 이렇게 해야
        # 폰 알림으로 이미 들어온 것과 나중에 엑셀로 또 들어오는 같은 결제를,
        # 승인번호·카드번호 자릿수가 다르더라도 서로 잡아낼 수 있다.
        seen_approval = set()
        existing_by_amount = defaultdict(list)
        for r in conn.execute("SELECT id, last4, amount, occurred_at, approval_no FROM inbox_items"):
            if r["approval_no"]:
                seen_approval.add(r["approval_no"])
            existing_by_amount[r["amount"]].append(
                [r["id"], r["last4"] or "", r["occurred_at"] or "", r["approval_no"] or ""]
            )

        def _last4_compatible(a, b):
            # 카드번호가 한쪽에라도 없으면(예: 일부 카드사 알림은 마스킹이
            # "(0*0*)"처럼 실제 숫자가 하나도 안 보이는 형식이라 뒷자리를 아예
            # 못 뽑는 경우가 있음) 카드로는 비교할 방법이 없으니, 다른 조건
            # (금액·시간)만으로 판단하게 "맞을 수도 있다"로 통과시킨다. 여기서
            # 막혀버리면 같은 결제가 알림으로 먼저 들어오고 나중에 엑셀로 또
            # 들어왔을 때(또는 그 반대) 중복으로 못 잡아서 그대로 두 번 쌓인다.
            if not a or not b:
                return True
            return card_codes_match(a, b)

        def _parse_ts(s):
            if not s:
                return None
            try:
                return datetime.strptime(s, "%Y-%m-%d %H:%M:%S") if len(s) > 10 else datetime.strptime(s, "%Y-%m-%d")
            except ValueError:
                return None

        def _find_duplicate(last4, amount, occurred_at, approval_no):
            """중복이면 (안내 문구, 시간을 보강해줄 기존 inbox_items.id 또는
            None)을 돌려준다. 기존 기록엔 날짜만 있고(시간 없음) 이번에 올리는
            행엔 시간까지 있으면, 새 행을 또 insert하는 대신(중복 행이 두 개
            생기면 안 되므로) 기존 기록의 occurred_at을 그 시간으로 보강한다 -
            그래야 나중에 같은 날짜에 여러 건이 몰려도 실제 결제 순서를
            알 수 있다(시간 없는 파일을 먼저 올리고, 시간 있는 파일을 나중에
            다시 올리는 경우를 위한 것)."""
            if approval_no and approval_no in seen_approval:
                return f"승인번호({approval_no})가 이미 등록되어 있어 중복으로 보고 건너뜀", None
            new_ts = _parse_ts(occurred_at)
            for old_id, old_last4, old_occurred_at, old_approval in existing_by_amount.get(amount, []):
                if not _last4_compatible(last4, old_last4):
                    continue
                if approval_no and old_approval and approval_no != old_approval:
                    continue
                if len(occurred_at) > 10 and len(old_occurred_at) > 10:
                    old_ts = _parse_ts(old_occurred_at)
                    if new_ts and old_ts and abs((new_ts - old_ts).total_seconds()) <= 300:
                        return "같은 카드·금액·비슷한 시간 내역이 이미 있어 중복으로 보고 건너뜀", None
                elif occurred_at[:10] == old_occurred_at[:10]:
                    if len(occurred_at) > 10 and len(old_occurred_at) <= 10:
                        return (
                            "같은 카드·금액·날짜 내역이 이미 있어 중복으로 보고 건너뜀"
                            "(기존 기록에 시간 정보 보강함)",
                            old_id,
                        )
                    return "같은 카드·금액·날짜 내역이 이미 있어 중복으로 보고 건너뜀", None
            return None, None

        # 취소 거래는 카드사 엑셀에 원 결제 건과 같은 승인번호로 "취소" 행이
        # 따로 한 줄 더 찍혀 나온다(금액은 음수로, 비고엔 "취소"로). 이걸 그냥
        # "승인번호 중복"으로 보고 버리면, 정작 취소된 원 결제가 혜택배정
        # 큐에 멀쩡한 결제인 것처럼 그대로 남아버린다. 그래서 취소로 보이는
        # 행이 들어오면 먼저 같은 승인번호의 원 결제를 찾아서 그 행을
        # status='cancelled'로 바꾸고(새 행은 또 만들지 않음), 취소가 아닐
        # 때만 원래의 "중복인지" 판단으로 넘어간다.
        approval_no_to_id = {}

        def _resolve_cancellation(approval_no):
            target_id = approval_no_to_id.get(approval_no)
            if target_id is None:
                row = conn.execute(
                    "SELECT id FROM inbox_items WHERE approval_no = ? AND status != 'cancelled' "
                    "ORDER BY id DESC LIMIT 1",
                    (approval_no,),
                ).fetchone()
                target_id = row["id"] if row else None
            if target_id is None:
                return False
            # 취소된 결제는 고정지출/보험 "이번 달 결제 확인"에서도 당연히
            # 빠져야 하므로 매칭도 같이 풀어준다.
            conn.execute(
                "UPDATE inbox_items SET status = 'cancelled', recurring_id = NULL, insurance_id = NULL WHERE id = ?",
                (target_id,),
            )
            return True

        queued = 0
        cancelled = 0
        cancel_matched = 0
        skipped = []
        for i, row in enumerate(
            ws.iter_rows(min_row=header_info["header_row"] + 1, values_only=True),
            start=header_info["header_row"] + 1,
        ):
            if row is None or all(v in (None, "") for v in row):
                continue

            amount = parse_amount_cell(row[amount_col] if amount_col < len(row) else None)
            if amount is None or amount == 0:
                skipped.append(f"{i}행: 금액을 확인할 수 없음")
                continue
            is_refund = amount < 0
            amount = abs(amount)

            used_at = parse_date_cell(row[date_col] if date_col < len(row) else None)
            if not used_at:
                skipped.append(f"{i}행: 날짜를 확인할 수 없음")
                continue
            occurred_at = parse_datetime_cell(row[date_col] if date_col < len(row) else None) or used_at

            merchant = ""
            if merchant_col is not None and merchant_col < len(row) and row[merchant_col] is not None:
                merchant = annotate_merchant(str(row[merchant_col]).strip())

            last4 = ""
            if cardno_col is not None and cardno_col < len(row):
                last4 = extract_last4(row[cardno_col])

            approval_no = ""
            if approval_col is not None and approval_col < len(row) and row[approval_col] not in (None, ""):
                approval_no = str(row[approval_col]).strip()

            usage_type = ""
            if usage_type_col is not None and usage_type_col < len(row) and row[usage_type_col] not in (None, ""):
                usage_type = str(row[usage_type_col]).strip()

            status_col = header_info.get("status_col")
            is_cancelled = is_refund or (
                status_col is not None and status_col < len(row) and is_cancelled_status(row[status_col])
            )

            if is_cancelled and approval_no and _resolve_cancellation(approval_no):
                skipped.append(f"{i}행: 취소 확인됨 - 같은 승인번호({approval_no})의 원 결제 내역을 취소 처리함")
                cancel_matched += 1
                continue

            dup_reason, backfill_id = _find_duplicate(last4, amount, occurred_at, approval_no)
            if dup_reason:
                if backfill_id:
                    conn.execute(
                        "UPDATE inbox_items SET occurred_at = ? WHERE id = ?", (occurred_at, backfill_id)
                    )
                    for entry in existing_by_amount.get(amount, []):
                        if entry[0] == backfill_id:
                            entry[2] = occurred_at
                            break
                skipped.append(f"{i}행: {dup_reason}")
                continue
            if approval_no:
                seen_approval.add(approval_no)
            existing_by_amount[amount].append([last4, occurred_at, approval_no])

            raw_parts = [p for p in [merchant, f"{amount:,.0f}원", used_at] if p]
            raw_text = " · ".join(raw_parts)

            cur = conn.execute(
                """INSERT INTO inbox_items
                   (raw_text, amount, last4, issuer, merchant, occurred_at, approval_no, usage_type, source, status)
                   VALUES (?, ?, ?, '', ?, ?, ?, ?, 'excel', ?)""",
                (raw_text, amount, last4, merchant, occurred_at, approval_no, usage_type,
                 "cancelled" if is_cancelled else "pending"),
            )
            if approval_no:
                approval_no_to_id[approval_no] = cur.lastrowid
            if is_cancelled:
                cancelled += 1
            else:
                queued += 1
                # 폰 알림으로 실시간으로 들어올 때와 똑같이, 엑셀로 한번에
                # 올린 지난 내역도 여기서 바로 대출/고정지출/보험 자동매칭을
                # 시도한다(전에는 대출 매칭이 빠져있었음). 몇 달치를 한 번에
                # 올려도 각 건이 해당하는 달에 바로 매칭되게 하려는 것.
                _try_auto_match_all(conn, cur.lastrowid)
        conn.commit()
    finally:
        conn.close()

    if cancelled:
        skipped.append(f"(참고) 취소/환불로 보이는 {cancelled}건은 DB에는 저장했지만 목록에는 안 띄웠습니다.")
    if cancel_matched:
        skipped.append(f"(참고) 같은 승인번호의 원 결제 {cancel_matched}건을 취소 처리했습니다.")

    result = serialize_full()
    result["import_result"] = {"mode": "statement", "queued": queued, "skipped": skipped}
    return jsonify(result)


def _repair_xlsx_styles(data):
    """일부 카드사(삼성카드 등) 엑셀은 xl/styles.xml에 OOXML 스펙에 없는
    오탈자 속성(applyNumberForm, 원래는 applyNumberFormat)이 들어있어서
    openpyxl이 아예 못 읽는 경우가 있다. 셀 값 자체는 멀쩡하므로 그 오탈자만
    고쳐서 다시 압축해 돌려준다. 이 문제가 아니면(styles.xml이 없거나 그
    속성이 없으면) None을 돌려줘서, 호출한 쪽에서 원래 에러를 그대로 보여주게
    한다."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as zin:
            names = zin.namelist()
            if "xl/styles.xml" not in names:
                return None
            styles_text = zin.read("xl/styles.xml").decode("utf-8")
            fixed_text = re.sub(r"applyNumberForm(?!at)", "applyNumberFormat", styles_text)
            if fixed_text == styles_text:
                return None
            out = BytesIO()
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
                for name in names:
                    content = zin.read(name)
                    if name == "xl/styles.xml":
                        content = fixed_text.encode("utf-8")
                    zout.writestr(name, content)
            return out.getvalue()
    except Exception:
        return None


class _SimpleSheet:
    """openpyxl Worksheet를 흉내내는 최소한의 어댑터. _import_statement 등
    기존 코드는 ws.iter_rows(min_row=, max_row=, values_only=True)만 쓰므로
    그것만 구현하면 HTML표/구버전 xls도 기존 파싱 로직을 그대로 재사용할 수
    있다."""

    def __init__(self, rows):
        self._rows = rows

    def iter_rows(self, min_row=1, max_row=None, values_only=True):
        start = max(min_row - 1, 0)
        end = max_row if max_row is not None else len(self._rows)
        for row in self._rows[start:end]:
            yield tuple(row)


class _SimpleWorkbook:
    def __init__(self, rows):
        self.active = _SimpleSheet(rows)


def _looks_like_html(data):
    """일부 카드사(씨티 등)는 진짜 엑셀 파일이 아니라, 엑셀에서 연 HTML
    표를 그대로 .xls 확장자로 내보낸다(파일 내용은 사실 <html>...). 확장자만
    보고는 구분이 안 돼서 내용으로 판단한다."""
    head = data[:4096]
    return head.lstrip()[:5].lower() in (b"<html", b"<!doc") or b"<table" in head.lower()


def _load_html_table_workbook(data):
    """HTML로 찍힌 '엑셀'을 읽는다. <table> 안의 행을 전부 순서대로 이어붙여서
    한 시트처럼 만든다(보통 표 하나 + 아래에 '총 이용건수/총 이용금액' 같은
    요약 표가 하나 더 있는데, 요약 표는 날짜/금액 열이 안 맞아서 기존 파싱
    로직이 알아서 건너뛴다)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(data, "lxml")
    rows = []
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
    if not rows:
        raise ValueError("HTML에서 표를 찾지 못했습니다.")
    return _SimpleWorkbook(rows)


def _load_legacy_xls_workbook(data):
    """진짜 옛날 바이너리 .xls(OLE2) 파일을 읽는다 - openpyxl은 .xlsx만
    지원해서 이 포맷은 아예 못 연다."""
    import xlrd

    wb = xlrd.open_workbook(file_contents=data)
    sheet = wb.sheet_by_index(0)
    rows = []
    for r in range(sheet.nrows):
        row = []
        for c in range(sheet.ncols):
            cell = sheet.cell(r, c)
            if cell.ctype == xlrd.XL_CELL_DATE:
                try:
                    dt = xlrd.xldate_as_datetime(cell.value, wb.datemode)
                    row.append(dt.strftime("%Y-%m-%d") if dt.time().hour == dt.time().minute == 0 else dt.strftime("%Y-%m-%d %H:%M:%S"))
                except Exception:
                    row.append(cell.value)
            else:
                row.append(cell.value)
        rows.append(row)
    return _SimpleWorkbook(rows)


def _load_workbook_resilient(file_storage):
    data = file_storage.read()
    if _looks_like_html(data):
        return _load_html_table_workbook(data)
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return _load_legacy_xls_workbook(data)
    try:
        return load_workbook(BytesIO(data), data_only=True)
    except Exception:
        repaired = _repair_xlsx_styles(data)
        if repaired is None:
            raise
        return load_workbook(BytesIO(repaired), data_only=True)


@app.route("/api/import/usage", methods=["POST"])
def import_usage():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "파일을 선택하세요."}), 400

    try:
        wb = _load_workbook_resilient(file)
    except Exception:
        return jsonify({"error": "엑셀 파일을 읽을 수 없습니다. .xlsx 파일인지 확인해주세요."}), 400
    ws = wb.active

    first_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None) or []
    header_cells = [str(c).strip() if c is not None else "" for c in first_row]
    is_own_template = any("카드명" in c for c in header_cells) and any("혜택명" in c for c in header_cells)

    if is_own_template:
        return _import_own_template(ws)

    header_info = find_statement_header(ws)
    if not header_info:
        return jsonify({
            "error": "엑셀에서 날짜·금액 열을 찾지 못했습니다. 카드사에서 받은 파일 원본 그대로 올려주세요 "
                     "(헤더에 '이용일자'나 '승인금액' 같은 표현이 있어야 자동으로 인식됩니다)."
        }), 400

    return _import_statement(ws, header_info)


# ---- 영수증/결제내역 캡쳐 OCR 등록 ----
# 엑셀 업로드랑 같은 목적(과거/누락 결제를 보충)인데, 엑셀이 없고 캡쳐
# 한 장밖에 없을 때를 위해 추가함(2026-11-14). 카드사/은행 앱마다 레이아웃이
# 완전히 달라서 "한 줄짜리 문자 알림"용 parse_notification과는 다른 전용
# 파서가 필요함 - 이름표(라벨)+값이 서로 다른 줄에 떨어져 있는 구조라서,
# 화면에서 제일 먼저 나오는 "OOO원"(단, 부가세/누적금액 같은 금칙어 라벨
# 바로 다음 건 제외)을 진짜 결제금액으로 보고, 그 바로 앞 줄을 가맹점명으로
# 쓴다. 확신 없는 건 빈칸으로 둔다(추측 금지) - raw_text에 OCR 원문을 전부
# 남겨서 틀렸으면 사람이 바로 알아챌 수 있게 한다.
_RECEIPT_AMOUNT_BLACKLIST_LABELS = [
    "부가세", "누적금액", "포인트", "할인", "캐시백", "적립", "수수료", "이자", "잔액",
    "현지금액", "합계포인트", "공급가액",
]
_RECEIPT_CHROME_LABELS = {
    "카드매출전표", "상세내역", "결제내역", "거래내역", "이용내역", "결제상세",
    "주문상세", "구매내역", "영수증",
}
# 날짜(YY[YY].MM.DD [HH:MM[:SS]]) 모양의 줄을 찾을 때 공용으로 쓴다 - OCR
# 재인식 대상을 고를 때도, 날짜 줄이 last4(카드뒷자리) 추출에 잘못 걸리는 걸
# 막을 때도 똑같이 필요해서 모듈 레벨로 뺐다.
_DATE_LINE_RE = re.compile(
    r"^\d{2}(?:\d{2})?[.\-]\d{1,2}[.\-]\d{1,2}([.\s]+\d{1,2}[:.]\d{2}(?:[:.]\d{2})?)?\.?$"
)
# last4 추출을 건너뛸지 판단할 땐 줄 전체가 아니라 "날짜 모양을 포함하는지"만
# 보면 충분하다(뒤에 다른 잡음이 더 붙어 있어도 상관없음) - _DATE_LINE_RE는
# 줄 전체가 정확히 날짜 모양이어야 해서 "09.2300"처럼 초 자리가 깨지면
# 전체매칭엔 실패하지만, 그래도 날짜 줄인 건 맞으므로 이걸로 한 번 더 본다.
_DATE_LIKE_RE = re.compile(r"\d{2}(?:\d{2})?[.\-]\d{1,2}[.\-]\d{1,2}")
_OCR_READER = None


def _get_ocr_reader():
    """easyocr Reader는 생성(모델 로딩)이 느려서 한 번만 만들고 재사용한다
    (서버 켜져 있는 동안 계속). 첫 호출 때 모델을 인터넷에서 받아오므로
    그때만 느리고 그다음부턴 로컬에 캐시돼서 빠름."""
    global _OCR_READER
    if _OCR_READER is None:
        import easyocr
        _OCR_READER = easyocr.Reader(["ko", "en"], gpu=False)
    return _OCR_READER


def _find_ampm_fallback_time(lines):
    """카카오톡 "알림톡 도착" 캡쳐는 알림 본문의 거래시각(위쪽)이랑 별개로,
    카톡 자체가 그 메시지를 받은 시각("오후 4:14")도 맨 아래 따로 찍혀서
    같이 읽힌다. 둘은 같은 시각을 가리키는 별개의 글자라서, 거래시각 쪽
    숫자가 OCR로 깨져서(예: "16:14"가 "16:74"로, 분이 59를 넘어 무효한
    값이 됨) 못 쓰게 됐을 때 이 카톡 수신시각으로 대신 보충할 수 있다
    (완전히 다른 글자를 두 번 읽은 거라 같은 자리에서 같은 식으로 깨질
    가능성은 낮음). 못 찾으면 None(지어내지 않음)."""
    for line in lines:
        m = re.match(r"^(오전|오후)\s*(\d{1,2})[:.](\d{2})$", (line or "").strip())
        if m:
            hour, minute = int(m.group(2)), int(m.group(3))
            if m.group(1) == "오후" and hour != 12:
                hour += 12
            elif m.group(1) == "오전" and hour == 12:
                hour = 0
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return hour, minute
    return None


def _parse_receipt_ocr(lines):
    amount = None
    merchant = ""
    occurred_at = None
    last4 = ""
    approval_no = ""
    prev_text = ""
    recent_lines = []  # 가맹점명을 찾을 때 거슬러 올라갈 최근 줄들(노이즈 포함 그대로)
    for line_idx, raw_line in enumerate(lines):
        text = (raw_line or "").strip()
        if not text:
            continue

        if amount is None:
            m = re.match(r"^([1-9][\d,]*)\s*원$", text)
            if m and not any(bad in prev_text for bad in _RECEIPT_AMOUNT_BLACKLIST_LABELS):
                try:
                    amount = float(m.group(1).replace(",", ""))
                    # 가맹점명 바로 앞에 화살표(>)나 아이콘 한 글자 같은
                    # OCR 노이즈가 끼어드는 경우가 있어서, 바로 앞 줄이
                    # 너무 짧거나 글자가 없으면 그 전 줄들을 거슬러 올라가
                    # 제일 가까운 "이름같이 생긴"(한글/영문 2자 이상) 줄을 쓴다.
                    for candidate in reversed(recent_lines):
                        if candidate in _RECEIPT_CHROME_LABELS:
                            continue
                        if re.search(r"[가-힣A-Za-z]{2,}", candidate):
                            merchant = candidate
                            break
                except ValueError:
                    pass

        if occurred_at is None:
            # 시간 구분자로 ":"가 와야 하는데 OCR이 "."로 잘못 읽는 경우가
            # 있어서(실제로 "01:01"을 "07.07"로 오인식한 사례 발견) 둘 다
            # 받아준다. 그리고 월/일/시/분이 말이 안 되는 값(월 71 같은)이면
            # OCR이 숫자를 잘못 읽은 거니 그 날짜는 통째로 버린다(엉뚱한
            # 날짜를 저장하느니 빈칸이 낫다 - 추측 금지).
            dm = re.search(
                r"(\d{2})[.\-](\d{1,2})[.\-](\d{1,2})\.?\s*"
                r"(?:(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?|(\d{2})(\d{2})(\d{2})?(?!\d))?",
                text,
            )
            if dm:
                month, day = int(dm.group(2)), int(dm.group(3))
                if 1 <= month <= 12 and 1 <= day <= 31:
                    if dm.group(4):
                        hour, minute, second = int(dm.group(4)), int(dm.group(5)), int(dm.group(6) or 0)
                    elif dm.group(7):
                        # "180206"처럼 시:분:초 사이에 구분자(:,.)가 아예 없이
                        # 붙어서 읽히는 경우("2026.11.08 180206") - 숫자
                        # 2자리씩 끊어서 시/분/초로 본다.
                        hour, minute, second = int(dm.group(7)), int(dm.group(8)), int(dm.group(9) or 0)
                    else:
                        # 날짜만 있고 시간이 같은 줄에 없는 경우 - "거래일
                        # 2026.11.08 18:02"처럼 라벨/값이 표 형태인 상세내역
                        # 화면은 OCR이 날짜와 시간을 서로 다른 줄로 따로
                        # 읽는 경우가 있다. 바로 다음 줄들 중 시간만 있는
                        # 모양("18:02")이 있으면 그걸로 보충하고, 없으면
                        # 00:00:00으로 둔다(지어내지 않음).
                        hour, minute, second = 0, 0, 0
                        for next_line in lines[line_idx + 1:line_idx + 3]:
                            tm = re.match(r"^(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?$", (next_line or "").strip())
                            if tm:
                                hour, minute, second = int(tm.group(1)), int(tm.group(2)), int(tm.group(3) or 0)
                                break
                    if 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59:
                        occurred_at = f"20{dm.group(1)}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
            else:
                # "11/03 23:05"처럼 연도 없이 월/일을 "/"로 적는 카드사
                # 알림톡(카카오톡 "알림톡 도착") 캡쳐 형식도 있다. 연도가
                # 안 찍혀 있지만 알림이니 당연히 최근(올해) 날짜라 올해로
                # 본다 - 지어내는 게 아니라 알림이라는 성격상 확실한 값.
                # 시간 구분자가 OCR에서 ":"가 "."으로 잘못 읽히는 경우("23:05"가
                # "23.05"로 오인식)가 흔해서 위 날짜 패턴과 똑같이 둘 다 받아준다.
                # "11/2701:58"처럼 날짜와 시간 사이에 공백이 아예 없이
                # 붙어서 읽히는 경우도 있어서(실제로 발견) \s+(공백 필수)가
                # 아니라 \s*(있어도 되고 없어도 됨)로 받는다.
                dm2 = re.match(r"^(\d{1,2})/(\d{1,2})\s*(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?$", text)
                if dm2:
                    month, day = int(dm2.group(1)), int(dm2.group(2))
                    hour, minute, second = int(dm2.group(3)), int(dm2.group(4)), int(dm2.group(5) or 0)
                    if 1 <= month <= 12 and 1 <= day <= 31 and 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59:
                        occurred_at = f"{datetime.now().year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
                    elif 1 <= month <= 12 and 1 <= day <= 31:
                        # 시간 쪽 숫자가 OCR로 깨져서(예: 분이 59 넘음) 못 쓰게
                        # 됐어도 날짜는 멀쩡하면, 카톡 수신시각("오후 4:14")
                        # 으로 시간만 보충한다.
                        fallback = _find_ampm_fallback_time(lines)
                        if fallback:
                            hour, minute = fallback
                            occurred_at = f"{datetime.now().year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:00"
                else:
                    # "11/20"과 "16:14"가 아예 서로 다른 줄로 따로 읽히는
                    # 경우도 있다(위 YY.MM.DD 형식의 날짜/시간 분리 줄 보완과
                    # 똑같은 이유) - 날짜만 있는 줄이면 바로 다음 줄들에서
                    # 시간만 있는 모양을 찾는다.
                    dm3 = re.match(r"^(\d{1,2})/(\d{1,2})$", text)
                    if dm3:
                        month, day = int(dm3.group(1)), int(dm3.group(2))
                        if 1 <= month <= 12 and 1 <= day <= 31:
                            for next_line in lines[line_idx + 1:line_idx + 3]:
                                tm = re.match(r"^(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?$", (next_line or "").strip())
                                if tm:
                                    hour, minute, second = int(tm.group(1)), int(tm.group(2)), int(tm.group(3) or 0)
                                    if 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59:
                                        occurred_at = (
                                            f"{datetime.now().year}-{month:02d}-{day:02d} "
                                            f"{hour:02d}:{minute:02d}:{second:02d}"
                                        )
                                    break

        if not last4 and not _DATE_LIKE_RE.search(text):
            # 카드번호가 두 줄로 쪼개져 읽히면("5188-37.\n~0741"처럼) 뒷자리
            # 앞에 "-"가 아니라 "~"나 다른 깨진 기호가 올 수 있어서, "-"로
            # 한정하지 않고 "숫자 아닌 글자(또는 줄 맨 앞) 바로 뒤에 오는
            # 숫자 4개로 끝나는 줄"이면 다 받아준다(승인번호 같은 긴 숫자는
            # 뒤에서 4번째 자리 바로 앞이 숫자라서 안 걸림). 단, 날짜 줄도
            # "2026.11.10 09.2300"처럼 끝이 숫자 4개라 last4로 잘못 걸릴 수
            # 있어서 날짜 모양이면 애초에 건너뛴다.
            lm = re.search(r"(?:^|\D)(\d{4})\s*$", text) or re.search(r"(\d{3,4})\s*[*•]\s*$", text)
            if lm:
                last4 = lm.group(1)

        if prev_text == "승인번호" and re.match(r"^\d+$", text):
            approval_no = text

        prev_text = text
        recent_lines.append(text)
        if len(recent_lines) > 6:
            recent_lines.pop(0)

    # 카드사 발급사(issuer)는 위 로직에서 전혀 안 뽑고 있었다 - last4로 카드
    # 매칭이 안 되면(마스킹 "0*0*"처럼 등록된 카드와 자릿수/모양이 안 맞는
    # 경우 등) issuer+가맹점 키워드로 보조 매칭하는 기존 로직이 있는데,
    # OCR 업로드는 issuer를 아예 안 채워서 그 보조 매칭 기회 자체가 없었다.
    # 문자 알림 붙여넣기용으로 이미 검증된 parse_notification()을 그대로
    # 재사용해서(중복 로직 새로 안 짬) issuer를 뽑고, 영수증 형식 로직이
    # 금액/가맹점을 못 찾았을 때는 그 결과도 같이 보충한다.
    notif = parse_notification("\n".join(lines))
    issuer = notif["issuer"]
    if amount is None:
        # 영수증/상세내역 캡쳐가 아니라 "알림톡 도착" 같은 카드사 결제알림
        # 캡쳐일 수 있다. 이 형식은 금액이 "일시불 5,900원"처럼 레이블과 한
        # 줄에 붙어 있고(위 로직은 금액만 혼자 줄인 경우만 잡음), 가맹점명도
        # 금액보다 아래(마지막 줄)에 나와서 위의 "금액 앞쪽을 거슬러 올라가는"
        # 방식으로는 못 찾는다.
        if notif["amount"] is not None:
            amount = notif["amount"]
            if not merchant:
                merchant = notif["merchant"]
            if not last4:
                last4 = notif["last4"]

    return {
        "amount": amount,
        "merchant": merchant,
        "occurred_at": occurred_at,
        "last4": last4,
        "issuer": issuer,
        "approval_no": approval_no,
    }


@app.route("/api/inbox/from-image", methods=["POST"])
def create_inbox_item_from_image():
    """카드매출전표/카드사·은행 앱 상세내역 캡쳐 한 장을 올리면 글자를 읽어서
    "받은 결제 알림" 큐에 등록한다."""
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "이미지를 선택하세요."}), 400
    try:
        image_bytes = file.read()
    except Exception:
        return jsonify({"error": "이미지를 읽을 수 없습니다."}), 400

    pil_img = None
    try:
        from PIL import Image
        import numpy as np
        pil_img = Image.open(BytesIO(image_bytes)).convert("RGB")
        target_width = 1600
        if pil_img.width < target_width:
            scale = target_width / pil_img.width
            resample = getattr(Image, "Resampling", Image).LANCZOS
            pil_img = pil_img.resize((target_width, round(pil_img.height * scale)), resample)
        ocr_input = np.array(pil_img)
    except Exception:
        ocr_input = image_bytes  # 전처리 실패해도 원본 그대로는 시도

    try:
        reader = _get_ocr_reader()
        detections = reader.readtext(ocr_input, paragraph=False)  # detail=1(기본): [bbox, text, 신뢰도]
    except Exception as e:
        return jsonify({"error": f"글자 인식에 실패했습니다: {e}"}), 500

    # 날짜/카드번호처럼 숫자·구분기호 위주인 줄은, 전체 화면을 한 번에 읽을
    # 때보다 그 부분만 숫자 전용으로 다시 좁혀서 읽으면 오인식이 줄어든다
    # (실제로 "01:01"을 "07.07"로 잘못 읽은 사례가 있어서 추가함 - 확대만
    # 해서는 안 고쳐졌었음). 그 줄의 bbox만 크게 잘라 확대하고, 글자 종류를
    # 숫자/구분기호로 제한해서(allowlist) 다시 읽어 더 정확하면 바꿔치기한다.
    # 실패해도(크롭 실패, 재인식 실패 등) 원래 읽은 글자를 그대로 쓴다.
    # 날짜(YY.MM.DD [HH:MM[:SS]]) 모양에만 좁혀서 적용한다(_DATE_LINE_RE,
    # 모듈 상단 공용 정의) - 카드번호(마스킹된 점/동그라미가 OCR엔 숫자/점
    # 으로 잘못 보여서 일반적인 "숫자+기호" 패턴에 걸려버리면, 마스킹
    # 부분을 숫자로 강제로 읽어서 되려 더 망가뜨림. 날짜 모양만 정확히
    # 매칭해서 그 경우만 보정한다.
    lines = []
    for item in detections:
        bbox, text, _conf = item
        if pil_img is not None and _DATE_LINE_RE.match(text.strip() or ""):
            try:
                xs = [p[0] for p in bbox]
                ys = [p[1] for p in bbox]
                pad = 8
                x0, y0 = max(0, int(min(xs)) - pad), max(0, int(min(ys)) - pad)
                x1, y1 = int(max(xs)) + pad, int(max(ys)) + pad
                crop = pil_img.crop((x0, y0, x1, y1))
                if crop.height:
                    zoom = max(1, min(6, 120 // crop.height))
                    if zoom > 1:
                        resample = getattr(Image, "Resampling", Image).LANCZOS
                        crop = crop.resize((crop.width * zoom, crop.height * zoom), resample)
                    # paragraph=True로 맡기면 날짜/시간 조각을 공백 없이
                    # 붙여버린 사례가 있어서(예: "10"+"01:01" -> "1001:01")
                    # False로 각 조각을 따로 받아 직접 공백으로 이어붙인다.
                    refined = reader.readtext(
                        np.array(crop), detail=0, paragraph=False,
                        allowlist="0123456789:./-",
                    )
                    refined_text = " ".join(refined).strip()
                    if refined_text:
                        text = refined_text
            except Exception:
                pass
        lines.append(text)

    raw_text = "\n".join(lines)
    parsed = _parse_receipt_ocr(lines)
    if parsed["amount"] is None:
        return jsonify({
            "error": "금액을 못 읽었습니다. 더 선명한 캡쳐로 다시 올리거나, "
                     "필요하면 직접 알림을 추가해주세요.",
            "ocr_text": raw_text,
        }), 400

    occurred_at = parsed["occurred_at"] or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO inbox_items
               (raw_text, amount, last4, issuer, merchant, occurred_at, approval_no, source, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'screenshot', 'pending')""",
            (raw_text, parsed["amount"], parsed["last4"], parsed["issuer"], parsed["merchant"], occurred_at,
             parsed["approval_no"]),
        )
        _try_auto_match_all(conn, cur.lastrowid)
        conn.commit()
    finally:
        conn.close()

    result = serialize_full()
    result["ocr_preview"] = {
        "amount": parsed["amount"],
        "merchant": parsed["merchant"],
        "occurred_at": occurred_at,
        "last4": parsed["last4"],
    }
    return jsonify(result)


def _retry_unassigned_loan_attachments():
    """미분류로 남아있는 증빙파일을, 서버가 켜질 때마다 최신 매칭
    로직으로 다시 시도한다 - 매칭 로직을 고친 뒤에도 사용자가 같은
    파일을 다시 올려야만 재시도되던 걸(그것도 "이미 있는 파일"로 보고
    조용히 건너뛰어서 실제로는 재시도조차 안 됐음), 재업로드 없이
    자동으로 풀리게 한다."""
    conn = get_conn()
    try:
        unassigned = conn.execute(
            "SELECT id, file_name, original_name, extracted_text FROM loan_attachments WHERE loan_id IS NULL"
        ).fetchall()
        if not unassigned:
            return
        loans = conn.execute("SELECT * FROM loans").fetchall()
        for item in unassigned:
            # 파일 하나 처리하다 예외가 나도(글자 인식 실패 등) 나머지
            # 파일까지 같이 재시도 안 되면 안 되니, 파일마다 따로
            # try/commit한다.
            try:
                path = os.path.join(UPLOAD_DIR, item["file_name"])
                with open(path, "rb") as f:
                    file_bytes = f.read()
                fname = item["original_name"] or item["file_name"]
                text = _get_or_extract_attachment_text(
                    conn, item["id"], item["extracted_text"], fname, file_bytes
                )
                if not text:
                    continue
                matched_loans = _match_loans_for_attachment_text(loans, text)
                if not matched_loans:
                    continue
                conn.execute(
                    "UPDATE loan_attachments SET loan_id = ? WHERE id = ?",
                    (matched_loans[0]["id"], item["id"]),
                )
                for loan in matched_loans[1:]:
                    conn.execute(
                        "INSERT INTO loan_attachments (loan_id, file_name, original_name, file_hash) "
                        "SELECT ?, file_name, original_name, file_hash FROM loan_attachments WHERE id = ?",
                        (loan["id"], item["id"]),
                    )
                conn.commit()
            except BaseException as e:
                _reraise_if_fatal(e)
                conn.rollback()
                print(f"[증빙 재시도] {item['file_name']} 처리 실패: {e}")
    except BaseException as e:
        _reraise_if_fatal(e)
    finally:
        conn.close()


def _revalidate_loan_attachments():
    """이미 배정된 증빙파일도 최신 매칭 기준으로 다시 검증한다 - 메모 속
    날짜("2026.11.17")를 계좌번호로 오인하던 버그(수정함) 때문에 그
    연도가 적힌 대출마다 그해 아무 서류나 잘못 붙는 사고가 실제로
    있었다(한 대출에 전혀 무관한 파일이 여러 개 쌓임). 지금 기준으로
    다시 검증해서 더 이상 근거가 없는 연결은 풀어 미분류로 돌려놓고,
    다음 재시도 때 실제로 맞는 대출이 있으면 거기로 다시 붙는다."""
    conn = get_conn()
    try:
        assigned = conn.execute(
            "SELECT id, loan_id, file_name, original_name, extracted_text FROM loan_attachments "
            "WHERE loan_id IS NOT NULL"
        ).fetchall()
        if not assigned:
            return
        loans = conn.execute("SELECT * FROM loans").fetchall()
        loans_by_id = {l["id"]: l for l in loans}
        for item in assigned:
            try:
                path = os.path.join(UPLOAD_DIR, item["file_name"])
                with open(path, "rb") as f:
                    file_bytes = f.read()
                fname = item["original_name"] or item["file_name"]
                text = _get_or_extract_attachment_text(
                    conn, item["id"], item["extracted_text"], fname, file_bytes
                )
                if not text:
                    continue
                matched_loans = _match_loans_for_attachment_text(loans, text)
                matched_ids = {l["id"] for l in matched_loans}
                current_loan = loans_by_id.get(item["loan_id"])
                if current_loan and item["loan_id"] not in matched_ids:
                    conn.execute(
                        "UPDATE loan_attachments SET loan_id = NULL WHERE id = ?", (item["id"],)
                    )
                    conn.commit()
            except BaseException as e:
                _reraise_if_fatal(e)
                conn.rollback()
                print(f"[증빙 재검증] {item['file_name']} 처리 실패: {e}")
    except BaseException as e:
        _reraise_if_fatal(e)
    finally:
        conn.close()


def _fix_online_japhwa_overcredit():
    """다이소 온라인 결제를 "잡화"로 과대 적립했던 과거 기록을 서버 시작 시
    한 번 바로잡는다 - "온라인은 잡화 할인 대상 아님"으로 코드만 고치고
    이미 저장된 usage_logs는 손 안 대면, 고치기 전에 기록된 과거 건들은
    계속 틀린 금액(10%)으로 남아있게 된다(알림 목록 추천 배지만 고치고
    이미 쌓인 데이터는 안 고치는 식으로 또 놓칠 뻔한 부분)."""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT usage_logs.id, usage_logs.raw_value, inbox_items.raw_text
               FROM usage_logs
               LEFT JOIN inbox_items ON inbox_items.id = usage_logs.source_inbox_id
               WHERE usage_logs.rate_category = '잡화'"""
        ).fetchall()
        fixed = 0
        for r in rows:
            if not (r["raw_text"] and "온라인" in r["raw_text"]):
                continue
            raw_value = r["raw_value"] if r["raw_value"] is not None else 0
            new_memo = f"결제 {raw_value:,.0f}원 → 할인 0원(0% - 온라인 구매는 잡화 할인 대상 아님, 자동 정정)"
            conn.execute(
                "UPDATE usage_logs SET used_value = 0, memo = ?, rate_category = '' WHERE id = ?",
                (new_memo, r["id"]),
            )
            fixed += 1
        if fixed:
            conn.commit()
            print(f"[온라인 잡화 정정] {fixed}건 바로잡음")
    except BaseException as e:
        _reraise_if_fatal(e)
    finally:
        conn.close()


def _retry_auto_match_all_pending():
    """대출/고정지출/보험 탭의 "자동 매칭" 버튼을 직접 눌러야만 매칭
    로직이 고쳐진 뒤의 기존 큐가 다시 시도됐다 - 메리츠화재 이자 알림이
    매칭 로직을 고친 뒤에도 "큐에 이미 들어있던 건"이라 버튼을 누르기
    전까진 계속 그대로 남아있었음(실제로 재현된 문제). 서버 시작 시
    자동으로 한 번 돌려서, 버튼을 누르지 않아도 코드 수정이 기존 큐에
    바로 반영되게 한다."""
    conn = get_conn()
    try:
        _auto_match_all_pending(conn)
    except BaseException as e:
        _reraise_if_fatal(e)
    finally:
        conn.close()


def _merge_duplicate_loan_withdrawal_accounts():
    """출금계좌가 대출마다 계좌번호 없는 라벨("IBK사업자"/"0042"/
    "토스뱅크" 등)과 일부만 적힌 번호가 섞여서 등록돼 있어, 실제로는
    같은 계좌인데 선택 목록에 여러 개로 갈라져 보이던 걸 정리한다.

    처음엔 "케이뱅크"/"IBK사업자"만 마스킹된 형태로 남기고 나머지는
    전체번호로 등록하는 식으로 대출마다 다르게 처리했었는데, 등록
    형식이 들쭉날쭉하면 나중에 또 헷갈린다는 지적을 받아 전부 전체
    계좌번호로 통일한다. 대신 실제 알림은 은행마다 가운데를 마스킹하고
    뒷자리만 보여주는 경우가 있어서("듀얼K입출금통장(0042)",
    "395-******-01-018") 전체번호만 등록하면 못 찾는 문제가 있었는데,
    이건 등록값을 짧게 줄이는 게 아니라 _try_auto_match_loan_account_text
    쪽에 "등록값이 충분히 길면 뒷 4자리만으로도 찾아본다"는 보완 로직을
    추가해서 코드 쪽에서 해결했다(대출마다 등록 형식을 다르게 가져가는
    게 아니라, 전체번호 하나로 통일해도 코드가 양쪽 다 커버하게 함).
    이어서 받은 전체번호로 "IBK개인"/"광주은행"/"KB사업자"/"하나은행"도
    마저 통일한다. "하나은행"은 실제 알림에 신/구 계좌번호가 같이 찍힌
    걸 직접 봤는데("106-******-24107(구)620****16681" - 구번호 끝자리가
    예전에 등록했던 6681), 사용자가 불러준 새 번호(106-891584-24107)는
    그 알림의 새 계좌번호 쪽과 일치해서 그대로 쓴다."""
    conn = get_conn()
    try:
        renames = [
            ("IBK사업자", "IBK사업자 395-138608-01-018"),
            ("IBK사업자 395-01-018", "IBK사업자 395-138608-01-018"),
            ("카카오뱅크 4526", "카카오뱅크 3333-10-2514526"),
            ("KB국민은행 6193", "KB국민은행 457-21-1466-193"),
            ("0042", "케이뱅크 100-134-550042"),
            ("케이뱅크 0042", "케이뱅크 100-134-550042"),
            ("토스뱅크", "토스뱅크 1000-0603-4545"),
            ("IBK개인", "IBK개인 375-050140-02-016"),
            ("IBK개인 375-******-02-016", "IBK개인 375-050140-02-016"),
            ("광주은행", "광주은행 1121-034-843545"),
            ("KB사업자", "KB국민은행 사업 522301-01-395776"),
            ("하나은행 6681", "하나은행 106-891584-24107"),
        ]
        for old, new in renames:
            conn.execute("UPDATE loans SET withdrawal_account = ? WHERE withdrawal_account = ?", (new, old))
        conn.commit()
    except BaseException as e:
        _reraise_if_fatal(e)
    finally:
        conn.close()


init_db()
apply_startup_data_fixes()
_revalidate_loan_attachments()
_retry_unassigned_loan_attachments()
_retry_medical_no_benefit_items()
_fix_online_japhwa_overcredit()
_merge_duplicate_loan_withdrawal_accounts()
_retry_auto_match_all_pending()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)
