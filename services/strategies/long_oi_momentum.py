"""
OI 動能做多策略（Long Only）

進場邏輯（由掃描器預篩選 OI+價格同步上升幣種後的技術確認）：
  1. 收盤價 > EMA20（趨勢方向確認）
  2. RSI < rsi_max（避免極端超買）
  3. 近期成交量突破：近 3 根均量 > 前 10 根均量 × vol_surge_ratio

出場邏輯（結構性出場）：
  - 硬止損：ROI ≤ -sl_roi（例如 sl_roi=0.50 → ROI -50% 出場）
  - 固定止盈：進場漲幅 >= tp_pct
  - OI 出場：當前 OI 較峰值下跌 > oi_exit_pct（資金開始撤退）
  - 鎖定止損：進場漲幅 >= lock_gain_pct 後，SL 移至 entry × (1 + lock_sl_pct)
  - 移動止損：進場漲幅超過 trail_activate_pct 後，每根 K 線更新交易所 SL

絕不開空倉。
"""
from __future__ import annotations

import logging

from services.external_data.binance_futures import BinanceFuturesData
from services.indicators import Candle, IndicatorSnapshot
from services.strategies.base import ActivePosition, BaseStrategy, Signal

logger = logging.getLogger(__name__)

_SL_ROI             = 0.50  # 硬止損 ROI 門檻（預設 50% = 價格 -12.5% @ 4x）
_OI_EXIT_PCT        = 0.05
_PERIOD             = "1h"
_LEVERAGE           = 4
_TRAIL_ACTIVATE_ROI = 0.60  # ROI 門檻：保證金收益 ≥ 此值才啟動移動止損（預設 60%）
_TRAIL_DISTANCE_ROI = 0.32  # ROI 距離：移動止損距現價換算 ROI（預設 32% = 價格 8%）
_VOL_SURGE_RATIO    = 1.5   # 近 3 根均量需 > 前 10 根均量 × 此倍
_RSI_MAX            = 75.0
_TP_ROI             = 2.00  # 固定止盈 ROI 門檻（預設 200% = 價格 +50% @ 4x）
_LOCK_GAIN_ROI      = 1.20  # 鎖定觸發 ROI 門檻（預設 120% = 價格 +30% @ 4x）
_LOCK_SL_PCT        = 0.10  # 鎖定止損位置：entry × (1 + 此值)（仍以價格%計）
_MAX_EMA_EXT        = 0.08  # 收盤距 EMA20 最大延伸比例（預設 8%；超過視為追高）

_PERIOD_MAP = {
    "1m": "5m", "3m": "5m", "5m": "5m",
    "15m": "15m", "30m": "30m",
    "1h": "1h", "2h": "2h", "4h": "4h",
    "6h": "6h", "12h": "12h", "1d": "1d",
}


