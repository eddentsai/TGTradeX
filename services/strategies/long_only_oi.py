"""
OI 背離做多策略（Long Only）

進場邏輯（由掃描器預篩選 OI 背離幣種後的技術確認）：
  1. 收盤價 > EMA20（價格確認開始往上）
  2. RSI < 70（避免極端超買）
  3. 多空比近 5 期下降 > 3%（空方持續累積）
  4. [可選] 資金費率近 N 期為負值（空方主導，短擠風險高）
  5. [可選] BB 緊縮（帶寬低於近期均值，能量壓縮蓄力）
  6. [可選] 近期空單清算量 > 門檻（空方正在被擠，動能確認）
  7. [可選] 成交量萎縮 + OI 上升（籌碼鎖定，市場盤整佈局中）

出場邏輯（結構性出場，由市場結構決定，不設固定 TP）：
  - 硬止損：進場價 × (1 - sl_pct)，預設 -20%
  - OI 出場：當前 OI 較峰值下跌 > oi_exit_pct（預設 5%）→ 資金開始撤退
  - LS 出場：多空比較進場時上升 > ls_shift_pct（預設 10%）→ 空方增加，動能消退
  - 移動止損：進場漲幅超過 trail_activate_pct 後，每根 K 線更新交易所 SL

TP 設為進場價 × 5（500%）作為安全邊界，實際由 OI/LS 結構決定出場時機。
絕不開空倉。
"""
from __future__ import annotations

import logging
import math

from services.external_data.binance_futures import BinanceFuturesData
from services.indicators import Candle, IndicatorSnapshot
from services.strategies.base import ActivePosition, BaseStrategy, Signal

logger = logging.getLogger(__name__)

# ── 可調整參數 ─────────────────────────────────────────────────────────────────
_SL_PCT              = 0.20   # 硬止損：進場價 -20%
_OI_EXIT_PCT         = 0.05   # OI 從峰值下跌 5% 視為資金撤退
_LS_SHIFT_PCT        = 0.10   # 多空比較進場上升 10% 視為空方增加
_LS_ENTRY_DROP       = 3.0    # 進場確認：多空比需下降至少 3%（空方在累積）
_NO_TP_MULT          = 5.0    # 「無 TP」佔位值：進場價 × 5，實際由 OI/LS 出場
_PERIOD              = "1h"
_LS_ENTRY_LIMIT      = 5      # 進場時取最近 5 期多空比判斷趨勢
_LS_LIMIT            = 3      # 出場監控時取最新 3 筆
_TRAIL_ACTIVATE_PCT  = 0.15   # 移動止損啟動：進場後上漲 15% 才開始追蹤
_TRAIL_DISTANCE_PCT  = 0.08   # 移動止損距離：SL 設在當前價格下方 8%

# ── 附加 filter 預設值 ────────────────────────────────────────────────────────
_FUNDING_PERIODS     = 2      # 最近 N 期資金費率須為負值
_BB_SQUEEZE_PERIODS  = 20     # BB 緊縮比較週期（當前帶寬 vs 近 N 根平均）
_BB_SQUEEZE_RATIO    = 0.80   # 當前帶寬 < 近期均值 × 此比例視為緊縮
_MIN_LIQ_USDT        = 50_000 # 近期空單清算量門檻（USDT）
_VOL_SHRINK_RATIO    = 0.75   # 近 5 根均量 / 前 15 根均量 < 此值視為萎縮

_PERIOD_MAP = {
    "1m": "5m", "3m": "5m", "5m": "5m",
    "15m": "15m", "30m": "30m",
    "1h": "1h", "2h": "2h", "4h": "4h",
    "6h": "6h", "12h": "12h", "1d": "1d",
}


