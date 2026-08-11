# 토스증권 오픈API 연동

토스증권 오픈API(https://developers.tossinvest.com/docs)로 내 포트폴리오를 조회하고,
Claude에서 대화로 확인할 수 있게 해주는 모듈입니다. 이 저장소의 기존 마진 대시보드
(`app.py` 등)와는 무관한 별도 기능이라 `toss_invest/` 폴더로 분리했습니다.

## 1. 준비

1. https://corp.tossinvest.com/ko/open-api 에서 오픈API 신청 후 Client ID / Secret 발급
2. 의존성 설치

   ```bash
   pip install -r toss_invest/requirements.txt
   ```

3. 자격증명 설정 — `toss_invest/.env.example`을 복사해 `toss_invest/.env`로 저장하고 값 채우기

   ```bash
   cp toss_invest/.env.example toss_invest/.env
   ```

   `.env`는 `.gitignore`에 등록되어 있어 커밋되지 않습니다. 절대 코드에 직접 키를 적지 마세요.

## 2. 포트폴리오 조회 (CLI)

```bash
python -m toss_invest.cli
```

계좌별 매수가능금액, 보유 종목(수량/평균단가/현재가/평가금액/손익), 총 평가금액·총 손익을 출력합니다.

## 3. Claude에서 대화로 조회 (MCP)

`toss_invest/mcp_server.py`는 읽기 전용 도구 3개(`list_accounts`, `get_holdings`, `get_buying_power`)를
Claude에 노출하는 MCP 서버입니다. Claude Desktop의 `claude_desktop_config.json`에 아래처럼 등록하세요.

```json
{
  "mcpServers": {
    "toss-invest": {
      "command": "python",
      "args": ["-m", "toss_invest.mcp_server"],
      "cwd": "/절대/경로/A",
      "env": {
        "TOSSINVEST_CLIENT_ID": "발급받은 값",
        "TOSSINVEST_CLIENT_SECRET": "발급받은 값"
      }
    }
  }
}
```

등록 후 Claude에서 "내 토스증권 포트폴리오 보여줘" 같은 대화로 바로 조회할 수 있습니다.

## 4. 주문/자동매매에 대해

`client.py`에 `place_order` / `cancel_order`가 있지만 **기본값이 `dry_run=True`**라서
실제로는 아무 주문도 나가지 않고, 서버로 보낼 요청 내용만 돌려줍니다. 실거래를 켜려면
호출부에서 명시적으로 `dry_run=False`를 넘겨야 합니다.

자동매매 봇은 여기 포함하지 않았습니다 — 어떤 종목을, 어떤 조건(가격/지표/시간)에,
얼마의 예산과 손실 한도로 매매할지는 실제 돈이 오가는 결정이라 먼저 규칙을 정해야
안전하게 만들 수 있습니다. 원하시면 그 규칙을 알려주시면 이어서 만들어드릴게요.
