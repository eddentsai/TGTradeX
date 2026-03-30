"""
自動幣種掃描器

從交易所取得所有合約的 24h ticker，過濾出符合條件的交易對：
  1. 排除穩定幣對（USDC, BUSD, TUSD, DAI, FDUSD 等）
  2. 排除槓桿代幣（UP/DOWN/BULL/BEAR 結尾）
  3. 成交量 >= min_quote_vol（24h USDT 成交量）
  4. 按成交量降序排序，回傳前 top_n 名
"""
from __future__ import annotations

import logging
import re

from exchanges.base import BaseExchange

logger = logging.getLogger(__name__)

# 排除穩定幣本身作為標的（例如 USDCUSDT）
_STABLECOIN_RE = re.compile(
    r"^(USDC|BUSD|TUSD|DAI|FDUSD|USDP|GUSD|FRAX|USTC|USDD)USDT$",
    re.IGNORECASE,
)

# 排除槓桿代幣（以 UP/DOWN/BULL/BEAR/3L/3S/2L/2S 結尾）
_LEVERAGED_RE = re.compile(
    r"(UP|DOWN|BULL|BEAR|\dL|\dS)USDT$",
    re.IGNORECASE,
)


def _is_valid_symbol(symbol: str) -> bool:
    """True = 這個幣種值得納入掃描候選"""
    if not symbol.endswith("USDT"):
        return False
    if _STABLECOIN_RE.match(symbol):
        return False
    if _LEVERAGED_RE.search(symbol):
        return False
    return True


class SymbolScanner:
    """
    Args:
        exchange:      已初始化的交易所客戶端
        min_quote_vol: 24h 最低 USDT 成交量門檻（預設 Binance=5億, Bitunix=1億）
        top_n:         最多回傳幾個候選幣種（0 = 不限）
    """

    def __init__(
        self,
        exchange: BaseExchange,
        min_quote_vol: float = 100_000_000,
        top_n: int = 0,
    ) -> None:
        self._exchange     = exchange
        self._min_quote_vol = min_quote_vol
        self._top_n        = top_n

    def scan(self, held_symbols: set[str] | None = None) -> list[str]:
        """
        回傳符合條件的交易對列表（按 24h 成交量降序）。

        Args:
            held_symbols: 目前已持倉的交易對集合，會無條件保留在結果中
                          （即使暫時滑出成交量排名也不中途停掉）
        Returns:
            交易對名稱列表，例如 ["BTCUSDT", "ETHUSDT", ...]
        """
        held = held_symbols or set()

        try:
            tickers = self._exchange.get_tickers()
        except Exception as e:
            logger.error(f"[Scanner] 取得 ticker 失敗: {e}")
            return list(held)

        candidates = []
        for t in tickers:
            sym = t.get("symbol", "")
            if not _is_valid_symbol(sym):
                continue
            vol = t.get("quote_vol", 0) or 0
            if vol < self._min_quote_vol and sym not in held:
                continue
            candidates.append((sym, vol))

        # 按成交量降序
        candidates.sort(key=lambda x: x[1], reverse=True)

        if self._top_n > 0:
            # 先保留已持倉幣種，再從排名前 top_n 補齊
            top_symbols = [s for s, _ in candidates[:self._top_n]]
            for sym in held:
                if sym not in top_symbols:
                    top_symbols.append(sym)
            result = top_symbols
        else:
            result = [s for s, _ in candidates]

        logger.info(
            f"[Scanner] 掃描完成: 共 {len(tickers)} 個合約，"
            f"符合條件 {len(result)} 個"
            f"（最低成交量門檻 {self._min_quote_vol:,.0f} USDT）"
        )
        return result
