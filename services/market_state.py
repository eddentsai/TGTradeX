"""
市場狀態識別

根據技術指標自動判斷當前市場狀態，供策略選擇器使用。
"""
from __future__ import annotations

from enum import Enum, auto

from services.indicators import IndicatorSnapshot


class MarketState(Enum):
    UPTREND        = auto()   # 上升趨勢
    DOWNTREND      = auto()   # 下降趨勢
    RANGING        = auto()   # 震蕩市場
    HIGH_VOLATILITY = auto()  # 高波動市場


def classify_market(snap: IndicatorSnapshot) -> MarketState:
    """
    判斷邏輯（優先順序由高到低）：

    1. 高波動：近期波動率 > 3%
    2. 上升趨勢：ADX > 25 且 EMA20 > EMA50 且線性回歸斜率 > 0
    3. 下降趨勢：ADX > 25 且 EMA20 < EMA50 且線性回歸斜率 < 0
    4. 震蕩：ADX < 20 且布林帶寬度 < 3%
    5. 其餘：預設震蕩
    """
    # ── 優先判斷高波動 ─────────────────────────────────────────────────────────
    if snap.volatility_pct is not None and snap.volatility_pct > 3.0:
        return MarketState.HIGH_VOLATILITY

    adx      = snap.adx
    ema20    = snap.ema20
    ema50    = snap.ema50
    lr_slope = snap.lr_slope_pct
    bb_width = snap.bb_width_pct

    # ── 趨勢市場 ───────────────────────────────────────────────────────────────
    if adx is not None and adx > 25 and ema20 is not None and ema50 is not None:
        if ema20 > ema50 and lr_slope is not None and lr_slope > 0:
            return MarketState.UPTREND
        if ema20 < ema50 and lr_slope is not None and lr_slope < 0:
            return MarketState.DOWNTREND

    # ── 震蕩市場 ───────────────────────────────────────────────────────────────
    if adx is not None and adx < 20 and bb_width is not None and bb_width < 3.0:
        return MarketState.RANGING

    return MarketState.RANGING


STATE_LABELS = {
    MarketState.UPTREND:         "上升趨勢",
    MarketState.DOWNTREND:       "下降趨勢",
    MarketState.RANGING:         "震蕩市場",
    MarketState.HIGH_VOLATILITY: "高波動",
}
