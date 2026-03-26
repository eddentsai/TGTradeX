from __future__ import annotations

from typing import Any

from .http import BitunixHttpTransport


class BitunixFuturesPrivateHttpApi:
    def __init__(self, transport: BitunixHttpTransport, api_key: str, secret_key: str):
        self._transport = transport
        self._api_key = api_key
        self._secret_key = secret_key

    # ── 帳戶 ──────────────────────────────────────────────────────────────────

    def get_account(self, margin_coin: str = "USDT") -> dict:
        """取得帳戶資訊"""
        return self._get("/api/v1/futures/account", {"marginCoin": margin_coin})

    # ── 交易 ──────────────────────────────────────────────────────────────────

    def place_order(self, order: dict) -> dict:
        """下單

        order 必填欄位：
          symbol, side ("BUY"|"SELL"), orderType ("LIMIT"|"MARKET"), qty

        LIMIT 訂單額外必填：price, effect ("GTC"|"IOC"|"FOK"|"POST_ONLY")
        CLOSE 倉位額外必填：positionId
        """
        self._validate_place_order(order)
        return self._post("/api/v1/futures/trade/place_order", order)

    def cancel_orders(self, symbol: str, order_list: list[dict]) -> Any:
        """取消訂單

        order_list 每項包含 {"orderId": "..."} 或 {"clientId": "..."}
        """
        body = {"symbol": symbol, "orderList": order_list}
        return self._post("/api/v1/futures/trade/cancel_orders", body)

    def get_history_orders(
        self,
        symbol: str | None = None,
        order_id: str | None = None,
        client_id: str | None = None,
        status: str | None = None,
        type_: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        skip: int | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """查詢歷史訂單"""
        query: dict[str, Any] = {}
        if symbol is not None:
            query["symbol"] = symbol
        if order_id is not None:
            query["orderId"] = order_id
        if client_id is not None:
            query["clientId"] = client_id
        if status is not None:
            query["status"] = status
        if type_ is not None:
            query["type"] = type_
        if start_time is not None:
            query["startTime"] = start_time
        if end_time is not None:
            query["endTime"] = end_time
        if skip is not None:
            query["skip"] = skip
        if limit is not None:
            query["limit"] = limit
        result = self._get("/api/v1/futures/trade/get_history_orders", query)
        return result if isinstance(result, list) else []

    def get_pending_orders(
        self,
        symbol: str | None = None,
        order_id: str | None = None,
        client_id: str | None = None,
        status: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        skip: int | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """查詢未完成訂單"""
        query: dict[str, Any] = {}
        if symbol is not None:
            query["symbol"] = symbol
        if order_id is not None:
            query["orderId"] = order_id
        if client_id is not None:
            query["clientId"] = client_id
        if status is not None:
            query["status"] = status
        if start_time is not None:
            query["startTime"] = start_time
        if end_time is not None:
            query["endTime"] = end_time
        if skip is not None:
            query["skip"] = skip
        if limit is not None:
            query["limit"] = limit
        result = self._get("/api/v1/futures/trade/get_pending_orders", query)
        # API 回傳 { orderList: [...], total: N }
        if isinstance(result, dict):
            return result.get("orderList", [])
        return result if isinstance(result, list) else []

    # ── 倉位 ──────────────────────────────────────────────────────────────────

    def get_pending_positions(self, symbol: str | None = None) -> list[dict]:
        """查詢持倉中的倉位"""
        query: dict = {}
        if symbol is not None:
            query["symbol"] = symbol
        result = self._get("/api/v1/futures/position/get_pending_positions", query)
        return result if isinstance(result, list) else []

    def get_history_positions(self, symbol: str | None = None) -> list[dict]:
        """查詢歷史倉位"""
        query: dict = {}
        if symbol is not None:
            query["symbol"] = symbol
        result = self._get("/api/v1/futures/position/get_history_positions", query)
        return result if isinstance(result, list) else []

    def adjust_margin(
        self,
        symbol: str,
        position_id: str,
        amount: str,
        type_: str,  # "ADD" | "SUB"
    ) -> Any:
        """調整保證金"""
        body = {
            "symbol": symbol,
            "positionId": position_id,
            "amount": amount,
            "type": type_,
        }
        return self._post("/api/v1/futures/position/adjust_margin", body)

    # ── 內部輔助 ──────────────────────────────────────────────────────────────

    def _get(self, path: str, query: dict | None = None) -> Any:
        return self._transport.private_request(
            self._api_key, self._secret_key, "GET", path, query=query
        )

    def _post(self, path: str, body: dict) -> Any:
        return self._transport.private_request(
            self._api_key, self._secret_key, "POST", path, body=body
        )

    @staticmethod
    def _validate_place_order(order: dict) -> None:
        if order.get("orderType") == "LIMIT" and not order.get("price"):
            raise ValueError("當 orderType 為 LIMIT 時，price 為必填")
        if order.get("orderType") == "LIMIT" and not order.get("effect"):
            raise ValueError("當 orderType 為 LIMIT 時，effect 為必填")
        if order.get("tpOrderType") == "LIMIT" and not order.get("tpOrderPrice"):
            raise ValueError("當 tpOrderType 為 LIMIT 時，tpOrderPrice 為必填")
        if order.get("slOrderType") == "LIMIT" and not order.get("slOrderPrice"):
            raise ValueError("當 slOrderType 為 LIMIT 時，slOrderPrice 為必填")
        if order.get("tradeSide") == "CLOSE" and not order.get("positionId"):
            raise ValueError("當 tradeSide 為 CLOSE 時，positionId 為必填")
