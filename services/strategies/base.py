"""
策略抽象介面與共用資料結構
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from services.indicators import IndicatorSnapshot


@dataclass
class ActivePosition:
    """Runner 維護的本地持倉狀態"""
    position_id: str          # 交易所倉位 ID（開倉後從交易所取得）
    side: str                 # "BUY" | "SELL"
    entry_price: float
    qty: str
    stop_loss: float
    take_profit: float
    strategy_name: str = ""
    exchange: str = ""        # 交易所名稱，例如 "bitunix" | "binance"
    interval: str = ""        # K 線週期，例如 "15m" | "1h"
    peak_price: float | None = None  # 移動止損用：做多記最高價，做空記最低價


@dataclass
class Signal:
    """策略回傳的交易信號"""
    action: str               # "open_long" | "open_short" | "close" | "hold" | "trail_sl"
    order_type: str = "MARKET"
    price: str | None = None
    stop_loss: float | None = None    # 開倉時設定，由 runner 存入 ActivePosition
    take_profit: float | None = None
    reason: str = ""


class BaseStrategy(ABC):
    """所有策略的抽象基類"""

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名稱"""

    @abstractmethod
    def on_candle(
        self,
        snap: IndicatorSnapshot,
        position: ActivePosition | None,
    ) -> Signal:
        """
        每根 K 線結束時呼叫。
        - position=None  → 目前無持倉，判斷是否開倉
        - position≠None  → 目前有持倉，判斷是否平倉
        """
