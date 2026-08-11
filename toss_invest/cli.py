"""토스증권 포트폴리오 조회 CLI.

사용법:
    python -m toss_invest.cli
"""
from __future__ import annotations

from .client import TossInvestAPIError, TossInvestClient

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _fmt_money(value, currency: str = "KRW") -> str:
    if value is None:
        return "-"
    return f"{value:,.0f} {currency}" if currency == "KRW" else f"{value:,.2f} {currency}"


def print_portfolio() -> None:
    client = TossInvestClient()

    accounts = client.list_accounts()
    if not accounts:
        print("연결된 계좌가 없습니다.")
        return

    for account in accounts:
        seq = account.get("accountSeq")
        print(f"\n=== 계좌 {account.get('accountNo')} ({account.get('accountType')}) ===")

        try:
            buying_power = client.get_buying_power(account_seq=seq)
            currency = buying_power.get("currency", "KRW")
            print(f"매수 가능 금액: {_fmt_money(buying_power.get('cashBuyingPower'), currency)}")
        except TossInvestAPIError as e:
            print(f"매수 가능 금액 조회 실패: {e}")

        try:
            holdings = client.get_holdings(account_seq=seq)
        except TossInvestAPIError as e:
            print(f"보유 종목 조회 실패: {e}")
            continue

        items = holdings.get("items", [])
        if not items:
            print("보유 종목 없음")
        else:
            print(f"{'종목':<10}{'수량':>10}{'평균매입가':>14}{'현재가':>12}{'평가금액':>14}{'손익':>14}")
            for item in items:
                currency = item.get("currency", "KRW")
                pl = item.get("profitLoss", {})
                pl_amount = pl.get("amount") if isinstance(pl, dict) else pl
                print(
                    f"{item.get('symbol', ''):<10}"
                    f"{item.get('quantity', 0):>10}"
                    f"{_fmt_money(item.get('averagePurchasePrice'), currency):>14}"
                    f"{_fmt_money(item.get('lastPrice'), currency):>12}"
                    f"{_fmt_money(item.get('marketValue'), currency):>14}"
                    f"{_fmt_money(pl_amount, currency):>14}"
                )

        total_value = holdings.get("marketValue")
        total_pl = holdings.get("profitLoss")
        if total_value is not None:
            print(f"총 평가금액: {_fmt_money(total_value)}")
        if total_pl is not None:
            print(f"총 손익: {_fmt_money(total_pl)}")


if __name__ == "__main__":
    print_portfolio()
