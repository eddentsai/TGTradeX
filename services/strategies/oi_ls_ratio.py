"""
OI + 多空比策略

原理：
  多空比下降（空頭增加）+ 持倉量上升（新資金進場）→ 軋空概率高 → 做多
  多空比上升（多頭增加）+ 持倉量上升（新資金進場）→ 軋多概率高 → 做空

數據來源：Binance 公開期貨 API（不需要 Key），可跨交易所用於 Bitunix 下單。

入場條件（做多）：
  - 最近 N 期多空比趨勢下降（從高→低，空頭部位持續增加）
  - 最近 N 期 OI 趨勢上升（新資金持續進場）
  - 變化幅度均超過最低門檻

出場條件：
  - 觸及止損或止盈
  - 多空比方向反轉（空頭開始減少）
"""

from __future__ import annotations

import logging

from services.external_data.binance_futures import BinanceFuturesData
from services.indicators import IndicatorSnapshot
from services.strategies.base import ActivePosition, BaseStrategy, Signal

logger = logging.getLogger(__name__)

# ── 可調整參數 ────────────────────────────────────────────────────────────────
_LOOKBACK = 4            # 取幾期數據判斷趨勢（最少需要 2）
_OI_MIN_CHANGE_PCT = 0.5    # OI 上升最少 0.5%
_LS_MIN_CHANGE_PCT = 2.0    # 多空比變動最少 2%
_SL_PCT = 0.015          # 初始止損 1.5%
_TP_PCT = 0.030          # 止盈 3.0%
_TRAIL_PCT = 0.015       # 移動止損：從峰值回落 1.5% 觸發

# Binance OI/LS API 支援的最小 period
_PERIOD_MAP = {
    "1m": "5m", "3m": "5m", "5m": "5m",
    "15m": "15m", "30m": "30m",
    "1h": "1h", "2h": "2h", "4h": "4h",
    "6h": "6h", "12h": "12h", "1d": "1d",
}


def _trend_change_pct(values: list[float]) -> float:
    """計算序列從最早到最新的變化幅度（%），values 由舊到新"""
    if len(values) < 2 or values[0] == 0:
        return 0.0
    return (values[-1] - values[0]) / values[0] * 100


