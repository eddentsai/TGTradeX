"""
OI 背離做多策略（Long Only）

進場邏輯（由掃描器預篩選 OI 背離幣種後的技術確認）：
  - 收盤價 > EMA20（價格確認開始往上）
  - RSI < 70（避免極端超買）

出場邏輯（結構性出場，由市場結構決定，不設固定 TP）：
  - 硬止損：進場價 × (1 - sl_pct)，預設 -20%
  - OI 出場：當前 OI 較峰值下跌 > oi_exit_pct（預設 5%）→ 資金開始撤退
  - LS 出場：多空比較進場時上升 > ls_shift_pct（預設 10%）→ 空方增加，動能消退

TP 設為進場價 × 5（500%）作為安全邊界，實際由 OI/LS 結構決定出場時機。
絕不開空倉。
"""
from __future__ import annotations

import logging

from services.external_data.binance_futures import BinanceFuturesData
from services.indicators import IndicatorSnapshot
from services.strategies.base import ActivePosition, BaseStrategy, Signal

logger = logging.getLogger(__name__)

# ── 可調整參數 ─────────────────────────────────────────────────────────────────
_SL_PCT           = 0.20  # 硬止損：進場價 -20%
_OI_EXIT_PCT      = 0.05  # OI 從峰值下跌 5% 視為資金撤退
_LS_SHIFT_PCT     = 0.10  # 多空比較進場上升 10% 視為空方增加
_LS_ENTRY_DROP    = 3.0   # 進場確認：多空比需下降至少 3%（空方在累積）
_NO_TP_MULT       = 5.0   # 「無 TP」佔位值：進場價 × 5，實際由 OI/LS 出場
_PERIOD           = "1h"
_LS_ENTRY_LIMIT   = 5     # 進場時取最近 5 期多空比判斷趨勢
_LS_LIMIT         = 3     # 出場監控時取最新 3 筆

_PERIOD_MAP = {
    "1m": "5m", "3m": "5m", "5m": "5m",
    "15m": "15m", "30m": "30m",
    "1h": "1h", "2h": "2h", "4h": "4h",
    "6h": "6h", "12h": "12h", "1d": "1d",
}


class LongOnlyOiStrategy(BaseStrategy):
    """
    Args:
        sl_pct:        硬止損比例（預設 0.20 = -20%）
        oi_exit_pct:   OI 從峰值下跌此比例時出場（預設 0.05 = 5%）
        ls_shift_pct:  多空比較進場上升此比例時出場（預設 0.10 = 10%）
        period:        Binance OI/LS API 週期（自動對齊至支援粒度）
        data_provider: 外部注入（測試用），預設自動建立
    """

    def __init__(
        self,
        sl_pct:        float = _SL_PCT,
        oi_exit_pct:   float = _OI_EXIT_PCT,
        ls_shift_pct:  float = _LS_SHIFT_PCT,
        period:        str   = _PERIOD,
        data_provider: BinanceFuturesData | None = None,
    ) -> None:
        self._sl_pct       = sl_pct
        self._oi_exit_pct  = oi_exit_pct
        self._ls_shift_pct = ls_shift_pct
        self._period       = _PERIOD_MAP.get(period, "1h")
        self._data         = data_provider or BinanceFuturesData()
        # 每個幣種的進場狀態追蹤（symbol → value）
        self._entry_oi: dict[str, float] = {}
        self._peak_oi:  dict[str, float] = {}
        self._entry_ls: dict[str, float] = {}

    @property
    def name(self) -> str:
        return "long_only_oi"

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
        # 清除可能的殘留狀態（上一次倉位被交易所 SL 平掉的情況）
        self._clear_state(symbol)

        if snap.close is None or snap.ema20 is None:
            return Signal(action="hold", reason="指標不足")

        # 技術確認：收盤站上 EMA20（價格開始往上）
        if snap.close <= snap.ema20:
            return Signal(
                action="hold",
                reason=f"收盤 {snap.close:.4f} ≤ EMA20 {snap.ema20:.4f}，等待突破",
            )

        # RSI 過濾：避免在極端超買時追高
        if snap.rsi is not None and snap.rsi >= 70:
            return Signal(
                action="hold",
                reason=f"RSI {snap.rsi:.1f} 超買（≥70），等待回落",
            )

        # 多空比確認：空方持續累積（longShortRatio 下降 = 空方比例增加）
        ls_hist = self._data.get_ls_ratio_history(symbol, self._period, limit=_LS_ENTRY_LIMIT)
        if ls_hist is None:
            return Signal(action="hold", reason=f"多空比數據不可用（{symbol} 不支援）")
        if len(ls_hist) < 2:
            return Signal(action="hold", reason="多空比數據不足")

        ls_values = [r["longShortRatio"] for r in ls_hist]  # 由舊到新
        ls_change = (ls_values[-1] - ls_values[0]) / ls_values[0] * 100
        if ls_change > -_LS_ENTRY_DROP:
            return Signal(
                action="hold",
                reason=(
                    f"多空比未持續下降 LS={ls_values[-1]:.3f}（變化 {ls_change:+.1f}%，"
                    f"需 <-{_LS_ENTRY_DROP:.0f}%），空方尚未累積"
                ),
            )

        close = snap.close
        sl    = round(close * (1 - self._sl_pct), 8)
        tp    = round(close * _NO_TP_MULT, 8)  # 500% 佔位，實際由 OI/LS 結構決定出場
        return Signal(
            action="open_long",
            stop_loss=sl,
            take_profit=tp,
            reason=(
                f"軋空佈局: EMA20={snap.ema20:.4f}"
                + (f" RSI={snap.rsi:.1f}" if snap.rsi is not None else "")
                + f" LS={ls_values[-1]:.3f}({ls_change:+.1f}%)"
                + f" SL={sl:.4f}（-{self._sl_pct*100:.0f}%）TP=結構出場"
            ),
        )

    # ── 出場 ──────────────────────────────────────────────────────────────────

    def _check_exit(self, snap: IndicatorSnapshot, pos: ActivePosition) -> Signal:
        symbol = snap.symbol
        close  = snap.close

        # ── 硬止損 ────────────────────────────────────────────────────────────
        sl_price = pos.entry_price * (1 - self._sl_pct)
        if close <= sl_price:
            self._clear_state(symbol)
            return Signal(
                action="close",
                reason=f"硬止損 price={close:.4f} ≤ SL={sl_price:.4f}（-{self._sl_pct*100:.0f}%）",
            )

        # ── OI 監控：追蹤峰值，下跌則出場 ────────────────────────────────────
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

        # ── 多空比監控：空方比例增加則出場 ────────────────────────────────────
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

        oi_info = (
            f"OI峰={self._peak_oi[symbol]:.0f}"
            if symbol in self._peak_oi else "OI追蹤中"
        )
        ls_info = (
            f"LS進場={self._entry_ls[symbol]:.3f} 現={ls_hist[-1]['longShortRatio']:.3f}"
            if ls_hist and symbol in self._entry_ls else ""
        )
        return Signal(
            action="hold",
            reason=f"持倉中 price={close:.4f} {oi_info} {ls_info}".strip(),
        )

    def _clear_state(self, symbol: str) -> None:
        self._entry_oi.pop(symbol, None)
        self._peak_oi.pop(symbol, None)
        self._entry_ls.pop(symbol, None)
