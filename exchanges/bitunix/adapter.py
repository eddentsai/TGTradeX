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
        return self._client.futures_private.cancel_order(order_id=order_id, symbol=symbol)
