"""
OI 動能做多策略（Long Only）

進場邏輯（由掃描器預篩選 OI+價格同步上升幣種後的技術確認）：
  1. 收盤價 > EMA20（趨勢方向確認）
  2. RSI < 75（避免極端超買，比背離策略略寬）
  3. 近期成交量突破：近 3 根均量 > 前 10 根均量 × 1.5（有量撐漲）

出場邏輯（結構性出場）：
  - 硬止損：進場價 × (1 - sl_pct)
  - OI 出場：當前 OI 較峰值下跌 > oi_exit_pct（資金開始撤退）
  - LS 出場：多空比較進場時上升 > ls_shift_pct（空方增加，動能消退）
  - 移動止損：進場漲幅超過 trail_activate_pct 後，每根 K 線更新交易所 SL

TP 設為進場價 × 5 作為安全邊界，實際由 OI/LS 結構決定出場時機。
絕不開空倉。
"""
from __future__ import annotations

import logging

from services.external_data.binance_futures import BinanceFuturesData
from services.indicators import Candle, IndicatorSnapshot
from services.strategies.base import ActivePosition, BaseStrategy, Signal

logger = logging.getLogger(__name__)

_SL_PCT             = 0.20
_OI_EXIT_PCT        = 0.05
_LS_SHIFT_PCT       = 0.10
_PERIOD             = "1h"
_LS_LIMIT           = 3
_TRAIL_ACTIVATE_PCT = 0.15
_TRAIL_DISTANCE_PCT = 0.08
_VOL_SURGE_RATIO    = 1.5   # 近 3 根均量需 > 前 10 根均量 × 此倍
_RSI_MAX            = 75.0
_TP_PCT             = 0.50  # 固定止盈：進場 +50% 直接出場
_LOCK_GAIN_PCT      = 0.30  # 進場漲幅達此值後，SL 鎖定至 entry + lock_sl_pct
_LOCK_SL_PCT        = 0.10  # 鎖定止損位置：entry × (1 + 此值)

_PERIOD_MAP = {
    "1m": "5m", "3m": "5m", "5m": "5m",
    "15m": "15m", "30m": "30m",
    "1h": "1h", "2h": "2h", "4h": "4h",
    "6h": "6h", "12h": "12h", "1d": "1d",
}


