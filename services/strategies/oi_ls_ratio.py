"""
OI + 多空比策略

原理：
  多空比下降（空頭增加）+ 持倉量上升（新資金進場）→ 軋空概率高 → 做多
  多空比上升（多頭增加）+ 持倉量上升（新資金進場）→ 軋多概率高 → 做空

數據來源：Binance 公開期貨 API（不需要 Key），可跨交易所用於 Bitunix 下單。

入場條件（做多）：
  - 最近 N 期多空比趨勢下降（且方向一致性 >= 60%）
  - 最近 N 期 OI 趨勢上升（且方向一致性 >= 60%）
  - 變化幅度均超過最低門檻
  - RSI 未超買（< 65），避免追高

出場條件：
  - 觸及止損或止盈
  - 移動止損（從峰值回落 trail_pct）
  - 多空比方向反轉（空頭開始減少）

止損冷卻：
  - 幣種觸及止損後進入冷卻期（預設 2 小時），冷卻中不重新進場
"""

from __future__ import annotations

import logging
import time

from services.external_data.binance_futures import BinanceFuturesData
from services.indicators import IndicatorSnapshot
from services.strategies.base import ActivePosition, BaseStrategy, Signal

logger = logging.getLogger(__name__)

# ── 可調整參數 ────────────────────────────────────────────────────────────────
_LOOKBACK = 5               # 取幾期數據判斷趨勢（最少需要 2）
_OI_MIN_CHANGE_PCT = 1.5    # OI 上升最少 1.5%（原 0.5%，太容易觸發）
_LS_MIN_CHANGE_PCT = 3.0    # 多空比變動最少 3.0%（原 2.0%）
_MONOTONE_RATIO = 0.6       # 趨勢一致性要求：至少 60% 的期間方向正確
_RSI_LONG_MAX = 65.0        # 做多時 RSI 不能超過此值（避免追高）
_RSI_SHORT_MIN = 35.0       # 做空時 RSI 不能低於此值（避免追低）
_SL_PCT = 0.015             # 初始止損 1.5%
_TP_PCT = 0.030             # 止盈 3.0%
_TRAIL_PCT = 0.015          # 移動止損：從峰值回落 1.5% 觸發
_SL_COOLDOWN_HOURS = 2.0    # 止損觸發後同幣種冷卻時間（小時）
_MAX_DAILY_GAIN_LONG = 15.0  # 做多時 24h 最大漲幅限制（超過此值不追高）
_MAX_DAILY_DROP_SHORT = 15.0 # 做空時 24h 最大跌幅限制（已大跌則空頭過度擁擠）

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


def _monotone_ratio(values: list[float], rising: bool) -> float:
    """
    計算趨勢一致性：連續期間中符合方向（上升/下降）的比例。
    rising=True → 計算上升期間比例；rising=False → 下降期間比例。
    values 由舊到新，長度 >= 2。
    """
    if len(values) < 2:
        return 0.0
    n_correct = 0
    for i in range(1, len(values)):
        delta = values[i] - values[i - 1]
        if rising and delta > 0:
            n_correct += 1
        elif not rising and delta < 0:
            n_correct += 1
    return n_correct / (len(values) - 1)


