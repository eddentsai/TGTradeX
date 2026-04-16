"""
OI 48 小時背離篩選器

篩選邏輯：OI 悄悄累積代表有資金在佈局，但市場還沒動，
          等待突破的機率較高，適合提前做多等待爆發。

嚴格版參數（預設）：
  - OI 48h 增加 > 20%（持倉持續累積）
  - 價格 48h 變化 < ±3%（市場尚未反應）
"""
from __future__ import annotations

import logging
import time

from exchanges.base import BaseExchange
from services.external_data.binance_futures import BinanceFuturesData

logger = logging.getLogger(__name__)

_OI_CHANGE_MIN    = 0.20  # OI 48h 增加最少 20%
_PRICE_CHANGE_MAX = 0.03  # 價格 48h 變化絕對值最多 3%
_API_DELAY        = 0.2   # 每筆 symbol 間的延遲（秒），避免觸發速率限制


class OiDivergenceFilter:
    """
    從候選幣種中篩選出 OI 大幅增加但價格尚未反應的幣種。

    Args:
        binance_data:     BinanceFuturesData 實例（提供 OI 歷史）
        exchange:         用於取得 K 線的交易所（建議用 Binance 公開端點）
        oi_change_min:    OI 48h 最低上升比例（預設 0.20 = 20%）
        price_change_max: 價格 48h 最高變動絕對值（預設 0.03 = 3%）
    """

    def __init__(
        self,
        binance_data:     BinanceFuturesData,
        exchange:         BaseExchange,
        oi_change_min:    float = _OI_CHANGE_MIN,
        price_change_max: float = _PRICE_CHANGE_MAX,
    ) -> None:
        self._data      = binance_data
        self._exchange  = exchange
        self._oi_min    = oi_change_min
        self._price_max = price_change_max

    def filter(
        self,
        symbols:      list[str],
        held_symbols: set[str] | None = None,
    ) -> list[str]:
        """
        回傳符合 OI 背離條件的幣種列表。
        已持倉幣種無條件保留，不受篩選影響。
        """
        held    = held_symbols or set()
        result  = []
        skipped = []

        for sym in symbols:
            if sym in held:
                result.append(sym)
                continue
            try:
                oi_chg, price_chg = self._get_divergence(sym)
                if oi_chg >= self._oi_min and abs(price_chg) <= self._price_max:
                    logger.info(
                        f"[OiDiv] ✓ {sym}: OI 48h={oi_chg*100:+.1f}%  "
                        f"價格 48h={price_chg*100:+.1f}%"
                    )
                    result.append(sym)
                else:
                    skipped.append(
                        f"{sym}(OI={oi_chg*100:+.1f}%,P={price_chg*100:+.1f}%)"
                    )
                time.sleep(_API_DELAY)
            except Exception as e:
                logger.debug(f"[OiDiv] {sym} 篩選失敗: {e}")

        if skipped:
            logger.debug(f"[OiDiv] 未達門檻: {', '.join(skipped)}")
        logger.info(
            f"[OiDiv] 篩選完成: {len(symbols)} 個候選 → {len(result)} 個符合"
            f"（OI>{self._oi_min*100:.0f}%, |價格|<{self._price_max*100:.0f}%）"
        )
        return result

    def _get_divergence(self, symbol: str) -> tuple[float, float]:
        """計算 (oi_change_48h, price_change_48h)，均為小數（0.20 = 20%）"""
        # OI：1h period × 49 筆 = 48 個區間
        oi_hist = self._data.get_oi_history(symbol, period="1h", limit=49)
        if not oi_hist or len(oi_hist) < 2:
            raise ValueError("OI 數據不足")
        oi_old = oi_hist[0]["openInterest"]
        oi_new = oi_hist[-1]["openInterest"]
        if oi_old == 0:
            raise ValueError("OI 起始值為 0")
        oi_change = (oi_new - oi_old) / oi_old

        # 價格：49 根 1h K 線的首尾收盤價
        klines = self._exchange.get_klines(symbol, "1h", limit=49)
        if not klines or len(klines) < 2:
            raise ValueError("K 線數據不足")
        price_old = float(klines[0]["close"])
        price_new = float(klines[-1]["close"])
        if price_old == 0:
            raise ValueError("起始價格為 0")
        price_change = (price_new - price_old) / price_old

        return oi_change, price_change