class LongOiMomentumStrategy(BaseStrategy):
    """
    Args:
        sl_roi:              硬止損 ROI 門檻（預設 0.50 = ROI -50%，4x 槓桿對應價格 -12.5%）
        oi_exit_pct:         OI 從峰值下跌此比例時出場（預設 0.05 = 5%）
        leverage:            槓桿倍數，用於 ROI 換算（預設 4）
        trail_activate_roi:  移動止損啟動 ROI 門檻（預設 0.60 = 60% 保證金收益）
        trail_distance_roi:  移動止損 ROI 距離（預設 0.32 = 32% ROI = 價格 8% @ 4x）
        vol_surge_ratio:     近 3 根均量需超過前 10 根均量的倍數（預設 1.5）
        rsi_max:             RSI 超買門檻（預設 75）
        tp_roi:              固定止盈 ROI 門檻（預設 2.00 = 200% = 價格 +50% @ 4x）
        lock_gain_roi:       鎖定觸發 ROI 門檻（預設 1.20 = 120% = 價格 +30% @ 4x）
        lock_sl_pct:         鎖定止損位置（價格%，entry × (1 + 此值)，預設 0.10）
        period:              Binance OI API 週期
        data_provider:       外部注入（測試用）
    """

    def __init__(
        self,
        sl_roi:             float = _SL_ROI,
        oi_exit_pct:        float = _OI_EXIT_PCT,
        ls_shift_pct:       float = 0.0,   # 已停用，保留參數供舊設定向下相容
        leverage:           int   = _LEVERAGE,
        trail_activate_roi: float = _TRAIL_ACTIVATE_ROI,
        trail_distance_roi: float = _TRAIL_DISTANCE_ROI,
        vol_surge_ratio:    float = _VOL_SURGE_RATIO,
        rsi_max:            float = _RSI_MAX,
        tp_roi:             float = _TP_ROI,
        lock_gain_roi:      float = _LOCK_GAIN_ROI,
        lock_sl_pct:        float = _LOCK_SL_PCT,
        max_ema_ext:        float = _MAX_EMA_EXT,
        enable_reverse:     bool  = False,
        reverse_tp_roi:     float = 0.20,
        reverse_sl_roi:     float = 0.05,
        period:             str   = _PERIOD,
        data_provider:      BinanceFuturesData | None = None,
    ) -> None:
        self._sl_roi             = sl_roi
        self._oi_exit_pct        = oi_exit_pct
        self._leverage           = leverage
        self._trail_activate_roi = trail_activate_roi
        self._trail_distance_roi = trail_distance_roi
        self._vol_surge_ratio    = vol_surge_ratio
        self._rsi_max            = rsi_max
        self._tp_roi             = tp_roi
        self._lock_gain_roi      = lock_gain_roi
        self._lock_sl_pct        = lock_sl_pct
        self._max_ema_ext        = max_ema_ext
        self._enable_reverse     = enable_reverse
        self._reverse_tp_roi     = reverse_tp_roi
        self._reverse_sl_roi     = reverse_sl_roi
        self._period             = _PERIOD_MAP.get(period, "1h")
        self._data               = data_provider or BinanceFuturesData()
        self._entry_oi:  dict[str, float] = {}
        self._peak_oi:   dict[str, float] = {}

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

        # 1. 收盤站上 EMA20（主週期）
        if snap.close <= snap.ema20:
            return Signal(
                action="hold",
                reason=f"收盤 {snap.close:.4f} ≤ EMA20 {snap.ema20:.4f}，等待確認",
            )

        # 1b. EMA20 延伸過濾：避免追高（主週期）
        if self._max_ema_ext > 0:
            ext = (snap.close - snap.ema20) / snap.ema20
            if ext > self._max_ema_ext:
                return Signal(
                    action="hold",
                    reason=(
                        f"距 EMA20 延伸 {ext*100:.1f}%（>{self._max_ema_ext*100:.0f}%），"
                        f"追高風險，等待回落貼近均線"
                    ),
                )

        # 2. RSI 過濾（主週期）
        if snap.rsi is not None and snap.rsi >= self._rsi_max:
            return Signal(
                action="hold",
                reason=f"RSI {snap.rsi:.1f} 超買（≥{self._rsi_max:.0f}），等待回落",
            )

        # 3. 高週期趨勢確認（若 runner 提供 confirm_snap）
        if snap.confirm_snap is not None:
            cs = snap.confirm_snap
            if cs.close is None or cs.ema20 is None:
                return Signal(action="hold", reason="確認週期指標不足")
            if cs.close <= cs.ema20:
                return Signal(
                    action="hold",
                    reason=f"[確認週期] 收盤 {cs.close:.4f} ≤ EMA20 {cs.ema20:.4f}，高週期趨勢未確認",
                )
            if cs.rsi is not None and cs.rsi >= self._rsi_max:
                return Signal(
                    action="hold",
                    reason=f"[確認週期] RSI {cs.rsi:.1f} 超買（≥{self._rsi_max:.0f}），等待回落",
                )
            # 高週期 EMA20 延伸過濾（門檻放寬 50%，高週期均線反應較慢）
            if self._max_ema_ext > 0:
                cs_ext = (cs.close - cs.ema20) / cs.ema20
                cs_threshold = self._max_ema_ext * 1.5
                if cs_ext > cs_threshold:
                    return Signal(
                        action="hold",
                        reason=(
                            f"[確認週期] 距 EMA20 延伸 {cs_ext*100:.1f}%"
                            f"（>{cs_threshold*100:.0f}%），等待回落"
                        ),
                    )

        # 4. 成交量突破確認（主週期）
        vol_check = self._check_vol_surge(snap.klines)
        if vol_check is not None:
            return vol_check

        # ── 通過，發出信號 ────────────────────────────────────────────────────
        close = snap.close

        # enable_reverse=True：OI 動能視為「即將到頂」→ 直接開空
        if self._enable_reverse:
            sl = round(close * (1 + self._reverse_sl_roi / self._leverage), 8)
            tp = round(close * (1 - self._reverse_tp_roi / self._leverage), 8)
            return Signal(
                action="open_short",
                stop_loss=sl,
                take_profit=tp,
                reason=(
                    f"OI動能反轉空單: EMA20={snap.ema20:.4f}"
                    + (f" RSI={snap.rsi:.1f}" if snap.rsi is not None else "")
                    + f" SL={sl:.4f}（ROI-{self._reverse_sl_roi*100:.0f}%）"
                    + f" TP={tp:.4f}（ROI+{self._reverse_tp_roi*100:.0f}%）"
                ),
            )

        sl = round(close * (1 - self._sl_roi / self._leverage), 8)
        tp = round(close * (1 + self._tp_roi / self._leverage), 8)
        return Signal(
            action="open_long",
            stop_loss=sl,
            take_profit=tp,
            reason=(
                f"OI動能突破: EMA20={snap.ema20:.4f}"
                + (f" RSI={snap.rsi:.1f}" if snap.rsi is not None else "")
                + f" SL={sl:.4f}（ROI-{self._sl_roi*100:.0f}%）"
                + f" TP={tp:.4f}（ROI+{self._tp_roi*100:.0f}%）"
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

    # ── 反向空單輔助 ──────────────────────────────────────────────────────────

    def _to_reverse(self, close: float, original_reason: str) -> Signal:
        """將出場信號轉換為反向空單信號"""
        sl = round(close * (1 + self._reverse_sl_roi / self._leverage), 8)
        tp = round(close * (1 - self._reverse_tp_roi / self._leverage), 8)
        return Signal(
            action="reverse_short",
            stop_loss=sl,
            take_profit=tp,
            reason=(
                f"{original_reason} → 反向空單"
                f" TP={tp:.4f}(ROI+{self._reverse_tp_roi*100:.0f}%)"
                f" SL={sl:.4f}(ROI-{self._reverse_sl_roi*100:.0f}%)"
            ),
        )

    def _exit_or_reverse(self, close: float, reason: str) -> Signal:
        return Signal(action="close", reason=reason)

    # ── 出場 ──────────────────────────────────────────────────────────────────

    def _check_exit(self, snap: IndicatorSnapshot, pos: ActivePosition) -> Signal:
        # 反向空單由交易所 TP/SL 管理，策略不介入
        if pos.side == "SELL":
            return Signal(action="hold", reason="反向空單由交易所 TP/SL 管理中")

        symbol = snap.symbol
        close  = snap.close
        gain   = (close - pos.entry_price) / pos.entry_price
        roi    = gain * self._leverage  # 保證金收益率（ROI）

        # 硬止損（以 ROI% 計）
        if roi <= -self._sl_roi:
            self._clear_state(symbol)
            return self._exit_or_reverse(
                close,
                f"硬止損 ROI={roi*100:.1f}%（≤-{self._sl_roi*100:.0f}%）price={close:.4f}",
            )

        # 固定止盈：ROI 達到門檻直接出場
        if roi >= self._tp_roi:
            self._clear_state(symbol)
            return self._exit_or_reverse(
                close,
                f"達到目標獲利 ROI+{roi*100:.1f}%（≥{self._tp_roi*100:.0f}%），獲利了結",
            )

        # ROI 達到鎖定門檻後：SL 至少鎖定至 entry + lock_sl_pct（價格%）
        if roi >= self._lock_gain_roi:
            lock_sl = round(pos.entry_price * (1 + self._lock_sl_pct), 8)
            if lock_sl > pos.stop_loss:
                return Signal(
                    action="trail_sl",
                    stop_loss=lock_sl,
                    reason=(
                        f"ROI+{roi*100:.1f}%（≥{self._lock_gain_roi*100:.0f}%），"
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
                    return self._exit_or_reverse(
                        close,
                        f"OI 從峰值下跌 {oi_drop*100:.1f}%（>{self._oi_exit_pct*100:.0f}%），資金開始撤退",
                    )

        # 移動止損（ROI 達門檻後啟動，距離以 ROI 換算回價格%）
        if roi >= self._trail_activate_roi:
            trail_price_dist = self._trail_distance_roi / self._leverage
            new_sl = round(close * (1 - trail_price_dist), 8)
            if new_sl > pos.stop_loss:
                return Signal(
                    action="trail_sl",
                    stop_loss=new_sl,
                    reason=(
                        f"移動止損上移 SL {pos.stop_loss:.4f} → {new_sl:.4f}"
                        f"（ROI距離 -{self._trail_distance_roi*100:.0f}%，"
                        f"ROI+{roi*100:.1f}%）"
                    ),
                )

        oi_info = (
            f"OI峰={self._peak_oi[symbol]:.0f}"
            if symbol in self._peak_oi else "OI追蹤中"
        )
        trail_info = (
            f" trail={'啟動' if roi >= self._trail_activate_roi else f'待啟動(ROI需+{self._trail_activate_roi*100:.0f}%,現ROI+{roi*100:.1f}%)'}"
        )
        return Signal(
            action="hold",
            reason=f"持倉中 price={close:.4f} {oi_info}{trail_info}".strip(),
        )

    def _clear_state(self, symbol: str) -> None:
        self._entry_oi.pop(symbol, None)
        self._peak_oi.pop(symbol, None)
