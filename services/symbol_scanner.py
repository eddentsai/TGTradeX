"""
自動幣種掃描器

從交易所取得所有合約的 24h ticker，過濾出符合條件的交易對：
  1. 排除穩定幣對（USDC, BUSD, TUSD, DAI, FDUSD 等）
  2. 排除槓桿代幣（UP/DOWN/BULL/BEAR 結尾）
  3. 排除主流幣（BTC、ETH 等，成交量高但波動特性不適合此策略）
  4. 成交量 >= min_quote_vol（24h USDT 成交量）
  5. 按成交量降序排序，回傳前 top_n 名
"""

from __future__ import annotations

import logging
import re
import time

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

# 主流幣黑名單（市值前段班，流動性過高導致 volume profile 特徵不明顯）
_MAINSTREAM_SYMBOLS: frozenset[str] = frozenset(
    {
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
        "ADAUSDT",
        "BNBUSDT",
        "XRPUSDT",
        "LTCUSDT",
        "DOTUSDT",
        "UNIUSDT",
        "XAGUSDT",
        "XAUUSDT",
        "XAUTUSDT",
        "PAXGUSDT",  # 黃金掛鉤代幣，同 XAU 性質
        "CLUSDT",  # 石油掛鉤代幣
    }
)


def _is_valid_symbol(symbol: str, exclude_mainstream: bool = True) -> bool:
    """True = 這個幣種值得納入掃描候選"""
    if not symbol.endswith("USDT"):
        return False
    if _STABLECOIN_RE.match(symbol):
        return False
    if _LEVERAGED_RE.search(symbol):
        return False
    if exclude_mainstream and symbol in _MAINSTREAM_SYMBOLS:
        return False
    return True


