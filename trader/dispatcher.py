"""
交易指令路由器

根據 OrderRequest.exchange 將開單請求派送到對應的交易所客戶端。
"""
from __future__ import annotations

from typing import Any

from exchanges.base import BaseExchange
from trader.models import OrderRequest, OrderType


class TradeDispatcher:
    """持有多個交易所客戶端，統一接收並派送交易指令"""

    def __init__(self) -> None:
        self._exchanges: dict[str, BaseExchange] = {}

    def register(self, exchange: BaseExchange) -> None:
        """註冊一個交易所客戶端"""
        self._exchanges[exchange.name.lower()] = exchange

    def get_exchange(self, name: str) -> BaseExchange:
        key = name.lower()
        if key not in self._exchanges:
            raise ValueError(f"未知交易所: {name}，已註冊: {list(self._exchanges)}")
        return self._exchanges[key]

    def execute(self, req: OrderRequest) -> dict[str, Any]:
        """執行開單請求，回傳交易所原始回應"""
        exchange = self.get_exchange(req.exchange)

        payload: dict[str, Any] = {
            "symbol": req.symbol,
            "side": req.side.value,
            "orderType": req.order_type.value,
            "qty": req.qty,
        }
        if req.trade_side is not None:
            payload["tradeSide"] = req.trade_side.value
        if req.position_id is not None:
            payload["positionId"] = req.position_id
        if req.order_type == OrderType.LIMIT:
            if req.price is None:
                raise ValueError("LIMIT 訂單必須提供 price")
            payload["price"] = req.price
            payload["effect"] = req.effect or "GTC"
        payload.update(req.extra)

        return exchange.place_order(payload)

    def get_account(self, exchange_name: str) -> dict[str, Any]:
        return self.get_exchange(exchange_name).get_account()

    def get_positions(self, exchange_name: str, symbol: str | None = None) -> list[dict[str, Any]]:
        return self.get_exchange(exchange_name).get_pending_positions(symbol)

    def get_orders(self, exchange_name: str, symbol: str | None = None) -> list[dict[str, Any]]:
        return self.get_exchange(exchange_name).get_pending_orders(symbol)
