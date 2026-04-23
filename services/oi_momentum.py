"""
OI 動能篩選器

篩選邏輯：OI 與價格短期同步上升，代表資金正跟著市場方向進入，
          趨勢延伸機率較高，適合順勢做多。

篩選參數（預設）：
  - OI 8h 增加 > 5%（資金近期快速湧入）
  - 價格 8h 增加 > 2%（方向確認，非背離）
  - 最近 3 根 K 線合計成交額 > 200 萬 USDT（排除殭屍幣）
"""

from __future__ import annotations

import logging
import time

from exchanges.base import BaseExchange
from services.external_data.binance_futures import BinanceFuturesData

logger = logging.getLogger(__name__)

_OI_CHANGE_MIN   = 0.05   # OI 8h 至少上升 5%
_PRICE_CHANGE_MIN = 0.02  # 價格 8h 至少上升 2%
_MIN_RECENT_VOL  = 2_000_000
_API_DELAY       = 0.2
_OI_LIMIT        = 9      # 1h × 9 = 8h 區間


class OiMomentumFilter:
    """
    從候選幣種中篩選出 OI 與價格同步上升的動能幣種。

    Args:
        binance_data:      BinanceFuturesData 實例
        exchange:          用於取得 K 線的交易所
        oi_change_min:     OI 8h 最低上升比例（預設 0.05 = 5%）
        price_change_min:  價格 8h 最低上升比例（預設 0.02 = 2%）
        min_recent_vol:    最近 3 根 K 線合計 USDT 成交額門檻（預設 200 萬）
    """

    def __init__(
        self,
        binance_data: BinanceFuturesData,
        exchange: BaseExchange,
        oi_change_min:    float = _OI_CHANGE_MIN,
        price_change_min: float = _PRICE_CHANGE_MIN,
        min_recent_vol:   float = _MIN_RECENT_VOL,
    ) -> None:
        self._data            = binance_data
        self._exchange        = exchange
        self._oi_min          = oi_change_min
        self._price_min       = price_change_min
        self._min_recent_vol  = min_recent_vol

    def filter(
        self,
        symbols: list[str],
        held_symbols: set[str] | None = None,
    ) -> list[str]:
        """回傳符合 OI 動能條件的幣種列表。已持倉幣種無條件保留。"""
        held   = held_symbols or set()
        result = []
        skipped = []

        for sym in symbols:
            if sym in held:
                result.append(sym)
                continue
            try:
                oi_chg, price_chg, recent_vol = self._get_momentum(sym)
                if oi_chg >= self._oi_min and price_chg >= self._price_min:
                    if recent_vol < self._min_recent_vol:
                        logger.info(
                            f"[OiMom] ✗ {sym}: OI 8h={oi_chg*100:+.1f}% OK 但近期成交額過低"
                            f" {recent_vol/1e6:.1f}M USDT（門檻 {self._min_recent_vol/1e6:.0f}M）"
                        )
                        skipped.append(f"{sym}(低流動性)")
                    else:
                        logger.info(
                            f"[OiMom] ✓ {sym}: OI 8h={oi_chg*100:+.1f}%"
                            f"  價格 8h={price_chg*100:+.1f}%"
                            f"  近期量={recent_vol/1e6:.1f}M"
                        )
                        result.append(sym)
                else:
                    skipped.append(
                        f"{sym}(OI={oi_chg*100:+.1f}%,P={price_chg*100:+.1f}%)"
                    )
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

    def _get_momentum(self, symbol: str) -> tuple[float, float, float]:
        """計算 (oi_change_8h, price_change_8h, recent_vol_usdt)"""
        oi_hist = self._data.get_oi_history(symbol, period="1h", limit=_OI_LIMIT)
        if not oi_hist or len(oi_hist) < 2:
            raise ValueError("OI 數據不足")
        oi_old = oi_hist[0]["openInterest"]
        oi_new = oi_hist[-1]["openInterest"]
        if oi_old == 0:
            raise ValueError("OI 起始值為 0")
        oi_change = (oi_new - oi_old) / oi_old

        klines = self._exchange.get_klines(symbol, "1h", limit=_OI_LIMIT)
        if not klines or len(klines) < 2:
            raise ValueError("K 線數據不足")
        price_old = float(klines[0]["close"])
        price_new = float(klines[-1]["close"])
        if price_old == 0:
            raise ValueError("起始價格為 0")
        price_change = (price_new - price_old) / price_old

        recent_vol = sum(float(k["volume"]) * float(k["close"]) for k in klines[-3:])
        return oi_change, price_change, recent_vol
