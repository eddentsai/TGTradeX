"""
成交量分佈策略 VA/POC（震蕩市場使用）

入場條件：
  - 震蕩市場中
  - 計算過去 100 根 K 線的成交量分佈
  - VAL-POC 價差 ≥ 0.5%（成交量分佈需有足夠價差）
  - 價格接近 VAL（價值區下限）±1.5%
  - RSI < 50

出場條件：
  - 價格接近 POC（成交量最大的價格點）±1.5%
  - 止損：VAL 下方 3%
  - 止盈：POC 價格
"""
from __future__ import annotations

from services.indicators import IndicatorSnapshot
from services.strategies.base import ActivePosition, BaseStrategy, Signal


class VolumeProfileStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return "volume_profile"

    def on_candle(self, snap: IndicatorSnapshot, position: ActivePosition | None) -> Signal:
        if position is not None:
            return self._check_exit(snap, position)
        return self._check_entry(snap)

    # ── 入場 ──────────────────────────────────────────────────────────────────

    def _check_entry(self, snap: IndicatorSnapshot) -> Signal:
        close = snap.close
        val   = snap.val
        poc   = snap.poc
        rsi   = snap.rsi

        if val is None or poc is None or rsi is None:
            return Signal(action="hold", reason="指標資料不足（vol profile 或 RSI）")

        # VAL-POC 最小價差檢查：價差不足代表成交量分佈過度集中，區間不清晰
        poc_val_spread = (poc - val) / val if val > 0 else 0
        if poc_val_spread < 0.005:
            return Signal(
                action="hold",
                reason=f"VAL-POC 價差過小 ({poc_val_spread*100:.2f}%)，成交量分佈不清晰",
            )

        near_val = abs(close - val) / val <= 0.015
        rsi_ok   = rsi < 50.0

        if near_val and rsi_ok:
            stop_loss   = val * 0.97
            take_profit = poc
            return Signal(
                action="open_long",
                stop_loss=stop_loss,
                take_profit=take_profit,
                reason=(
                    f"接近 VAL={val:.2f} RSI={rsi:.1f} "
                    f"POC={poc:.2f} SL={stop_loss:.2f} "
                    f"spread={poc_val_spread*100:.2f}%"
                ),
            )

        return Signal(
            action="hold",
            reason=f"未達入場條件 near_val={near_val} rsi_ok={rsi_ok} (RSI={rsi:.1f})",
        )

    # ── 出場 ──────────────────────────────────────────────────────────────────

    def _check_exit(self, snap: IndicatorSnapshot, pos: ActivePosition) -> Signal:
        close = snap.close
        poc   = snap.poc

        # 止損
        if close <= pos.stop_loss:
            return Signal(
                action="close",
                reason=f"觸發止損 price={close:.2f} SL={pos.stop_loss:.2f}",
            )

        # 止盈（接近 POC）
        if poc is not None and abs(close - poc) / poc <= 0.015:
            return Signal(
                action="close",
                reason=f"接近 POC={poc:.2f} price={close:.2f}，達到止盈目標",
            )

        return Signal(action="hold", reason=f"持倉中 price={close:.2f}")