class LongOnlyOiStrategy(BaseStrategy):
    """
    Args:
        sl_pct:               硬止損比例（預設 0.20 = -20%）
        oi_exit_pct:          OI 從峰值下跌此比例時出場（預設 0.05 = 5%）
        ls_shift_pct:         多空比較進場上升此比例時出場（預設 0.10 = 10%）
        trail_activate_pct:   移動止損啟動門檻：進場後上漲此比例才開始追蹤（預設 0.15）
        trail_distance_pct:   移動止損距離：SL 距當前價格下方此比例（預設 0.08）
        use_funding_filter:   是否啟用資金費率負值確認（預設 True）
        use_bb_squeeze:       是否啟用 BB 緊縮確認（預設 True）
        use_liq_filter:       是否啟用空單清算量確認（預設 True）
        use_vol_shrink:       是否啟用成交量萎縮確認（預設 True）
        min_filter_confirm:   啟用的 filter 中需通過幾個才進場（預設 2）
        funding_periods:      資金費率需為負值的連續期數（預設 2）
        bb_squeeze_periods:   BB 緊縮比較週期（預設 20）
        bb_squeeze_ratio:     帶寬緊縮比例門檻（預設 0.80）
        min_liq_usdt:         近期空單清算量門檻 USDT（預設 50,000）
        vol_shrink_ratio:     成交量萎縮比例門檻（預設 0.75）
        period:               Binance OI/LS API 週期（自動對齊至支援粒度）
        data_provider:        外部注入（測試用），預設自動建立
    """

    def __init__(
        self,
        sl_pct:              float = _SL_PCT,
        oi_exit_pct:         float = _OI_EXIT_PCT,
        ls_shift_pct:        float = _LS_SHIFT_PCT,
        trail_activate_pct:  float = _TRAIL_ACTIVATE_PCT,
        trail_distance_pct:  float = _TRAIL_DISTANCE_PCT,
        use_funding_filter:  bool  = True,
        use_bb_squeeze:      bool  = True,
        use_liq_filter:      bool  = True,
        use_vol_shrink:      bool  = True,
        min_filter_confirm:  int   = 2,
        funding_periods:     int   = _FUNDING_PERIODS,
        bb_squeeze_periods:  int   = _BB_SQUEEZE_PERIODS,
        bb_squeeze_ratio:    float = _BB_SQUEEZE_RATIO,
        min_liq_usdt:        float = _MIN_LIQ_USDT,
        vol_shrink_ratio:    float = _VOL_SHRINK_RATIO,
        period:              str   = _PERIOD,
        data_provider:       BinanceFuturesData | None = None,
    ) -> None:
        self._sl_pct             = sl_pct
        self._oi_exit_pct        = oi_exit_pct
        self._ls_shift_pct       = ls_shift_pct
        self._trail_activate_pct = trail_activate_pct
        self._trail_distance_pct = trail_distance_pct
        self._use_funding        = use_funding_filter
        self._use_bb_squeeze     = use_bb_squeeze
        self._use_liq            = use_liq_filter
        self._use_vol_shrink     = use_vol_shrink
        self._min_filter_confirm = min_filter_confirm
        self._funding_periods    = funding_periods
        self._bb_squeeze_periods = bb_squeeze_periods
        self._bb_squeeze_ratio   = bb_squeeze_ratio
        self._min_liq_usdt       = min_liq_usdt
        self._vol_shrink_ratio   = vol_shrink_ratio
        self._period             = _PERIOD_MAP.get(period, "1h")
        self._data               = data_provider or BinanceFuturesData()
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

        # 1. 技術確認：收盤站上 EMA20
        if snap.close <= snap.ema20:
            return Signal(
                action="hold",
                reason=f"收盤 {snap.close:.4f} ≤ EMA20 {snap.ema20:.4f}，等待突破",
            )

        # 2. RSI 過濾：避免極端超買
        if snap.rsi is not None and snap.rsi >= 70:
            return Signal(
                action="hold",
                reason=f"RSI {snap.rsi:.1f} 超買（≥70），等待回落",
            )

        # 3. 多空比確認：空方持續累積
        ls_hist = self._data.get_ls_ratio_history(symbol, self._period, limit=_LS_ENTRY_LIMIT)
        if ls_hist is None:
            return Signal(action="hold", reason=f"多空比數據不可用（{symbol} 不支援）")
        if len(ls_hist) < 2:
            return Signal(action="hold", reason="多空比數據不足")

        ls_values = [r["longShortRatio"] for r in ls_hist]
        ls_change = (ls_values[-1] - ls_values[0]) / ls_values[0] * 100
        if ls_change > -_LS_ENTRY_DROP:
            return Signal(
                action="hold",
                reason=(
                    f"多空比未持續下降 LS={ls_values[-1]:.3f}（變化 {ls_change:+.1f}%，"
                    f"需 <-{_LS_ENTRY_DROP:.0f}%），空方尚未累積"
                ),
            )

        # ── 附加 filter：加分制，達到 min_filter_confirm 即放行 ──────────────
        checks: list[tuple[str, bool, str]] = []   # (標籤, 通過, 未通過原因)

        if self._use_funding:
            sig = self._check_funding(symbol)
            checks.append(("FR", sig is None, "" if sig is None else sig.reason))

        if self._use_bb_squeeze:
            sig = self._check_bb_squeeze(snap.klines)
            checks.append(("BB", sig is None, "" if sig is None else sig.reason))

        if self._use_liq:
            sig = self._check_liquidations(symbol)
            checks.append(("LQ", sig is None, "" if sig is None else sig.reason))

        if self._use_vol_shrink:
            sig = self._check_vol_shrink(snap.klines)
            checks.append(("VS", sig is None, "" if sig is None else sig.reason))

        passed  = [label for label, ok, _ in checks if ok]
        failed  = [(label, reason) for label, ok, reason in checks if not ok]
        total   = len(checks)
        n_pass  = len(passed)

        if total > 0 and n_pass < self._min_filter_confirm:
            failed_str = " | ".join(f"{l}: {r}" for l, r in failed)
            return Signal(
                action="hold",
                reason=(
                    f"附加 filter 未達標 {n_pass}/{total}（需 {self._min_filter_confirm}）"
                    + (f" ✓{','.join(passed)}" if passed else "")
                    + f" — {failed_str}"
                ),
            )

        # ── 通過，發出開多信號 ────────────────────────────────────────────────
        close = snap.close
        sl    = round(close * (1 - self._sl_pct), 8)
        tp    = round(close * _NO_TP_MULT, 8)

        filters_info = (
            f" filter={n_pass}/{total}✓({','.join(passed)})"
            if total > 0 else ""
        )
        return Signal(
            action="open_long",
            stop_loss=sl,
            take_profit=tp,
            reason=(
                f"軋空佈局: EMA20={snap.ema20:.4f}"
                + (f" RSI={snap.rsi:.1f}" if snap.rsi is not None else "")
                + f" LS={ls_values[-1]:.3f}({ls_change:+.1f}%)"
                + filters_info
                + f" SL={sl:.4f}（-{self._sl_pct*100:.0f}%）TP=結構出場"
            ),
        )

    # ── 附加進場 filter ────────────────────────────────────────────────────────

    def _check_funding(self, symbol: str) -> Signal | None:
        """資金費率近 N 期均為負值；通過回傳 None，不通過回傳 hold Signal"""
        fr_hist = self._data.get_funding_rate_history(symbol, limit=self._funding_periods + 1)
        if not fr_hist:
            # 取不到資料時放行（避免因 API 問題誤擋）
            return None
        recent = [r["fundingRate"] for r in fr_hist[-self._funding_periods:]]
        if not all(r <= 0 for r in recent):
            positive = [f"{r*100:.4f}%" for r in recent if r > 0]
            return Signal(
                action="hold",
                reason=(
                    f"資金費率未持續負值（近 {self._funding_periods} 期含正值: {', '.join(positive)}）"
                    f"，空方主導不足"
                ),
            )
        rates_str = ", ".join(f"{r*100:.4f}%" for r in recent)
        logger.debug(f"[long_only_oi] FR✓ {symbol} 近期資金費率: {rates_str}")
        return None

    def _check_bb_squeeze(self, klines: list[Candle]) -> Signal | None:
        """BB 帶寬低於近期均值的 bb_squeeze_ratio 倍；通過回傳 None"""
        period = self._bb_squeeze_periods
        if len(klines) < period + 5:
            return None  # K 線不足時放行

        # 計算最近 period+5 根每根的 BB 帶寬（4σ / mid）
        widths: list[float] = []
        window = klines[-(period + 5):]
        for i in range(period - 1, len(window)):
            closes = [k.close for k in window[i - period + 1: i + 1]]
            mean = sum(closes) / period
            if mean == 0:
                continue
            std = math.sqrt(sum((c - mean) ** 2 for c in closes) / period)
            widths.append(4 * std / mean * 100)  # 上下各 2σ，轉為 %

        if len(widths) < 2:
            return None

        curr_width = widths[-1]
        avg_width  = sum(widths[:-1]) / len(widths[:-1])
        if avg_width == 0:
            return None

        if curr_width >= avg_width * self._bb_squeeze_ratio:
            return Signal(
                action="hold",
                reason=(
                    f"BB 未緊縮 帶寬={curr_width:.2f}%（需 < 均值{avg_width:.2f}%"
                    f" × {self._bb_squeeze_ratio}={avg_width*self._bb_squeeze_ratio:.2f}%）"
                ),
            )
        logger.debug(
            f"[long_only_oi] BB✓ 帶寬={curr_width:.2f}% < 均值{avg_width:.2f}% × {self._bb_squeeze_ratio}"
        )
        return None

    def _check_liquidations(self, symbol: str) -> Signal | None:
        """近期空單清算量 > 門檻；通過回傳 None"""
        liq_usdt = self._data.get_recent_liquidations(symbol, limit=50)
        if liq_usdt < self._min_liq_usdt:
            return Signal(
                action="hold",
                reason=(
                    f"空單清算量不足 {liq_usdt/1e3:.1f}K USDT"
                    f"（需 ≥ {self._min_liq_usdt/1e3:.0f}K），空方擠壓動能不強"
                ),
            )
        logger.debug(f"[long_only_oi] LQ✓ 空單清算 {liq_usdt/1e3:.1f}K USDT")
        return None

    def _check_vol_shrink(self, klines: list[Candle]) -> Signal | None:
        """近 5 根均量 < 前 15 根均量 × vol_shrink_ratio；通過回傳 None"""
        if len(klines) < 20:
            return None  # K 線不足時放行

        def avg_vol(bars: list[Candle]) -> float:
            vols = [k.volume * k.close for k in bars]
            return sum(vols) / len(vols) if vols else 0.0

        recent_avg = avg_vol(klines[-5:])
        prior_avg  = avg_vol(klines[-20:-5])

        if prior_avg == 0:
            return None

        ratio = recent_avg / prior_avg
        if ratio >= self._vol_shrink_ratio:
            return Signal(
                action="hold",
                reason=(
                    f"成交量未萎縮 近期/前期={ratio:.2f}（需 < {self._vol_shrink_ratio}），"
                    f"籌碼尚未鎖定"
                ),
            )
        logger.debug(
            f"[long_only_oi] VS✓ 量比={ratio:.2f} < {self._vol_shrink_ratio}"
        )
        return None

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

        # ── 移動止損：進場漲幅超過啟動門檻後，每根 K 線更新交易所 SL ──────────
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
