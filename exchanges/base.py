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

    @abstractmethod
    def cancel_all_orders(self, symbol: str) -> None:
        """取消該交易對所有掛單（平倉前呼叫，清除 SL/TP 條件單）"""

    @abstractmethod
    def place_sl_tp_orders(
        self,
        symbol: str,
        side: str,           # 倉位方向 "BUY" | "SELL"
        qty: str,
        sl_price: float,
        tp_price: float,
        position_id: str = "",
    ) -> None:
        """對現有倉位補掛交易所層面的 SL/TP 條件單（服務重啟後保護未追蹤倉位用）"""

    # ── 市場資料 ──────────────────────────────────────────────────────────────

    @abstractmethod
    def get_klines(self, symbol: str, interval: str, limit: int = 250) -> list[dict[str, Any]]:
        """取得 K 線資料（由舊到新），每筆至少包含 time, open, high, low, close, volume"""

    @abstractmethod
    def get_qty_precision(self, symbol: str) -> int:
        """回傳該交易對的數量小數位數（例如 BTC=3, ETH=2, SOL=1）"""

    @abstractmethod
    def get_tickers(self) -> list[dict]:
        """
        取得所有合約的行情摘要，用於自動幣種掃描。

        每筆至少包含以下欄位（由子類正規化）：
          symbol     : str   交易對名稱，例如 "BTCUSDT"
          last_price : float 最新成交價
          quote_vol  : float 24h 計價貨幣成交量（USDT）
          base_vol   : float 24h 標的資產成交量
          high       : float 24h 最高價
          low        : float 24h 最低價
          change_pct : float 24h 漲跌幅（%），例如 3.5 代表 +3.5%
        """
