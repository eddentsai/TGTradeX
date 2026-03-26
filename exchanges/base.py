"""
交易所抽象介面

所有交易所 SDK 都應實作此介面，確保 dispatcher 可統一呼叫。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseExchange(ABC):
    """交易所統一介面"""

    @property
    @abstractmethod
    def name(self) -> str:
        """交易所名稱，例如 'bitunix'"""

    # ── 帳戶 ──────────────────────────────────────────────────────────────────

    @abstractmethod
    def get_account(self) -> dict[str, Any]:
        """取得帳戶資訊（餘額、未實現盈虧等）"""

    # ── 持倉 / 訂單查詢 ───────────────────────────────────────────────────────

    @abstractmethod
    def get_pending_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """取得持倉中的倉位"""

    @abstractmethod
    def get_pending_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """取得未完成訂單"""

    # ── 下單 ──────────────────────────────────────────────────────────────────

    @abstractmethod
    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        """送出訂單，回傳交易所原始回應"""

    @abstractmethod
    def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        """取消訂單"""