class OiLsRatioStrategy(BaseStrategy):
    """
    Args:
        period:              Binance API 抓取週期（自動對齊至支援的粒度）
        lookback:            判斷趨勢所用的期數
        oi_min_change:       OI 最低上升 % 門檻
        ls_min_change:       多空比最低變動 % 門檻
        monotone_ratio:      趨勢一致性門檻（0~1）；至少此比例的期間須方向一致
        rsi_long_max:        做多時 RSI 上限；超過視為超買，跳過
        rsi_short_min:       做空時 RSI 下限；低於視為超賣，跳過
        sl_pct:              初始止損比例
        tp_pct:              止盈比例
        trail_pct:           移動止損：從峰值回落此比例觸發
        sl_cooldown_hours:   止損觸發後同幣種冷卻時間（小時），冷卻中不再進場
        max_daily_gain_long: 做多時 24h 漲幅上限；超過視為追高，跳過
        max_daily_drop_short: 做空時 24h 跌幅上限（絕對值）；超過視為空頭擁擠，跳過
        data_provider:       外部注入（測試用），預設自動建立
    """

    def __init__(
        self,
        period: str = "15m",
        lookback: int = _LOOKBACK,
        oi_min_change: float = _OI_MIN_CHANGE_PCT,
        ls_min_change: float = _LS_MIN_CHANGE_PCT,
        monotone_ratio: float = _MONOTONE_RATIO,
        rsi_long_max: float = _RSI_LONG_MAX,
        rsi_short_min: float = _RSI_SHORT_MIN,
        sl_pct: float = _SL_PCT,
        tp_pct: float = _TP_PCT,
        trail_pct: float = _TRAIL_PCT,
        sl_cooldown_hours: float = _SL_COOLDOWN_HOURS,
        max_daily_gain_long: float = _MAX_DAILY_GAIN_LONG,
        max_daily_drop_short: float = _MAX_DAILY_DROP_SHORT,
        data_provider: BinanceFuturesData | None = None,
    ) -> None:
        self._period = _PERIOD_MAP.get(period, period)
        self._lookback = max(lookback, 2)
        self._oi_min_change = oi_min_change
        self._ls_min_change = ls_min_change
        self._monotone_ratio = monotone_ratio
        self._rsi_long_max = rsi_long_max
        self._rsi_short_min = rsi_short_min
        self._sl_pct = sl_pct
        self._tp_pct = tp_pct
        self._trail_pct = trail_pct
        self._sl_cooldown_secs = sl_cooldown_hours * 3600
        self._max_daily_gain_long = max_daily_gain_long
        self._max_daily_drop_short = max_daily_drop_short
        # symbol → 最後一次止損觸發的時間戳
        self._sl_cooldown: dict[str, float] = {}
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

        # 止損冷卻期檢查
        if symbol in self._sl_cooldown:
            elapsed = time.time() - self._sl_cooldown[symbol]
            if elapsed < self._sl_cooldown_secs:
                remaining_h = (self._sl_cooldown_secs - elapsed) / 3600
                return Signal(
                    action="hold",
                    reason=f"止損冷卻中（剩 {remaining_h:.1f}h）",
                )
            else:
                del self._sl_cooldown[symbol]

        ls_data = self._data.get_ls_ratio_history(symbol, self._period, self._lookback)
        oi_data = self._data.get_oi_history(symbol, self._period, self._lookback)

        if ls_data is None or oi_data is None or len(ls_data) < 2 or len(oi_data) < 2:
            return Signal(action="hold", reason=f"OI/LS 數據不足或幣種不支援（{symbol}）")

        ls_values = [r["longShortRatio"] for r in ls_data]  # 由舊到新
        oi_values = [r["openInterest"]   for r in oi_data]

        ls_change = _trend_change_pct(ls_values)   # 正 = 多頭增加；負 = 空頭增加
        oi_change = _trend_change_pct(oi_values)   # 正 = OI 上升

        close = snap.close
        rsi   = snap.rsi           # 可能為 None
        chg24 = snap.change_24h_pct  # 可能為 None

        reason_base = (
            f"LS={ls_values[-1]:.3f}({ls_change:+.1f}%) "
            f"OI_chg={oi_change:+.1f}%"
            + (f" RSI={rsi:.1f}" if rsi is not None else "")
            + (f" 24h={chg24:+.1f}%" if chg24 is not None else "")
        )

        # OI 必須上升且達門檻
        if oi_change < self._oi_min_change:
            return Signal(
                action="hold",
                reason=f"OI 上升不足 {reason_base}（需 >={self._oi_min_change}%）",
            )

        # OI 趨勢一致性檢查
        oi_mono = _monotone_ratio(oi_values, rising=True)
        if oi_mono < self._monotone_ratio:
            return Signal(
                action="hold",
                reason=f"OI 趨勢不一致 {reason_base}（一致性={oi_mono:.0%} < {self._monotone_ratio:.0%}）",
            )

        # ── 做多：空頭增加 + OI 上升 → 軋空 ──────────────────────────────────
        if ls_change <= -self._ls_min_change:
            ls_mono = _monotone_ratio(ls_values, rising=False)
            if ls_mono < self._monotone_ratio:
                return Signal(
                    action="hold",
                    reason=f"LS 下降趨勢不一致 {reason_base}（一致性={ls_mono:.0%}）",
                )
            if rsi is not None and rsi > self._rsi_long_max:
                return Signal(
                    action="hold",
                    reason=f"RSI 超買，跳過做多 {reason_base}（RSI>{self._rsi_long_max}）",
                )
            if chg24 is not None and chg24 > self._max_daily_gain_long:
                return Signal(
                    action="hold",
                    reason=f"日內已大漲，跳過做多 {reason_base}（24h>{self._max_daily_gain_long:.0f}%）",
                )
            sl = round(close * (1 - self._sl_pct), 8)
            tp = round(close * (1 + self._tp_pct), 8)
            return Signal(
                action="open_long",
                stop_loss=sl,
                take_profit=tp,
                reason=(
                    f"軋空訊號 {reason_base} "
                    f"LS_mono={ls_mono:.0%} OI_mono={oi_mono:.0%} "
                    f"SL={sl:.4f} TP={tp:.4f}"
                ),
            )

        # ── 做空：多頭增加 + OI 上升 → 軋多 ──────────────────────────────────
        if ls_change >= self._ls_min_change:
            ls_mono = _monotone_ratio(ls_values, rising=True)
            if ls_mono < self._monotone_ratio:
                return Signal(
                    action="hold",
                    reason=f"LS 上升趨勢不一致 {reason_base}（一致性={ls_mono:.0%}）",
                )
            if rsi is not None and rsi < self._rsi_short_min:
                return Signal(
                    action="hold",
                    reason=f"RSI 超賣，跳過做空 {reason_base}（RSI<{self._rsi_short_min}）",
                )
            if chg24 is not None and chg24 < -self._max_daily_drop_short:
                return Signal(
                    action="hold",
                    reason=f"日內已大跌，跳過做空 {reason_base}（24h<-{self._max_daily_drop_short:.0f}%）",
                )
            sl = round(close * (1 + self._sl_pct), 8)
            tp = round(close * (1 - self._tp_pct), 8)
            return Signal(
                action="open_short",
                stop_loss=sl,
                take_profit=tp,
                reason=(
                    f"軋多訊號 {reason_base} "
                    f"LS_mono={ls_mono:.0%} OI_mono={oi_mono:.0%} "
                    f"SL={sl:.4f} TP={tp:.4f}"
                ),
            )

        return Signal(
            action="hold",
            reason=f"條件不足 {reason_base}（需 |LS|>={self._ls_min_change}%）",
        )

    # ── 出場 ──────────────────────────────────────────────────────────────────

    def _check_exit(self, snap: IndicatorSnapshot, pos: ActivePosition) -> Signal:
        close = snap.close
        symbol = self._resolve_symbol(snap)

        if pos.side == "SELL":
            # 硬止損 / 止盈
            if close >= pos.stop_loss:
                self._sl_cooldown[symbol] = time.time()
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
                self._sl_cooldown[symbol] = time.time()
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
