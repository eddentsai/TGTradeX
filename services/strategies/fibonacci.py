"""
斐波那契回調策略（雙向）

做多入場條件：
  - 上升趨勢中（EMA20 > EMA50）
  - 找到有效波段（先漲後跌的結構，波幅 > 5%）
  - 價格回調至斐波那契關鍵位（0.618 / 0.5 / 0.382）
  - 出現錘子線反轉形態
  - 止盈：前高，止損：0.786 回調位下方

做空入場條件：
  - 下降趨勢中（EMA20 < EMA50）
  - 找到有效波段（先跌後反彈的結構，波幅 > 5%）
  - 價格反彈至斐波那契關鍵位（0.382 / 0.5 / 0.618）
  - 出現射擊之星拒絕形態
  - 止盈：前低，止損：0.786 反彈位上方
"""

from __future__ import annotations

from services.indicators import Candle, IndicatorSnapshot
from services.strategies.base import ActivePosition, BaseStrategy, Signal

# 斐波那契關鍵比率（用整數 key 避免浮點精度問題）
_FIB_RETRACE: dict[int, float] = {
    236: 0.236,
    382: 0.382,
    500: 0.500,
    618: 0.618,
    786: 0.786,
}
_FIB_EXTEND: dict[int, float] = {
    1000: 1.000,
    1272: 1.272,
    1618: 1.618,
}
_ENTRY_FIBS = [618, 500, 382]  # 優先進場位（整數 key）
_CONFLUENCE_TOL = 0.005  # 匯合區容差 0.5%
_LOOKBACK = 50          # 尋找高低點的回看週期（預設，適合 1h/4h）
_MIN_SWING_PCT = 0.05   # 最小波幅 5%（預設）


