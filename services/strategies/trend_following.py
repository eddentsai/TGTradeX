"""
趨勢跟隨策略（上升趨勢使用）

入場條件：
  - 上升趨勢中
  - 價格回調至 EMA20 ±2%
  - RSI 30–65
  - 價格在布林帶 20%–60% 位置

出場條件：
  - EMA20 開始向下（當根 EMA20 < 上一根 EMA20）
  - RSI > 75
  - 止損：EMA50 下方 3%
  - 止盈：入場價 +5%
"""
from __future__ import annotations

from services.indicators import IndicatorSnapshot
from services.strategies.base import ActivePosition, BaseStrategy, Signal


class TrendFollowingStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return "trend_following"

    def on_candle(self, snap: IndicatorSnapshot, position: ActivePosition | None) -> Signal:
        if position is not None:
            return self._check_exit(snap, position)
        return self._check_entry(snap)

    # ── 入場 ──────────────────────────────────────────────────────────────────

    def _check_entry(self, snap: IndicatorSnapshot) -> Signal:
        close    = snap.close
        ema20    = snap.ema20
        ema50    = snap.ema50
        rsi      = snap.rsi
        bb_pos   = snap.bb_position

        if ema20 is None or ema50 is None or rsi is None or bb_pos is None:
            return Signal(action="hold", reason="指標資料不足")

        near_ema20 = abs(close - ema20) / ema20 <= 0.02
        rsi_ok     = 30.0 <= rsi <= 65.0
        bb_ok      = 0.20 <= bb_pos <= 0.60

        if near_ema20 and rsi_ok and bb_ok:
            stop_loss   = ema50 * 0.97
            take_profit = close * 1.05
            return Signal(
                action="open_long",
                stop_loss=stop_loss,
                take_profit=take_profit,
                reason=(
                    f"趨勢回調入場 "
                    f"EMA20={ema20:.2f} RSI={rsi:.1f} BB位置={bb_pos:.2f} "
                    f"SL={stop_loss:.2f} TP={take_profit:.2f}"
                ),
            )

        return Signal(action="hold", reason=f"未達入場條件 near_ema20={near_ema20} rsi_ok={rsi_ok} bb_ok={bb_ok}")

    # ── 出場 ──────────────────────────────────────────────────────────────────

    def _check_exit(self, snap: IndicatorSnapshot, pos: ActivePosition) -> Signal:
        close      = snap.close
        ema20      = snap.ema20
        ema20_prev = snap.ema20_prev
        rsi        = snap.rsi

        # 止損
        if close <= pos.stop_loss:
            return Signal(
                action="close",
                reason=f"觸發止損 price={close:.2f} SL={pos.stop_loss:.2f}",
            )

        # 止盈
        if close >= pos.take_profit:
            return Signal(
                action="close",
                reason=f"觸發止盈 price={close:.2f} TP={pos.take_profit:.2f}",
            )

        # EMA20 開始向下
        if ema20 is not None and ema20_prev is not None and ema20 < ema20_prev:
            return Signal(
                action="close",
                reason=f"EMA20 下彎 ({ema20:.2f} < {ema20_prev:.2f})",
            )

        # RSI 過熱
        if rsi is not None and rsi > 75.0:
            return Signal(action="close", reason=f"RSI 過熱 ({rsi:.1f} > 75)")

        return Signal(action="hold", reason=f"持倉中 price={close:.2f}")
