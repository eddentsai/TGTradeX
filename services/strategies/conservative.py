"""
保守策略（高波動市場使用）

不開新倉；若有持倉則發出平倉信號。
"""
from __future__ import annotations

from services.indicators import IndicatorSnapshot
from services.strategies.base import ActivePosition, BaseStrategy, Signal


class ConservativeStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return "conservative"

    def on_candle(self, snap: IndicatorSnapshot, position: ActivePosition | None) -> Signal:
        if position is not None:
            return Signal(
                action="close",
                reason=f"高波動市場，平倉觀望 price={snap.close:.2f}",
            )
        return Signal(action="hold", reason="高波動市場，不開新倉")
