"""
VWAP/POC 均值回歸策略（震盪市場使用）

入場條件：
  - 震盪市場中
  - POC 在 VWAP 上方（確保有回歸目標）
  - 價格跌至 VWAP -1.5σ 標準差
  - RSI < 40（超賣）
  - 盈虧比 >= 1.5

出場條件：
  - 目標：POC（全倉出場）
  - 止損：VWAP -2.5σ

注意：分批出場（VWAP 半倉 / POC 全倉）待 Signal 支援 quantity_pct 後再啟用。
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

        # DESIGN FIX #2：優先使用 snap 已算好的 VWAP，避免重複計算
        vwap, std = self._get_vwap_std(snap)
        if vwap is None or std is None:
            return Signal(action="hold", reason="VWAP 計算資料不足")

        # POC 必須在 VWAP 上方
        if poc <= vwap:
            return Signal(
                action="hold",
                reason=f"POC={poc:.4f} 低於 VWAP={vwap:.4f}，無回歸目標",
            )

        lower_band = vwap - self.entry_band * std
        stop_loss = vwap - self.stop_loss_band * std

        if close <= lower_band and rsi < 40:
            risk = abs(close - stop_loss)
            reward = abs(poc - close)

            # BUG FIX #1：防止 risk == 0 導致 ZeroDivisionError
            if risk <= 0:
                return Signal(action="hold", reason="止損距離為零，跳過")

            if reward / risk >= 1.5:
                return Signal(
                    action="open_long",
                    stop_loss=stop_loss,
                    take_profit=poc,
                    reason=(
                        f"VWAP 回歸 price={close:.4f} "
                        f"VWAP={vwap:.4f} lower_band={lower_band:.4f} "
                        f"POC={poc:.4f} RSI={rsi:.1f} R:R={reward/risk:.2f}"
                    ),
                )
            return Signal(
                action="hold",
                reason=f"盈虧比不足 R:R={reward/risk:.2f} < 1.5",
            )

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

        # 止損
        if close <= pos.stop_loss:
            return Signal(
                action="close",
                reason=f"觸發止損 price={close:.4f} SL={pos.stop_loss:.4f}",
            )

        # 止盈（到達 POC 全倉出場）
        # MINOR FIX #4：部分平倉邏輯暫時移除，等 Signal 支援 quantity_pct 後再加回
        if close >= pos.take_profit:
            return Signal(
                action="close",
                reason=f"達到 POC 目標 price={close:.4f} TP={pos.take_profit:.4f}",
            )

        # DESIGN FIX #2：用 snap.vwap 做中途出場參考（可選）
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
