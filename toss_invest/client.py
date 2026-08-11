"""토스증권 오픈API 클라이언트.

인증: OAuth2 Client Credentials (POST /oauth2/token, HTTP Basic client_id:client_secret)
문서: https://developers.tossinvest.com/docs
키 발급: https://corp.tossinvest.com/ko/open-api

자격증명은 코드에 직접 넣지 말고 환경변수(TOSSINVEST_CLIENT_ID, TOSSINVEST_CLIENT_SECRET)나
.env 파일로 관리한다.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import requests

DEFAULT_BASE_URL = "https://openapi.tossinvest.com"


class TossInvestAPIError(RuntimeError):
    def __init__(self, status_code: int, code: str | None, message: str | None, request_id: str | None = None):
        self.status_code = status_code
        self.code = code
        self.request_id = request_id
        super().__init__(f"[{status_code}] {code}: {message} (requestId={request_id})")


@dataclass
class _Token:
    access_token: str
    expires_at: float


class TossInvestClient:
    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        base_url: str | None = None,
        account_seq: str | None = None,
        timeout: float = 10.0,
    ):
        self.client_id = client_id or os.environ.get("TOSSINVEST_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("TOSSINVEST_CLIENT_SECRET")
        if not self.client_id or not self.client_secret:
            raise ValueError(
                "TOSSINVEST_CLIENT_ID / TOSSINVEST_CLIENT_SECRET 이 필요합니다. "
                "환경변수나 .env 파일에 설정하세요."
            )
        self.base_url = (base_url or os.environ.get("TOSSINVEST_API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.default_account_seq = account_seq or os.environ.get("TOSSINVEST_ACCOUNT")
        self.timeout = timeout
        self._session = requests.Session()
        self._token: _Token | None = None

    def _get_access_token(self) -> str:
        if self._token and self._token.expires_at - 30 > time.time():
            return self._token.access_token

        resp = self._session.post(
            f"{self.base_url}/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
            timeout=self.timeout,
        )
        if not resp.ok:
            self._raise_for_error(resp)
        payload = resp.json()
        self._token = _Token(
            access_token=payload["access_token"],
            expires_at=time.time() + float(payload.get("expires_in", 3600)),
        )
        return self._token.access_token

    def _raise_for_error(self, resp: requests.Response) -> None:
        code = None
        message = resp.text
        request_id = resp.headers.get("X-Request-Id")
        try:
            body = resp.json()
            err = body.get("error", body)
            code = err.get("code")
            message = err.get("message", message)
            request_id = err.get("requestId", request_id)
        except ValueError:
            pass
        raise TossInvestAPIError(resp.status_code, code, message, request_id)

    def _request(
        self,
        method: str,
        path: str,
        *,
        account_seq: str | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        headers = {"Authorization": f"Bearer {self._get_access_token()}"}
        acc = account_seq or self.default_account_seq
        if acc:
            headers["X-Tossinvest-Account"] = str(acc)

        resp = self._session.request(
            method,
            f"{self.base_url}{path}",
            headers=headers,
            params=params,
            json=json_body,
            timeout=self.timeout,
        )
        if not resp.ok:
            self._raise_for_error(resp)
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    # ---- 조회 (읽기 전용) ----

    def list_accounts(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/api/v1/accounts")
        return data.get("accounts", data) if isinstance(data, dict) else data

    def get_holdings(self, account_seq: str | None = None) -> dict[str, Any]:
        return self._request("GET", "/api/v1/holdings", account_seq=account_seq)

    def get_buying_power(self, account_seq: str | None = None) -> dict[str, Any]:
        return self._request("GET", "/api/v1/buying-power", account_seq=account_seq)

    def get_sellable_quantity(self, symbol: str, account_seq: str | None = None) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/holdings/{symbol}/sellable-quantity", account_seq=account_seq)

    # ---- 주문 (실거래 - 기본적으로 dry_run=True로 막아둠) ----

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        *,
        quantity: float | None = None,
        order_amount: float | None = None,
        price: float | None = None,
        client_order_id: str | None = None,
        confirm_high_value_order: bool = False,
        account_seq: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """실제 주문 전송. 기본값 dry_run=True는 서버로 보내지 않고 전송될 요청만 반환한다.

        실거래를 원하면 명시적으로 dry_run=False를 넘겨야 한다.
        """
        body: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
        }
        if quantity is not None:
            body["quantity"] = quantity
        if order_amount is not None:
            body["orderAmount"] = order_amount
        if price is not None:
            body["price"] = price
        if client_order_id is not None:
            body["clientOrderId"] = client_order_id
        if confirm_high_value_order:
            body["confirmHighValueOrder"] = True

        if dry_run:
            return {"dry_run": True, "would_send": body}

        return self._request("POST", "/api/v1/orders", account_seq=account_seq, json_body=body)

    def get_order(self, order_id: str, account_seq: str | None = None) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/orders/{order_id}", account_seq=account_seq)

    def cancel_order(self, order_id: str, account_seq: str | None = None, dry_run: bool = True) -> dict[str, Any]:
        if dry_run:
            return {"dry_run": True, "would_cancel": order_id}
        return self._request("DELETE", f"/api/v1/orders/{order_id}", account_seq=account_seq)
