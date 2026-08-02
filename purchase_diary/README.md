# 구매 다이어리

자주 사는 생필품(화장지, 세제 등)의 구매 기록을 직접 입력해서, 지난 구매/이번 구매/구매 간격/가격 변화를
품목별로 한눈에 보여주는 앱입니다. 쇼핑몰 계정 로그인이나 자동 스크래핑은 하지 않고, 사용자가 구매할
때마다 날짜·가격만 입력하는 방식이라 약관·보안 문제에서 자유롭습니다.

기존 `이유상점 Margin Board`(레포 루트의 `app.py`)와는 완전히 분리된 별도 앱이며, 같은 다크 테마
디자인 톤(카드, KPI 타일)만 재사용합니다.

## 실행 방법

```bash
cd purchase_diary
pip install -r requirements.txt
python app.py
```

브라우저에서 `http://127.0.0.1:5100` 접속. 데이터는 `purchase_diary/diary.db`(SQLite)에 저장됩니다.

## 인터넷 + 앱 겸용

- PC/모바일 브라우저에서 그대로 사용할 수 있고,
- 모바일 브라우저에서 "홈 화면에 추가"를 하면 PWA(Progressive Web App)로 설치되어
  앱 아이콘을 눌러 전체화면으로 실행됩니다.
- 별도의 iOS/Android 네이티브 앱 빌드 없이 웹 코드 하나로 두 가지 사용 방식을 모두 지원합니다.

## 화면 구성

- 상단 KPI: 등록 품목 수 / 이번 달 지출 / 재구매 임박(3일 이내) 품목 수 / 가격 오른 품목 수
- 품목 카드: 지난 구매일, 이번 구매일, 구매 간격(직전 간격 + 평균 간격), 가격 차이(금액·%),
  평균 간격 기반 다음 재구매 예상일, 구매 기록 전체 보기/삭제
- "+ 새 품목" / "+ 구매 기록 추가" 모달로 수동 입력

## 파일 구조

```
app.py                 Flask 서버 + 계산 로직(구매 간격/가격 차이/다음 예상일)
templates/index.html
static/css/style.css
static/js/app.js
static/manifest.json   PWA 매니페스트
static/sw.js           서비스워커 (오프라인 셸 캐싱, /api/는 캐시 제외)
static/icons/          PWA 아이콘
diary.db               SQLite (최초 실행 시 자동 생성)
```