class FibonacciStrategy(BaseStrategy):

    def __init__(
        self,
        lookback: int = _LOOKBACK,
        min_swing_pct: float = _MIN_SWING_PCT,
    ) -> None:
        self._lookback = lookback
        self._min_swing_pct = min_swing_pct

    @property
    def name(self) -> str:
        return "fibonacci"

    def on_candle(
        self, snap: IndicatorSnapshot, position: ActivePosition | None
    ) -> Signal:
        if position is not None:
            return self._check_exit(snap, position)
        return self._check_entry(snap)

    # ── 入場 ──────────────────────────────────────────────────────────────────

    def _check_entry(self, snap: IndicatorSnapshot) -> Signal:
        close = snap.close
        ema20 = snap.ema20
        ema50 = snap.ema50

        if ema20 is None or ema50 is None:
            return Signal(action="hold", reason="EMA 資料不足")

        # ── 做多：上升趨勢 ──────────────────────────────────────────────────────
        if ema20 > ema50:
            swing = self._find_swing_high_low(snap.klines)
            if swing is None:
                return Signal(action="hold", reason="無法識別有效波段高低點")

            high, low = swing
            fib_levels = self._calculate_fib_levels(high, low)

            for fib_key in _ENTRY_FIBS:
                fib_price = fib_levels[f"retrace_{fib_key}"]
                if abs(close - fib_price) / fib_price <= _CONFLUENCE_TOL:
                    if self._check_reversal_confirmation(snap.klines, fib_price):
                        stop_loss = fib_levels["retrace_786"]
                        take_profit = high
                        return Signal(
                            action="open_long",
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            reason=(
                                f"斐波那契 {fib_key/1000:.3f} 回調 "
                                f"price={close:.4f} fib={fib_price:.4f} "
                                f"high={high:.4f} low={low:.4f} "
                                f"SL={stop_loss:.4f} TP={take_profit:.4f}"
                            ),
                        )
            return Signal(action="hold", reason="未在斐波那契關鍵位或無反轉確認")

        # ── 做空：下降趨勢 ──────────────────────────────────────────────────────
        swing = self._find_swing_low_high(snap.klines)
        if swing is None:
            return Signal(action="hold", reason=f"下降趨勢但無有效反彈波段可做空 EMA20={ema20:.4f} <= EMA50={ema50:.4f}")

        low, high = swing
        fib_levels = self._calculate_fib_levels(high, low)

        for fib_key in _ENTRY_FIBS:
            fib_price = fib_levels[f"retrace_{fib_key}"]
            if abs(close - fib_price) / fib_price <= _CONFLUENCE_TOL:
                if self._check_short_reversal_confirmation(snap.klines, fib_price):
                    stop_loss = fib_levels["retrace_786"]  # 反彈 0.786 以上止損
                    take_profit = low                       # 目標前低
                    return Signal(
                        action="open_short",
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        reason=(
                            f"斐波那契空 {fib_key/1000:.3f} 反彈 "
                            f"price={close:.4f} fib={fib_price:.4f} "
                            f"low={low:.4f} high={high:.4f} "
                            f"SL={stop_loss:.4f} TP={take_profit:.4f}"
                        ),
                    )

        return Signal(
            action="hold",
            reason=f"下降趨勢未在斐波那契關鍵位或無射擊之星確認 EMA20={ema20:.4f} <= EMA50={ema50:.4f}",
        )

    def _find_swing_high_low(self, klines: list[Candle]) -> tuple[float, float] | None:
        """
        做多用：找出「先高後低」的有效波段（高點在前，低點在後）。
        回傳 (high, low)
        """
        if len(klines) < self._lookback:
            return None

        recent = klines[-self._lookback:]
        high_idx = max(range(len(recent)), key=lambda i: recent[i].high)
        high_val = recent[high_idx].high

        if high_idx >= len(recent) - 2:
            return None

        post_high = recent[high_idx:]
        low_val = min(c.low for c in post_high)

        if low_val <= 0 or (high_val - low_val) / low_val < self._min_swing_pct:
            return None

        return high_val, low_val

    def _find_swing_low_high(self, klines: list[Candle]) -> tuple[float, float] | None:
        """
        做空用：找出「先低後高」的有效波段（低點在前，反彈在後）。
        回傳 (low, high)
        """
        if len(klines) < self._lookback:
            return None

        recent = klines[-self._lookback:]
        low_idx = min(range(len(recent)), key=lambda i: recent[i].low)
        low_val = recent[low_idx].low

        if low_idx >= len(recent) - 2:
            return None

        post_low = recent[low_idx:]
        high_val = max(c.high for c in post_low)

        if low_val <= 0 or (high_val - low_val) / low_val < self._min_swing_pct:
            return None

        return low_val, high_val

    def _calculate_fib_levels(self, high: float, low: float) -> dict[str, float]:
        """計算斐波那契水平。使用整數 key 避免浮點精度問題。"""
        diff = high - low
        levels = {}

        # 回調位（從高點往下）
        for key, ratio in _FIB_RETRACE.items():
            levels[f"retrace_{key}"] = high - diff * ratio

        # 延伸位（從高點往上）
        for key, ratio in _FIB_EXTEND.items():
            levels[f"extend_{key}"] = high + diff * (ratio - 1.0)

        return levels

    def _check_reversal_confirmation(
        self, klines: list[Candle], fib_level: float
    ) -> bool:
        """做多反轉確認：錘子線（下影線 >= 實體 2 倍，或十字星）"""
        if len(klines) < 3:
            return False

        last = klines[-1]

        if not (last.low <= fib_level <= last.high):
            return False

        body = abs(last.close - last.open)
        lower_shadow = min(last.close, last.open) - last.low

        if body > 0:
            return lower_shadow >= body * 2.0

        candle_range = last.high - last.low
        return candle_range > 0 and lower_shadow >= candle_range * 0.3

    def _check_short_reversal_confirmation(
        self, klines: list[Candle], fib_level: float
    ) -> bool:
        """做空反轉確認：射擊之星（上影線 >= 實體 2 倍，或倒十字星）"""
        if len(klines) < 3:
            return False

        last = klines[-1]

        if not (last.low <= fib_level <= last.high):
            return False

        body = abs(last.close - last.open)
        upper_shadow = last.high - max(last.close, last.open)

        if body > 0:
            return upper_shadow >= body * 2.0

        candle_range = last.high - last.low
        return candle_range > 0 and upper_shadow >= candle_range * 0.3

    # ── 出場 ──────────────────────────────────────────────────────────────────

    def _check_exit(self, snap: IndicatorSnapshot, pos: ActivePosition) -> Signal:
        close = snap.close

        if pos.side == "SELL":
            # 做空：價格上漲超過 SL 止損，下跌達到 TP 止盈
            if close >= pos.stop_loss:
                return Signal(
                    action="close",
                    reason=f"觸發止損 price={close:.4f} SL={pos.stop_loss:.4f}",
                )
            if close <= pos.take_profit:
                return Signal(
                    action="close",
                    reason=f"達到前低目標 price={close:.4f} TP={pos.take_profit:.4f}",
                )
        else:
            # 做多：價格下跌低於 SL 止損，上漲達到 TP 止盈
            if close <= pos.stop_loss:
                return Signal(
                    action="close",
                    reason=f"觸發止損 price={close:.4f} SL={pos.stop_loss:.4f}",
                )
            if close >= pos.take_profit:
                return Signal(
                    action="close",
                    reason=f"達到前高目標 price={close:.4f} TP={pos.take_profit:.4f}",
                )

        return Signal(action="hold", reason=f"持倉中 price={close:.4f}")
