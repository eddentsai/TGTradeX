"""
斐波那契回調策略（上升趨勢使用）

入場條件：
  - 上升趨勢中（EMA20 > EMA50）
  - 找到有效波段（先漲後跌的結構，波幅 > 5%）
  - 價格回調至斐波那契關鍵位（0.618 / 0.5 / 0.382）
  - 出現錘子線反轉形態

出場條件：
  - 止盈：前高（100% 回測位）
  - 止損：0.786 回調位下方

注意：分批出場（1.0 / 1.272 / 1.618 延伸位）待 Signal 支援 quantity_pct 後再啟用。
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
_LOOKBACK = 50  # 尋找高低點的回看週期
_MIN_SWING_PCT = 0.05  # 最小波幅 5%


class FibonacciStrategy(BaseStrategy):

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

        if ema20 <= ema50:
            return Signal(
                action="hold",
                reason=f"非上升趨勢 EMA20={ema20:.4f} <= EMA50={ema50:.4f}",
            )

        swing = self._find_swing_points(snap.klines)
        if swing is None:
            return Signal(action="hold", reason="無法識別有效波段高低點")

        high, low = swing
        fib_levels = self._calculate_fib_levels(high, low)

        for fib_key in _ENTRY_FIBS:
            fib_price = fib_levels[f"retrace_{fib_key}"]

            if abs(close - fib_price) / fib_price <= _CONFLUENCE_TOL:
                # BUG FIX #2：確保有足夠 K 線才做反轉確認
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

    def _find_swing_points(self, klines: list[Candle]) -> tuple[float, float] | None:
        """
        找出有效波段高低點。

        BUG FIX #1：必須確認「高點在低點之前」的結構（先漲後回調），
        才符合斐波那契做多的前提。
        """
        if len(klines) < _LOOKBACK:
            return None

        recent = klines[-_LOOKBACK:]

        # 找最高點的位置
        high_idx = max(range(len(recent)), key=lambda i: recent[i].high)
        high_val = recent[high_idx].high

        # 低點必須在高點之後（高點出現後才開始回調）
        if high_idx >= len(recent) - 2:
            # 高點太靠近末端，回調還沒形成
            return None

        post_high = recent[high_idx:]
        low_val = min(c.low for c in post_high)

        # 確保波幅足夠
        if low_val <= 0 or (high_val - low_val) / low_val < _MIN_SWING_PCT:
            return None

        return high_val, low_val

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
        """
        確認反轉信號（錘子線）。

        BUG FIX #2：需至少 3 根 K 線才進行確認。
        """
        if len(klines) < 3:
            return False

        last = klines[-1]

        # 價格觸及斐波那契位
        if not (last.low <= fib_level <= last.high):
            return False

        # 錘子線：下影線 >= 實體 2 倍
        body = abs(last.close - last.open)
        lower_shadow = min(last.close, last.open) - last.low

        if body > 0:
            return lower_shadow >= body * 2.0

        # 十字星也算（下影線佔整根 K 線 30% 以上）
        candle_range = last.high - last.low
        return candle_range > 0 and lower_shadow >= candle_range * 0.3

    # ── 出場 ──────────────────────────────────────────────────────────────────

    def _check_exit(self, snap: IndicatorSnapshot, pos: ActivePosition) -> Signal:
        close = snap.close

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