class LongOiMomentumStrategy(BaseStrategy):
    """
    Args:
        sl_pct:              硬止損比例（預設 0.20 = -20%）
        oi_exit_pct:         OI 從峰值下跌此比例時出場（預設 0.05 = 5%）
        ls_shift_pct:        多空比較進場上升此比例時出場（預設 0.10 = 10%）
        trail_activate_pct:  移動止損啟動門檻（預設 0.15）
        trail_distance_pct:  移動止損距離（預設 0.08）
        vol_surge_ratio:     近 3 根均量需超過前 10 根均量的倍數（預設 1.5）
        rsi_max:             RSI 超買門檻（預設 75）
        period:              Binance OI/LS API 週期
        data_provider:       外部注入（測試用）
    """

    def __init__(
        self,
        sl_pct:             float = _SL_PCT,
        oi_exit_pct:        float = _OI_EXIT_PCT,
        ls_shift_pct:       float = _LS_SHIFT_PCT,
        trail_activate_pct: float = _TRAIL_ACTIVATE_PCT,
        trail_distance_pct: float = _TRAIL_DISTANCE_PCT,
        vol_surge_ratio:    float = _VOL_SURGE_RATIO,
        rsi_max:            float = _RSI_MAX,
        tp_pct:             float = _TP_PCT,
        lock_gain_pct:      float = _LOCK_GAIN_PCT,
        lock_sl_pct:        float = _LOCK_SL_PCT,
        period:             str   = _PERIOD,
        data_provider:      BinanceFuturesData | None = None,
    ) -> None:
        self._sl_pct             = sl_pct
        self._oi_exit_pct        = oi_exit_pct
        self._ls_shift_pct       = ls_shift_pct
        self._trail_activate_pct = trail_activate_pct
        self._trail_distance_pct = trail_distance_pct
        self._vol_surge_ratio    = vol_surge_ratio
        self._rsi_max            = rsi_max
        self._tp_pct             = tp_pct
        self._lock_gain_pct      = lock_gain_pct
        self._lock_sl_pct        = lock_sl_pct
        self._period             = _PERIOD_MAP.get(period, "1h")
        self._data               = data_provider or BinanceFuturesData()
        self._entry_oi:  dict[str, float] = {}
        self._peak_oi:   dict[str, float] = {}
        self._entry_ls:  dict[str, float] = {}

    @property
    def name(self) -> str:
        return "long_oi_momentum"

    def on_candle(
        self,
        snap: IndicatorSnapshot,
        position: ActivePosition | None,
    ) -> Signal:
        if position is None:
            return self._check_entry(snap)
        return self._check_exit(snap, position)

    # ── 進場 ──────────────────────────────────────────────────────────────────

    def _check_entry(self, snap: IndicatorSnapshot) -> Signal:
        symbol = snap.symbol
        self._clear_state(symbol)

        if snap.close is None or snap.ema20 is None:
            return Signal(action="hold", reason="指標不足")

        # 1. 收盤站上 EMA20
        if snap.close <= snap.ema20:
            return Signal(
                action="hold",
                reason=f"收盤 {snap.close:.4f} ≤ EMA20 {snap.ema20:.4f}，等待確認",
            )

        # 2. RSI 過濾
        if snap.rsi is not None and snap.rsi >= self._rsi_max:
            return Signal(
                action="hold",
                reason=f"RSI {snap.rsi:.1f} 超買（≥{self._rsi_max:.0f}），等待回落",
            )

        # 3. 成交量突破確認
        vol_check = self._check_vol_surge(snap.klines)
        if vol_check is not None:
            return vol_check

        # ── 通過，發出開多信號 ────────────────────────────────────────────────
        close = snap.close
        sl    = round(close * (1 - self._sl_pct), 8)
        tp    = round(close * (1 + self._tp_pct), 8)

        return Signal(
            action="open_long",
            stop_loss=sl,
            take_profit=tp,
            reason=(
                f"OI動能突破: EMA20={snap.ema20:.4f}"
                + (f" RSI={snap.rsi:.1f}" if snap.rsi is not None else "")
                + f" SL={sl:.4f}（-{self._sl_pct*100:.0f}%）TP=結構出場"
            ),
        )

    def _check_vol_surge(self, klines: list[Candle]) -> Signal | None:
        """近 3 根均量 > 前 10 根均量 × vol_surge_ratio；不符合回傳 hold Signal"""
        if len(klines) < 13:
            return None  # K 線不足時放行

        def avg_usdt(bars: list[Candle]) -> float:
            vals = [k.volume * k.close for k in bars]
            return sum(vals) / len(vals) if vals else 0.0

        recent_avg = avg_usdt(klines[-3:])
        prior_avg  = avg_usdt(klines[-13:-3])

        if prior_avg == 0:
            return None

        ratio = recent_avg / prior_avg
        if ratio < self._vol_surge_ratio:
            return Signal(
                action="hold",
                reason=(
                    f"成交量突破未確認 近期/前期={ratio:.2f}"
                    f"（需 ≥ {self._vol_surge_ratio}），動能不足"
                ),
            )
        logger.debug(f"[long_oi_momentum] Vol✓ 量比={ratio:.2f} ≥ {self._vol_surge_ratio}")
        return None

    # ── 出場 ──────────────────────────────────────────────────────────────────

    def _check_exit(self, snap: IndicatorSnapshot, pos: ActivePosition) -> Signal:
        symbol = snap.symbol
        close  = snap.close

        # 硬止損
        sl_price = pos.entry_price * (1 - self._sl_pct)
        if close <= sl_price:
            self._clear_state(symbol)
            return Signal(
                action="close",
                reason=f"硬止損 price={close:.4f} ≤ SL={sl_price:.4f}（-{self._sl_pct*100:.0f}%）",
            )

        # 固定止盈：進場 +tp_pct 直接出場
        if gain >= self._tp_pct:
            self._clear_state(symbol)
            return Signal(
                action="close",
                reason=f"達到目標獲利 +{gain*100:.1f}%（≥{self._tp_pct*100:.0f}%），獲利了結",
            )

        # 進場 +lock_gain_pct 後：SL 至少鎖定至 entry + lock_sl_pct
        if gain >= self._lock_gain_pct:
            lock_sl = round(pos.entry_price * (1 + self._lock_sl_pct), 8)
            if lock_sl > pos.stop_loss:
                return Signal(
                    action="trail_sl",
                    stop_loss=lock_sl,
                    reason=(
                        f"進場 +{gain*100:.1f}%（≥{self._lock_gain_pct*100:.0f}%），"
                        f"SL 鎖定至 entry+{self._lock_sl_pct*100:.0f}%={lock_sl:.4f}"
                    ),
                )

        # OI 監控：從峰值下跌則出場
        oi_hist = self._data.get_oi_history(symbol, self._period, limit=3)
        if oi_hist:
            curr_oi = oi_hist[-1]["openInterest"]
            if symbol not in self._entry_oi:
                self._entry_oi[symbol] = curr_oi
            self._peak_oi[symbol] = max(self._peak_oi.get(symbol, curr_oi), curr_oi)
            peak_oi = self._peak_oi[symbol]

            if peak_oi > 0:
                oi_drop = (peak_oi - curr_oi) / peak_oi
                if oi_drop >= self._oi_exit_pct:
                    self._clear_state(symbol)
                    return Signal(
                        action="close",
                        reason=(
                            f"OI 從峰值下跌 {oi_drop*100:.1f}%"
                            f"（>{self._oi_exit_pct*100:.0f}%），資金開始撤退"
                        ),
                    )

        # 多空比監控：空方比例增加則出場
        ls_hist = self._data.get_ls_ratio_history(symbol, self._period, limit=_LS_LIMIT)
        if ls_hist:
            curr_ls = ls_hist[-1]["longShortRatio"]
            if symbol not in self._entry_ls:
                self._entry_ls[symbol] = curr_ls
            entry_ls = self._entry_ls[symbol]

            if entry_ls > 0:
                ls_shift = (curr_ls - entry_ls) / entry_ls
                if ls_shift >= self._ls_shift_pct:
                    self._clear_state(symbol)
                    return Signal(
                        action="close",
                        reason=(
                            f"多空比較進場上升 {ls_shift*100:.1f}%"
                            f"（>{self._ls_shift_pct*100:.0f}%），空方持續增加"
                        ),
                    )

        # 移動止損
        gain = (close - pos.entry_price) / pos.entry_price
        if gain >= self._trail_activate_pct:
            new_sl = round(close * (1 - self._trail_distance_pct), 8)
            if new_sl > pos.stop_loss:
                return Signal(
                    action="trail_sl",
                    stop_loss=new_sl,
                    reason=(
                        f"移動止損上移 SL {pos.stop_loss:.4f} → {new_sl:.4f}"
                        f"（距現價 -{self._trail_distance_pct*100:.0f}%，"
                        f"進場 +{gain*100:.1f}%）"
                    ),
                )

        oi_info = (
            f"OI峰={self._peak_oi[symbol]:.0f}"
            if symbol in self._peak_oi else "OI追蹤中"
        )
        ls_info = (
            f"LS進場={self._entry_ls[symbol]:.3f} 現={ls_hist[-1]['longShortRatio']:.3f}"
            if ls_hist and symbol in self._entry_ls else ""
        )
        trail_info = (
            f" trail={'啟動' if gain >= self._trail_activate_pct else f'待啟動(需+{self._trail_activate_pct*100:.0f}%)'}"
            f"(+{gain*100:.1f}%)"
        )
        return Signal(
            action="hold",
            reason=f"持倉中 price={close:.4f} {oi_info} {ls_info}{trail_info}".strip(),
        )

    def _clear_state(self, symbol: str) -> None:
        self._entry_oi.pop(symbol, None)
        self._peak_oi.pop(symbol, None)
        self._entry_ls.pop(symbol, None)