class SymbolScanner:
    """
    Args:
        exchange:           用來取成交量排名的交易所（建議用 Binance，流動性數據更準）
        min_quote_vol:      24h 最低 USDT 成交量門檻（以 exchange 的數據為準）
        top_n:              最多回傳幾個候選幣種（0 = 不限）
        exclude_mainstream: 是否排除主流幣（預設 True）
        max_change_pct:     24h 漲跌幅絕對值上限（預設 40%）；
                            超過此值表示近期出現異常行情，K 線形態已失真，排除
        trade_exchange:     實際下單用的交易所；若與 exchange 不同，
                            掃描結果會過濾為兩邊都有上市的交集
        sort_by:            排序方式："volume"（24h 成交量，預設）或
                            "volatility"（24h 高低價振幅 %，適合尋找波動最大的幣種）
    """

    def __init__(
        self,
        exchange: BaseExchange,
        min_quote_vol: float = 100_000_000,
        top_n: int = 0,
        exclude_mainstream: bool = True,
        max_change_pct: float = 40.0,
        trade_exchange: BaseExchange | None = None,
        volatile_cooldown_hours: float = 24.0,
        sort_by: str = "volume",
    ) -> None:
        self._exchange = exchange
        self._min_quote_vol = min_quote_vol
        self._top_n = top_n
        self._exclude_mainstream = exclude_mainstream
        self._max_change_pct = max_change_pct
        self._volatile_cooldown_secs = volatile_cooldown_hours * 3600
        self._sort_by = sort_by
        # symbol → 最後一次被異常行情排除的時間戳
        self._volatile_banned: dict[str, float] = {}
        # 只有在掃描交易所與下單交易所不同時才需要取交集
        self._trade_exchange = (
            trade_exchange
            if trade_exchange is not None and trade_exchange is not exchange
            else None
        )

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

        # 若指定了下單交易所，先取得它的可用幣種集合，過濾為交集
        trade_symbols: set[str] | None = None
        if self._trade_exchange is not None:
            try:
                trade_tickers = self._trade_exchange.get_tickers()
                trade_symbols = {t.get("symbol", "") for t in trade_tickers}
                logger.debug(
                    f"[Scanner] {self._trade_exchange.name} 可用合約數: {len(trade_symbols)}"
                )
            except Exception as e:
                logger.warning(
                    f"[Scanner] 取得 {self._trade_exchange.name} 合約清單失敗，略過交集過濾: {e}"
                )

        now = time.time()
        candidates = []
        skipped_crash = []
        skipped_cooldown = []
        pumped_through = []
        skipped_no_market = 0
        for t in tickers:
            sym = t.get("symbol", "")
            if not _is_valid_symbol(sym, self._exclude_mainstream):
                continue
            # 下單交易所沒有此合約，跳過
            if (
                trade_symbols is not None
                and sym not in held
                and sym not in trade_symbols
            ):
                skipped_no_market += 1
                continue
            vol = t.get("quote_vol", 0) or 0
            if vol < self._min_quote_vol and sym not in held:
                continue
            change_pct = t.get("change_pct", 0) or 0
            if sym not in held:
                # 急跌：K 線已失真，流動性差，排除並進冷卻
                if change_pct < -self._max_change_pct:
                    skipped_crash.append(f"{sym}({change_pct:+.1f}%)")
                    self._volatile_banned[sym] = now
                    continue
                # 急漲：放行給策略層評估（dip_volume 可做空）
                if change_pct > self._max_change_pct:
                    pumped_through.append(f"{sym}({change_pct:+.1f}%)")
                # 冷卻期：曾因急跌被排除，尚未冷卻完畢
                if sym in self._volatile_banned:
                    elapsed = now - self._volatile_banned[sym]
                    if elapsed < self._volatile_cooldown_secs:
                        remaining_h = (self._volatile_cooldown_secs - elapsed) / 3600
                        skipped_cooldown.append(f"{sym}(剩{remaining_h:.1f}h)")
                        continue
                    else:
                        del self._volatile_banned[sym]
            last_price = t.get("last_price", 0) or 0
            high       = t.get("high", 0) or 0
            low        = t.get("low", 0) or 0
            volatility = (high - low) / last_price * 100 if last_price > 0 else 0.0
            candidates.append((sym, vol, volatility))

        if skipped_no_market:
            logger.info(
                f"[Scanner] 排除 {skipped_no_market} 個 {self._trade_exchange.name} 未上市的合約"
            )
        if skipped_crash:
            logger.info(
                f"[Scanner] 排除急跌幣種（跌幅>{self._max_change_pct:.0f}%，K 線失真）: "
                + ", ".join(skipped_crash)
            )
        if pumped_through:
            logger.info(
                f"[Scanner] 急漲幣種（漲幅>{self._max_change_pct:.0f}%，放行供策略評估做空）: "
                + ", ".join(pumped_through)
            )
        if skipped_cooldown:
            logger.info(
                f"[Scanner] 冷卻中幣種（曾急跌，{self._volatile_cooldown_secs/3600:.0f}h 冷卻）: "
                + ", ".join(skipped_cooldown)
            )

        # 依 sort_by 排序
        if self._sort_by == "volatility":
            candidates.sort(key=lambda x: x[2], reverse=True)  # x[2] = volatility %
            sort_label = "振幅%"
        else:
            candidates.sort(key=lambda x: x[1], reverse=True)  # x[1] = quote_vol
            sort_label = "成交量"

        if self._top_n > 0:
            # 先保留已持倉幣種，再從排名前 top_n 補齊
            top_symbols = [c[0] for c in candidates[: self._top_n]]
            for sym in held:
                if sym not in top_symbols:
                    top_symbols.append(sym)
            result = top_symbols
        else:
            result = [c[0] for c in candidates]

        logger.info(
            f"[Scanner] 掃描完成: 共 {len(tickers)} 個合約，"
            f"符合條件 {len(result)} 個"
            f"（排序={sort_label}，最低成交量門檻 {self._min_quote_vol:,.0f} USDT"
            + ("，已排除主流幣" if self._exclude_mainstream else "")
            + "）"
        )
        return result
