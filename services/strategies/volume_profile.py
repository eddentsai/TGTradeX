"""
成交量分佈策略 VA/POC（震蕩市場使用）

入場條件：
  - 震蕩市場中
  - VAL-POC 或 POC-VAH 價差 ≥ 0.15%（過濾無效成交量分佈）
  - 多單：價格接近 VAL ±1.5%  且  RSI < 50
  - 空單：價格接近 VAH ±1.5%  且  RSI > 55

出場條件（單向，不會與入場重疊）：
  - 多單：close >= take_profit（POC 價格）
  - 空單：close <= take_profit（POC 價格）
  - 止損：VAL 下方 3%（多）/ VAH 上方 3%（空）
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
        vah   = snap.vah
        rsi   = snap.rsi

        if val is None or poc is None or vah is None or rsi is None:
            return Signal(action="hold", reason="指標資料不足（vol profile 或 RSI）")

        # VAL-POC / POC-VAH 最小價差：過濾所有量集中在單一價位的情況
        spread_low  = (poc - val) / val if val > 0 else 0   # POC 離 VAL 多遠
        spread_high = (vah - poc) / poc if poc > 0 else 0   # VAH 離 POC 多遠

        # ── 多單：價格接近 VAL，RSI 偏低 ────────────────────────────────────
        if spread_low >= 0.0015:
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
                        f"POC={poc:.2f} SL={stop_loss:.2f} spread={spread_low*100:.2f}%"
                    ),
                )

        # ── 空單：價格接近 VAH，RSI 偏高 ────────────────────────────────────
        if spread_high >= 0.0015:
            near_vah = abs(close - vah) / vah <= 0.015
            rsi_ok   = rsi > 55.0
            if near_vah and rsi_ok:
                stop_loss   = vah * 1.03
                take_profit = poc
                return Signal(
                    action="open_short",
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    reason=(
                        f"接近 VAH={vah:.2f} RSI={rsi:.1f} "
                        f"POC={poc:.2f} SL={stop_loss:.2f} spread={spread_high*100:.2f}%"
                    ),
                )

        reasons = []
        if spread_low  < 0.0015: reasons.append(f"low-spread={spread_low*100:.2f}%")
        if spread_high < 0.0015: reasons.append(f"high-spread={spread_high*100:.2f}%")
        return Signal(
            action="hold",
            reason=f"未達入場條件 RSI={rsi:.1f} {' '.join(reasons)}",
        )

    # ── 出場（單向，不與入場重疊）─────────────────────────────────────────────

    def _check_exit(self, snap: IndicatorSnapshot, pos: ActivePosition) -> Signal:
        close = snap.close

        if pos.side == "BUY":
            # 止損
            if close <= pos.stop_loss:
                return Signal(
                    action="close",
                    reason=f"觸發止損 price={close:.2f} SL={pos.stop_loss:.2f}",
                )
            # 止盈（價格到達或超過 POC）
            if close >= pos.take_profit:
                return Signal(
                    action="close",
                    reason=f"達到止盈 price={close:.2f} TP={pos.take_profit:.2f}",
                )

        else:  # SELL
            # 止損
            if close >= pos.stop_loss:
                return Signal(
                    action="close",
                    reason=f"觸發止損 price={close:.2f} SL={pos.stop_loss:.2f}",
                )
            # 止盈（價格下跌到或低於 POC）
            if close <= pos.take_profit:
                return Signal(
                    action="close",
                    reason=f"達到止盈 price={close:.2f} TP={pos.take_profit:.2f}",
                )

        return Signal(action="hold", reason=f"持倉中 price={close:.2f}")