class OiLsRatioStrategy(BaseStrategy):
    """
    Args:
        period:           Binance API 抓取週期（自動對齊至支援的粒度）
        lookback:         判斷趨勢所用的 K 線數
        oi_min_change:    OI 最低上升 % 門檻
        ls_min_change:    多空比最低變動 % 門檻
        sl_pct:           止損比例
        tp_pct:           止盈比例
        data_provider:    外部注入（測試用），預設自動建立
    """

    def __init__(
        self,
        period: str = "15m",
        lookback: int = _LOOKBACK,
        oi_min_change: float = _OI_MIN_CHANGE_PCT,
        ls_min_change: float = _LS_MIN_CHANGE_PCT,
        sl_pct: float = _SL_PCT,
        tp_pct: float = _TP_PCT,
        trail_pct: float = _TRAIL_PCT,
        data_provider: BinanceFuturesData | None = None,
    ) -> None:
        self._period = _PERIOD_MAP.get(period, period)
        self._lookback = max(lookback, 2)
        self._oi_min_change = oi_min_change
        self._ls_min_change = ls_min_change
        self._sl_pct = sl_pct
        self._tp_pct = tp_pct
        self._trail_pct = trail_pct
        self._data = data_provider or BinanceFuturesData()

    @property
    def name(self) -> str:
        return "oi_ls_ratio"

    def on_candle(
        self, snap: IndicatorSnapshot, position: ActivePosition | None
    ) -> Signal:
        if position is not None:
            return self._check_exit(snap, position)
        return self._check_entry(snap)

    # ── 入場 ──────────────────────────────────────────────────────────────────

    def _check_entry(self, snap: IndicatorSnapshot) -> Signal:
        symbol = self._resolve_symbol(snap)

        ls_data = self._data.get_ls_ratio_history(symbol, self._period, self._lookback)
        oi_data = self._data.get_oi_history(symbol, self._period, self._lookback)

        if ls_data is None or oi_data is None or len(ls_data) < 2 or len(oi_data) < 2:
            return Signal(action="hold", reason=f"OI/LS 數據不足或幣種不支援（{symbol}）")

        ls_values = [r["longShortRatio"] for r in ls_data]  # 由舊到新
        oi_values = [r["openInterest"]   for r in oi_data]

        ls_change = _trend_change_pct(ls_values)   # 正 = 多頭增加；負 = 空頭增加
        oi_change = _trend_change_pct(oi_values)   # 正 = OI 上升

        close = snap.close
        reason_base = (
            f"LS={ls_values[-1]:.3f}({ls_change:+.1f}%) "
            f"OI_chg={oi_change:+.1f}%"
        )

        # ── 做多：空頭增加 + OI 上升 → 軋空 ──────────────────────────────────
        if ls_change <= -self._ls_min_change and oi_change >= self._oi_min_change:
            sl = round(close * (1 - self._sl_pct), 8)
            tp = round(close * (1 + self._tp_pct), 8)
            return Signal(
                action="open_long",
                stop_loss=sl,
                take_profit=tp,
                reason=(
                    f"軋空訊號 {reason_base} "
                    f"SL={sl:.4f} TP={tp:.4f}"
                ),
            )

        # ── 做空：多頭增加 + OI 上升 → 軋多 ──────────────────────────────────
        if ls_change >= self._ls_min_change and oi_change >= self._oi_min_change:
            sl = round(close * (1 + self._sl_pct), 8)
            tp = round(close * (1 - self._tp_pct), 8)
            return Signal(
                action="open_short",
                stop_loss=sl,
                take_profit=tp,
                reason=(
                    f"軋多訊號 {reason_base} "
                    f"SL={sl:.4f} TP={tp:.4f}"
                ),
            )

        return Signal(
            action="hold",
            reason=f"條件不足 {reason_base}（需 |LS|>={self._ls_min_change}% 且 OI>={self._oi_min_change}%）",
        )

    # ── 出場 ──────────────────────────────────────────────────────────────────

    def _check_exit(self, snap: IndicatorSnapshot, pos: ActivePosition) -> Signal:
        close = snap.close
        symbol = self._resolve_symbol(snap)

        if pos.side == "SELL":
            # 硬止損 / 止盈
            if close >= pos.stop_loss:
                return Signal(action="close", reason=f"觸發止損 price={close:.4f} SL={pos.stop_loss:.4f}")
            if close <= pos.take_profit:
                return Signal(action="close", reason=f"達到止盈 price={close:.4f} TP={pos.take_profit:.4f}")

            # 移動止損：追蹤最低價，SL 跟著下移
            peak = min(pos.peak_price or pos.entry_price, close)  # 做空峰值 = 最低點
            trail_sl = round(peak * (1 + self._trail_pct), 8)
            if trail_sl < pos.stop_loss:  # SL 下移（對空頭有利）
                return Signal(
                    action="trail_sl",
                    stop_loss=trail_sl,
                    take_profit=pos.take_profit,
                    reason=f"移動止損下移 peak={peak:.4f} SL {pos.stop_loss:.4f}→{trail_sl:.4f}",
                )

            # LS 反轉 → 提前出場
            ls_data = self._data.get_ls_ratio_history(symbol, self._period, self._lookback)
            if ls_data and len(ls_data) >= 2:
                ls_values = [r["longShortRatio"] for r in ls_data]
                if _trend_change_pct(ls_values) < 0:
                    return Signal(action="close", reason=f"軋多訊號消失（LS 反轉）price={close:.4f}")

        else:  # BUY
            # 硬止損 / 止盈
            if close <= pos.stop_loss:
                return Signal(action="close", reason=f"觸發止損 price={close:.4f} SL={pos.stop_loss:.4f}")
            if close >= pos.take_profit:
                return Signal(action="close", reason=f"達到止盈 price={close:.4f} TP={pos.take_profit:.4f}")

            # 移動止損：追蹤最高價，SL 跟著上移
            peak = max(pos.peak_price or pos.entry_price, close)
            trail_sl = round(peak * (1 - self._trail_pct), 8)
            if trail_sl > pos.stop_loss:  # SL 上移（對多頭有利）
                return Signal(
                    action="trail_sl",
                    stop_loss=trail_sl,
                    take_profit=pos.take_profit,
                    reason=f"移動止損上移 peak={peak:.4f} SL {pos.stop_loss:.4f}→{trail_sl:.4f}",
                )

            # LS 反轉 → 提前出場
            ls_data = self._data.get_ls_ratio_history(symbol, self._period, self._lookback)
            if ls_data and len(ls_data) >= 2:
                ls_values = [r["longShortRatio"] for r in ls_data]
                if _trend_change_pct(ls_values) > 0:
                    return Signal(action="close", reason=f"軋空訊號消失（LS 反轉）price={close:.4f}")

        return Signal(action="hold", reason=f"持倉中 price={close:.4f}")

    # ── 工具 ──────────────────────────────────────────────────────────────────

    def _resolve_symbol(self, snap: IndicatorSnapshot) -> str:
        """從 snap 取得幣種名稱（Binance 格式，例如 BTCUSDT）"""
        return snap.symbol.upper() if snap.symbol else ""
