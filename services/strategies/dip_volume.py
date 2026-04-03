"""
急跌爆量反彈 / 急漲爆量回落策略（雙向，高波動市場使用）

做多入場條件：
  - 近 5 根 K 線跌幅 > 3%，成交量 > 平均 2 倍
  - 出現止跌信號（無新低、下影線、量縮）
  - SL: -1.5%，TP: +3.0%

做空入場條件：
  - 近 5 根 K 線漲幅 > 3%，成交量 > 平均 2 倍
  - 出現頂部信號（無新高、上影線、量縮）
  - SL: +1.5%，TP: -3.0%

出場：EMA20 附近出場 / 時間止損 10 根 K 線
"""

from __future__ import annotations

from services.indicators import Candle, IndicatorSnapshot
from services.strategies.base import ActivePosition, BaseStrategy, Signal

_TIME_WINDOW = 5   # 監測窗口（根數）
_DROP_THRESH = -3.0  # 跌幅閾值（%）
_PUMP_THRESH = 3.0   # 漲幅閾值（%）
_VOL_MULT = 2.0    # 成交量倍數
_SL_PCT = 0.985    # 做多止損：-1.5%
_TP_PCT = 1.030    # 做多止盈：+3.0%
_SL_SHORT_PCT = 1.015  # 做空止損：+1.5%
_TP_SHORT_PCT = 0.970  # 做空止盈：-3.0%
_MAX_HOLD = 10     # 時間止損（根數）


class DipVolumeStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return "dip_volume"

    def on_candle(
        self, snap: IndicatorSnapshot, position: ActivePosition | None
    ) -> Signal:
        if position is not None:
            return self._check_exit(snap, position)
        return self._check_entry(snap)

    # ── 入場 ──────────────────────────────────────────────────────────────────

    def _check_entry(self, snap: IndicatorSnapshot) -> Signal:
        klines = snap.klines
        close = snap.close

        if len(klines) < 60:
            return Signal(action="hold", reason="K線數據不足（需 60 根）")

        recent_klines = klines[-_TIME_WINDOW:]
        first_open = recent_klines[0].open

        if first_open <= 0:
            return Signal(action="hold", reason="K線 open 異常（= 0）")

        price_change = (recent_klines[-1].close - first_open) / first_open * 100

        baseline_klines = klines[-60:-_TIME_WINDOW]
        baseline_count = len(baseline_klines)
        if baseline_count == 0:
            return Signal(action="hold", reason="基準成交量數據不足")

        avg_volume = sum(k.volume for k in baseline_klines) / baseline_count
        recent_volume = sum(k.volume for k in recent_klines)
        volume_ratio = (
            recent_volume / (avg_volume * _TIME_WINDOW) if avg_volume > 0 else 0.0
        )

        # ── 做多：急跌爆量反彈 ──────────────────────────────────────────────────
        if price_change <= _DROP_THRESH and volume_ratio >= _VOL_MULT:
            if self._confirm_stabilization(klines):
                stop_loss = close * _SL_PCT
                take_profit = close * _TP_PCT
                return Signal(
                    action="open_long",
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    reason=(
                        f"急跌爆量反彈 跌幅={price_change:.1f}% "
                        f"量比={volume_ratio:.1f}x "
                        f"SL={stop_loss:.4f} TP={take_profit:.4f}"
                    ),
                )

        # ── 做空：急漲爆量回落 ──────────────────────────────────────────────────
        if price_change >= _PUMP_THRESH and volume_ratio >= _VOL_MULT:
            if self._confirm_topping(klines):
                stop_loss = close * _SL_SHORT_PCT
                take_profit = close * _TP_SHORT_PCT
                return Signal(
                    action="open_short",
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    reason=(
                        f"急漲爆量回落 漲幅={price_change:.1f}% "
                        f"量比={volume_ratio:.1f}x "
                        f"SL={stop_loss:.4f} TP={take_profit:.4f}"
                    ),
                )

        return Signal(
            action="hold",
            reason=f"未達入場條件 跌幅={price_change:.1f}% 量比={volume_ratio:.1f}x",
        )

    def _confirm_stabilization(self, klines: list[Candle]) -> bool:
        """做多確認：價格企穩（無新低 + 下影線或量縮）"""
        if len(klines) < 2:
            return False

        last = klines[-1]
        prev = klines[-2]

        no_new_low = last.low >= prev.low
        body = abs(last.close - last.open)
        lower_shadow = min(last.close, last.open) - last.low

        if body > 0:
            has_lower_shadow = lower_shadow > body * 1.5
        else:
            candle_range = last.high - last.low
            has_lower_shadow = candle_range > 0 and lower_shadow > candle_range * 0.3

        volume_decreasing = last.volume < prev.volume
        return no_new_low and (has_lower_shadow or volume_decreasing)

    def _confirm_topping(self, klines: list[Candle]) -> bool:
        """做空確認：頂部信號（無新高 + 上影線或量縮）"""
        if len(klines) < 2:
            return False

        last = klines[-1]
        prev = klines[-2]

        no_new_high = last.high <= prev.high
        body = abs(last.close - last.open)
        upper_shadow = last.high - max(last.close, last.open)

        if body > 0:
            has_upper_shadow = upper_shadow > body * 1.5
        else:
            candle_range = last.high - last.low
            has_upper_shadow = candle_range > 0 and upper_shadow > candle_range * 0.3

        volume_decreasing = last.volume < prev.volume
        return no_new_high and (has_upper_shadow or volume_decreasing)

    # ── 出場 ──────────────────────────────────────────────────────────────────

    def _check_exit(self, snap: IndicatorSnapshot, pos: ActivePosition) -> Signal:
        close = snap.close
        ema20 = snap.ema20

        if pos.side == "SELL":
            # 做空
            if close >= pos.stop_loss:
                return Signal(
                    action="close",
                    reason=f"觸發止損 price={close:.4f} SL={pos.stop_loss:.4f}",
                )
            if close <= pos.take_profit:
                return Signal(
                    action="close",
                    reason=f"觸發止盈 price={close:.4f} TP={pos.take_profit:.4f}",
                )
            # 跌回 EMA20 附近出場
            if ema20 is not None and close <= ema20 * 1.002:
                return Signal(
                    action="close",
                    reason=f"回落至 EMA20={ema20:.4f} price={close:.4f}",
                )
        else:
            # 做多
            if close <= pos.stop_loss:
                return Signal(
                    action="close",
                    reason=f"觸發止損 price={close:.4f} SL={pos.stop_loss:.4f}",
                )
            if close >= pos.take_profit:
                return Signal(
                    action="close",
                    reason=f"觸發止盈 price={close:.4f} TP={pos.take_profit:.4f}",
                )
            if ema20 is not None and close >= ema20 * 0.998:
                return Signal(
                    action="close",
                    reason=f"反彈至 EMA20={ema20:.4f} price={close:.4f}",
                )

        # 時間止損（多空通用）
        if len(snap.klines) > 0:
            held_candles = self._estimate_held_candles(snap.klines, pos.entry_price)
            if held_candles >= _MAX_HOLD:
                return Signal(
                    action="close",
                    reason=f"時間止損：持倉超過 {_MAX_HOLD} 根 K 線未達目標",
                )

        return Signal(action="hold", reason=f"持倉中 price={close:.4f}")

    def _estimate_held_candles(self, klines: list[Candle], entry_price: float) -> int:
        """
        從 K 線序列尾端往前找，估算自入場以來經過的根數。
        以「收盤價最接近入場價的那根」作為入場點。
        """
        for i in range(len(klines) - 1, -1, -1):
            if abs(klines[i].close - entry_price) / entry_price <= 0.005:
                return len(klines) - 1 - i
        return 0
