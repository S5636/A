"""토스증권 포트폴리오를 Claude에서 대화로 조회할 수 있게 해주는 MCP 서버.

읽기 전용 도구만 노출한다 (주문 실행 도구는 의도적으로 제외 - 자동매매는
전략/리스크 규칙을 먼저 정한 뒤 별도로 붙이는 것을 권장).

Claude Desktop 설정 예시 (claude_desktop_config.json):
{
  "mcpServers": {
    "toss-invest": {
      "command": "python",
      "args": ["-m", "toss_invest.mcp_server"],
      "cwd": "/절대/경로/A",
      "env": {
        "TOSSINVEST_CLIENT_ID": "...",
        "TOSSINVEST_CLIENT_SECRET": "..."
      }
    }
  }
}
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .client import TossInvestClient

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

mcp = FastMCP("toss-invest")


def _client() -> TossInvestClient:
    return TossInvestClient()


@mcp.tool()
def list_accounts() -> list[dict]:
    """토스증권에 연결된 계좌 목록을 조회한다."""
    return _client().list_accounts()


@mcp.tool()
def get_holdings(account_seq: str | None = None) -> dict:
    """보유 종목, 평가금액, 손익 등 포트폴리오 현황을 조회한다.

    account_seq를 생략하면 TOSSINVEST_ACCOUNT 환경변수의 기본 계좌를 사용한다.
    """
    return _client().get_holdings(account_seq=account_seq)


@mcp.tool()
def get_buying_power(account_seq: str | None = None) -> dict:
    """매수 가능 금액(현금)을 조회한다."""
    return _client().get_buying_power(account_seq=account_seq)


if __name__ == "__main__":
    mcp.run()
