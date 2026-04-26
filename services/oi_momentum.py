"""
OI 動能篩選器

篩選邏輯：OI 與價格短期同步上升，代表資金正跟著市場方向進入，
          趨勢延伸機率較高，適合順勢做多。

篩選參數（預設）：
  - OI 8h 增加 > 5%（資金近期快速湧入）
  - 價格 8h 增加 > 2%（方向確認，非背離）
  - 最近 3 根 K 線合計成交額 > 200 萬 USDT（排除殭屍幣）
  - 近 2h 價格跌幅不超過 2%（動能仍在延伸，非已反轉）
  - 當前價格距 8h 高點回落不超過 5%（未處於回撤尾段）
"""

from __future__ import annotations

import logging
import time

from exchanges.base import BaseExchange
from services.external_data.binance_futures import BinanceFuturesData

logger = logging.getLogger(__name__)

_OI_CHANGE_MIN           = 0.05    # OI 8h 至少上升 5%
_PRICE_CHANGE_MIN        = 0.02    # 價格 8h 至少上升 2%
_MIN_RECENT_VOL          = 2_000_000
_API_DELAY               = 0.2
_OI_LIMIT                = 9       # 1h × 9 = 8h 區間
_MIN_RECENT_PRICE_CHANGE = -0.02   # 近 2h 最多允許下跌 2%（負值為跌幅）
_MAX_PEAK_RETRACE        = 0.05    # 當前價格距 8h 最高收盤不超過 5%


