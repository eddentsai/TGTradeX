"""
Bitunix 交易所 Adapter

將 BitunixClient 包裝為 BaseExchange 介面。
"""
from __future__ import annotations

from typing import Any

from exchanges.base import BaseExchange
from exchanges.bitunix import BitunixClient


class BitunixExchange(BaseExchange):
    """BaseExchange 的 Bitunix 實作"""

    def __init__(self, api_key: str, secret_key: str, **kwargs: Any) -> None:
        self._client = BitunixClient(api_key=api_key, secret_key=secret_key, **kwargs)

    @property
    def name(self) -> str:
        return "bitunix"

    def get_account(self) -> dict[str, Any]:
        if self._client.futures_private is None:
            raise RuntimeError("未設定 Bitunix credentials")
        return self._client.futures_private.get_account()

    def get_pending_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if self._client.futures_private is None:
            raise RuntimeError("未設定 Bitunix credentials")
        return self._client.futures_private.get_pending_positions(symbol=symbol)

    def get_pending_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if self._client.futures_private is None:
            raise RuntimeError("未設定 Bitunix credentials")
        return self._client.futures_private.get_pending_orders(symbol=symbol)

    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._client.futures_private is None:
            raise RuntimeError("未設定 Bitunix credentials")
        return self._client.futures_private.place_order(payload)

    def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        if self._client.futures_private is None:
            raise RuntimeError("未設定 Bitunix credentials")
        return self._client.futures_private.cancel_orders(
            symbol=symbol, order_list=[{"orderId": order_id}]
        )

    def get_klines(self, symbol: str, interval: str, limit: int = 250) -> list[dict[str, Any]]:
        """
        回傳由舊到新的 K 線列表。
        Bitunix 欄位：time, open, high, low, close, volume
        """
        result = self._client.futures_public.get_kline(
            symbol=symbol, interval=interval, limit=limit
        )
        # 確保由舊到新（有些交易所回傳由新到舊）
        if len(result) >= 2:
            if result[0].get("time", 0) > result[-1].get("time", 0):
                result = list(reversed(result))
        return result

    def get_qty_precision(self, symbol: str) -> int:
        """從 Bitunix 合約規格取得數量精度（basePrecision 欄位）"""
        pairs = self._client.futures_public.get_trading_pairs(symbol)
        if not pairs:
            raise ValueError(f"找不到交易對: {symbol}")
        return int(pairs[0].get("basePrecision", 3))
