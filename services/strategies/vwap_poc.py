"""
VWAP/POC 均值回歸策略（雙向，震盪市場使用）

做多入場條件：
  - 價格跌至 VWAP -1.5σ
  - RSI < 40（超賣）
  - R:R >= 1.5，目標 POC 或 VWAP，止損 VWAP -2.5σ

做空入場條件：
  - 價格漲至 VWAP +1.5σ
  - RSI > 60（超買）
  - R:R >= 1.5，目標 POC 或 VWAP，止損 VWAP +2.5σ
"""

from __future__ import annotations

import math

from services.indicators import IndicatorSnapshot
from services.strategies.base import ActivePosition, BaseStrategy, Signal


class VwapPocStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return "vwap_poc"

    def __init__(self):
        self.vwap_period = 24  # VWAP 計算週期（根數）
        self.entry_band = 1.5  # 進場：VWAP - N 個標準差
        self.stop_loss_band = 2.5  # 止損：VWAP - N 個標準差

    def on_candle(
        self, snap: IndicatorSnapshot, position: ActivePosition | None
    ) -> Signal:
        if position is not None:
            return self._check_exit(snap, position)
        return self._check_entry(snap)

    # ── 入場 ──────────────────────────────────────────────────────────────────

    def _check_entry(self, snap: IndicatorSnapshot) -> Signal:
        close = snap.close
        poc = snap.poc
        rsi = snap.rsi

        if poc is None or rsi is None:
            return Signal(action="hold", reason="POC 或 RSI 資料不足")

        vwap, std = self._get_vwap_std(snap)
        if vwap is None or std is None:
            return Signal(action="hold", reason="VWAP 計算資料不足")

        lower_band = vwap - self.entry_band * std
        upper_band = vwap + self.entry_band * std
        sl_lower   = vwap - self.stop_loss_band * std
        sl_upper   = vwap + self.stop_loss_band * std

        # ── 做多：超賣 ──────────────────────────────────────────────────────────
        if close <= lower_band and rsi < 40:
            risk = abs(close - sl_lower)
            if risk <= 0:
                return Signal(action="hold", reason="止損距離為零，跳過")

            tp_candidate = max(poc, vwap)
            reward = abs(tp_candidate - close)

            if reward / risk >= 1.5:
                return Signal(
                    action="open_long",
                    stop_loss=sl_lower,
                    take_profit=tp_candidate,
                    reason=(
                        f"VWAP 超賣回歸 price={close:.4f} "
                        f"VWAP={vwap:.4f} lower_band={lower_band:.4f} "
                        f"POC={poc:.4f} TP={tp_candidate:.4f} RSI={rsi:.1f} R:R={reward/risk:.2f}"
                    ),
                )
            return Signal(action="hold", reason=f"盈虧比不足 R:R={reward/risk:.2f} < 1.5")

        # ── 做空：超買 ──────────────────────────────────────────────────────────
        if close >= upper_band and rsi > 60:
            risk = abs(sl_upper - close)
            if risk <= 0:
                return Signal(action="hold", reason="止損距離為零，跳過")

            tp_candidate = min(poc, vwap)
            reward = abs(close - tp_candidate)

            if reward / risk >= 1.5:
                return Signal(
                    action="open_short",
                    stop_loss=sl_upper,
                    take_profit=tp_candidate,
                    reason=(
                        f"VWAP 超買回歸 price={close:.4f} "
                        f"VWAP={vwap:.4f} upper_band={upper_band:.4f} "
                        f"POC={poc:.4f} TP={tp_candidate:.4f} RSI={rsi:.1f} R:R={reward/risk:.2f}"
                    ),
                )
            return Signal(action="hold", reason=f"盈虧比不足 R:R={reward/risk:.2f} < 1.5")

        return Signal(
            action="hold",
            reason=(
                f"未達入場條件 price={close:.4f} "
                f"lower_band={lower_band:.4f} RSI={rsi:.1f}"
            ),
        )

    # ── 出場 ──────────────────────────────────────────────────────────────────

    def _check_exit(self, snap: IndicatorSnapshot, pos: ActivePosition) -> Signal:
        close = snap.close

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
                    reason=f"達到目標 price={close:.4f} TP={pos.take_profit:.4f}",
                )
            # 跌回 VWAP 提前出場
            if snap.vwap is not None and close <= snap.vwap:
                return Signal(
                    action="close",
                    reason=f"回落至 VWAP={snap.vwap:.4f} price={close:.4f}，提前出場",
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
                    reason=f"達到 POC 目標 price={close:.4f} TP={pos.take_profit:.4f}",
                )
            if snap.vwap is not None and close >= snap.vwap:
                return Signal(
                    action="close",
                    reason=f"反彈至 VWAP={snap.vwap:.4f} price={close:.4f}，提前出場",
                )

        return Signal(action="hold", reason=f"持倉中 price={close:.4f}")

    # ── 工具 ──────────────────────────────────────────────────────────────────

    def _get_vwap_std(
        self, snap: IndicatorSnapshot
    ) -> tuple[float | None, float | None]:
        """
        優先使用 snap 已計算的 VWAP；
        若 snap.vwap_lower 存在可反推 std，否則從 klines 重新計算。
        """
        # DESIGN FIX #2：snap 已有 vwap，直接使用
        if snap.vwap is not None and snap.vwap_lower is not None:
            std = (snap.vwap - snap.vwap_lower) / 1.5  # indicators 用 1.5σ 計算
            return snap.vwap, std

        # Fallback：從原始 K 線重新計算
        return self._calculate_vwap(snap.klines)

    def _calculate_vwap(self, klines) -> tuple[float | None, float | None]:
        """計算 VWAP 及標準差，回傳 (vwap, std) 或 (None, None)。"""
        if len(klines) < self.vwap_period:
            return None, None

        recent = klines[-self.vwap_period :]
        cum_vol = 0.0
        cum_vp = 0.0

        for c in recent:
            tp = (c.high + c.low + c.close) / 3
            cum_vol += c.volume
            cum_vp += tp * c.volume

        if cum_vol == 0:
            return None, None

        vwap = cum_vp / cum_vol

        var_sum = sum(
            ((c.high + c.low + c.close) / 3 - vwap) ** 2 * c.volume for c in recent
        )
        std = math.sqrt(var_sum / cum_vol)

        return vwap, std