class OiMomentumFilter:
    """
    從候選幣種中篩選出 OI 與價格同步上升的動能幣種。

    Args:
        binance_data:           BinanceFuturesData 實例
        exchange:               用於取得 K 線的交易所
        oi_change_min:          OI 8h 最低上升比例（預設 0.05 = 5%）
        price_change_min:       價格 8h 最低上升比例（預設 0.02 = 2%）
        min_recent_price_change:近 2h 最低價格變化（預設 -0.02 = 允許下跌 2%）
        max_peak_retrace:       距 8h 最高收盤最大回落（預設 0.05 = 5%）
        min_recent_vol:         最近 3 根 K 線合計 USDT 成交額門檻（預設 200 萬）
    """

    def __init__(
        self,
        binance_data: BinanceFuturesData,
        exchange: BaseExchange,
        oi_change_min:           float = _OI_CHANGE_MIN,
        price_change_min:        float = _PRICE_CHANGE_MIN,
        min_recent_price_change: float = _MIN_RECENT_PRICE_CHANGE,
        max_peak_retrace:        float = _MAX_PEAK_RETRACE,
        min_recent_vol:          float = _MIN_RECENT_VOL,
    ) -> None:
        self._data                    = binance_data
        self._exchange                = exchange
        self._oi_min                  = oi_change_min
        self._price_min               = price_change_min
        self._min_recent_price_change = min_recent_price_change
        self._max_peak_retrace        = max_peak_retrace
        self._min_recent_vol          = min_recent_vol

    def _btc_is_bullish(self) -> bool:
        """BTC 收盤站上 EMA20（1h）時回傳 True；取得失敗時放行（True）。"""
        try:
            klines = self._exchange.get_klines("BTCUSDT", "1h", limit=25)
            if not klines or len(klines) < 21:
                return True
            closes = [float(k["close"]) for k in klines]
            ema20 = sum(closes[-20:]) / 20
            return closes[-1] > ema20
        except Exception as e:
            logger.warning(f"[OiMom] BTC EMA20 取得失敗，放行: {e}")
            return True

    def filter(
        self,
        symbols: list[str],
        held_symbols: set[str] | None = None,
    ) -> list[str]:
        """回傳符合 OI 動能條件的幣種列表。已持倉幣種無條件保留。"""
        held   = held_symbols or set()
        result = []
        skipped = []

        # BTC 趨勢過濾：BTC 跌破 EMA20（1h）時不開新倉
        if not self._btc_is_bullish():
            logger.info("[OiMom] BTC 收盤低於 EMA20（1h），市場偏空，跳過所有新進場掃描")
            return [sym for sym in symbols if sym in held]

        for sym in symbols:
            if sym in held:
                result.append(sym)
                continue
            try:
                oi_chg, price_chg, recent_vol, price_chg_2h, peak_retrace = self._get_momentum(sym)
                if oi_chg < self._oi_min or price_chg < self._price_min:
                    skipped.append(
                        f"{sym}(OI={oi_chg*100:+.1f}%,P={price_chg*100:+.1f}%)"
                    )
                elif recent_vol < self._min_recent_vol:
                    logger.info(
                        f"[OiMom] ✗ {sym}: OI 8h={oi_chg*100:+.1f}% OK 但近期成交額過低"
                        f" {recent_vol/1e6:.1f}M USDT（門檻 {self._min_recent_vol/1e6:.0f}M）"
                    )
                    skipped.append(f"{sym}(低流動性)")
                elif price_chg_2h < self._min_recent_price_change:
                    logger.info(
                        f"[OiMom] ✗ {sym}: OI 8h={oi_chg*100:+.1f}% OK 但近 2h 已回落"
                        f" {price_chg_2h*100:+.1f}%（門檻 {self._min_recent_price_change*100:+.1f}%），動能已反轉"
                    )
                    skipped.append(f"{sym}(近期回落)")
                elif peak_retrace > self._max_peak_retrace:
                    logger.info(
                        f"[OiMom] ✗ {sym}: OI 8h={oi_chg*100:+.1f}% OK 但距 8h 高點已回落"
                        f" {peak_retrace*100:.1f}%（門檻 {self._max_peak_retrace*100:.0f}%），追高風險"
                    )
                    skipped.append(f"{sym}(高點回落)")
                else:
                    logger.info(
                        f"[OiMom] ✓ {sym}: OI 8h={oi_chg*100:+.1f}%"
                        f"  價格 8h={price_chg*100:+.1f}%"
                        f"  近 2h={price_chg_2h*100:+.1f}%"
                        f"  峰值回落={peak_retrace*100:.1f}%"
                        f"  近期量={recent_vol/1e6:.1f}M"
                    )
                    result.append(sym)
                time.sleep(_API_DELAY)
            except Exception as e:
                logger.debug(f"[OiMom] {sym} 篩選失敗: {e}")

        if skipped:
            logger.debug(f"[OiMom] 未達門檻: {', '.join(skipped)}")
        logger.info(
            f"[OiMom] 篩選完成: {len(symbols)} 個候選 → {len(result)} 個符合"
            f"（OI>{self._oi_min*100:.0f}%, 價格>{self._price_min*100:.0f}%）"
        )
        return result

    def _get_momentum(self, symbol: str) -> tuple[float, float, float, float, float]:
        """計算 (oi_change_8h, price_change_8h, recent_vol_usdt, price_change_2h, peak_retrace)"""
        oi_hist = self._data.get_oi_history(symbol, period="1h", limit=_OI_LIMIT)
        if not oi_hist or len(oi_hist) < 2:
            raise ValueError("OI 數據不足")
        oi_old = oi_hist[0]["openInterest"]
        oi_new = oi_hist[-1]["openInterest"]
        if oi_old == 0:
            raise ValueError("OI 起始值為 0")
        oi_change = (oi_new - oi_old) / oi_old

        klines = self._exchange.get_klines(symbol, "1h", limit=_OI_LIMIT)
        if not klines or len(klines) < 3:
            raise ValueError("K 線數據不足")
        price_old = float(klines[0]["close"])
        price_new = float(klines[-1]["close"])
        if price_old == 0:
            raise ValueError("起始價格為 0")
        price_change = (price_new - price_old) / price_old

        # 近 2h 動能：klines[-1] vs klines[-3]（2h 前的收盤）
        price_2h_ago = float(klines[-3]["close"])
        price_change_2h = (price_new - price_2h_ago) / price_2h_ago if price_2h_ago > 0 else 0.0

        # 8h 高點回落：當前收盤距 8h 內最高收盤的跌幅
        price_high = max(float(k["close"]) for k in klines)
        peak_retrace = (price_high - price_new) / price_high if price_high > 0 else 0.0

        recent_vol = sum(float(k["volume"]) * float(k["close"]) for k in klines[-3:])
        return oi_change, price_change, recent_vol, price_change_2h, peak_retrace
