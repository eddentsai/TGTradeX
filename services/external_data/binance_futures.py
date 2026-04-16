"""
Binance 公開期貨數據抓取器（不需要 API Key）

提供：
  - 多空比歷史（globalLongShortAccountRatio）
  - 持倉量歷史（openInterestHist）
  - 資金費率歷史（fundingRate）
  - 近期強平清算量（allForceOrders）

支援的 period：5m / 15m / 30m / 1h / 2h / 4h / 6h / 12h / 1d
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://fapi.binance.com"
_TIMEOUT = 8  # 秒
_CACHE_TTL = 60  # 快取 60 秒，避免同一週期重複打 API


class BinanceFuturesData:
    """Binance 期貨公開數據（OI + 多空比），帶簡易 TTL 快取"""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, Any]] = {}  # key → (timestamp, data)

    # ── 公開介面 ──────────────────────────────────────────────────────────────

    def get_ls_ratio_history(
        self, symbol: str, period: str = "15m", limit: int = 6
    ) -> list[dict] | None:
        """
        取得多空比歷史（全局帳戶）。

        Returns:
            list of dict: [{"longShortRatio": float, "longAccount": float,
                            "shortAccount": float, "timestamp": int}, ...]
            None: 幣種不支援或 API 失敗
        """
        key = f"ls:{symbol}:{period}:{limit}"
        cached = self._get_cache(key)
        if cached is not None:
            return cached

        try:
            resp = requests.get(
                f"{_BASE_URL}/futures/data/globalLongShortAccountRatio",
                params={"symbol": symbol, "period": period, "limit": limit},
                timeout=_TIMEOUT,
            )
            if resp.status_code == 400:
                # 幣種不支援此 endpoint
                return None
            resp.raise_for_status()
            data = [
                {
                    "longShortRatio": float(r["longShortRatio"]),
                    "longAccount":    float(r["longAccount"]),
                    "shortAccount":   float(r["shortAccount"]),
                    "timestamp":      int(r["timestamp"]),
                }
                for r in resp.json()
            ]
            self._set_cache(key, data)
            return data
        except Exception as e:
            logger.debug(f"[BinanceFutures] L/S ratio 取得失敗 {symbol}: {e}")
            return None

    def get_oi_history(
        self, symbol: str, period: str = "15m", limit: int = 6
    ) -> list[dict] | None:
        """
        取得持倉量歷史。

        Returns:
            list of dict: [{"openInterest": float, "timestamp": int}, ...]
            None: 幣種不支援或 API 失敗
        """
        key = f"oi:{symbol}:{period}:{limit}"
        cached = self._get_cache(key)
        if cached is not None:
            return cached

        try:
            resp = requests.get(
                f"{_BASE_URL}/futures/data/openInterestHist",
                params={"symbol": symbol, "period": period, "limit": limit},
                timeout=_TIMEOUT,
            )
            if resp.status_code == 400:
                return None
            resp.raise_for_status()
            data = [
                {
                    "openInterest": float(r["sumOpenInterest"]),
                    "timestamp":    int(r["timestamp"]),
                }
                for r in resp.json()
            ]
            self._set_cache(key, data)
            return data
        except Exception as e:
            logger.debug(f"[BinanceFutures] OI 取得失敗 {symbol}: {e}")
            return None

    def get_funding_rate_history(
        self, symbol: str, limit: int = 5
    ) -> list[dict] | None:
        """
        取得資金費率歷史（由舊到新）。

        Returns:
            list of dict: [{"fundingRate": float, "fundingTime": int}, ...]
            None: API 失敗
        """
        key = f"fr:{symbol}:{limit}"
        cached = self._get_cache(key)
        if cached is not None:
            return cached

        try:
            resp = requests.get(
                f"{_BASE_URL}/fapi/v1/fundingRate",
                params={"symbol": symbol, "limit": limit},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            data = [
                {
                    "fundingRate": float(r["fundingRate"]),
                    "fundingTime": int(r["fundingTime"]),
                }
                for r in resp.json()
            ]
            self._set_cache(key, data)
            return data
        except Exception as e:
            logger.debug(f"[BinanceFutures] 資金費率取得失敗 {symbol}: {e}")
            return None

    def get_recent_liquidations(
        self, symbol: str, limit: int = 50
    ) -> float:
        """
        取得近期空單強平量（USDT）。

        Binance allForceOrders 中，side=BUY 代表空倉被強平（交易所買入平空）。
        將這些訂單的 price × executedQty 加總即為空單清算 USDT 量。

        Returns:
            float: 空單清算 USDT 量；API 失敗時回傳 0.0
        """
        key = f"liq:{symbol}:{limit}"
        cached = self._get_cache(key)
        if cached is not None:
            return cached

        try:
            resp = requests.get(
                f"{_BASE_URL}/fapi/v1/allForceOrders",
                params={"symbol": symbol, "limit": limit},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            orders = resp.json()
            # side=BUY → 空倉被強平（我們關注的方向）
            liq_usdt = sum(
                float(o.get("averagePrice", 0) or o.get("price", 0))
                * float(o.get("executedQty", 0))
                for o in orders
                if o.get("side") == "BUY"
            )
            self._set_cache(key, liq_usdt)
            return liq_usdt
        except Exception as e:
            logger.debug(f"[BinanceFutures] 強平清算取得失敗 {symbol}: {e}")
            return 0.0

    # ── 快取 ─────────────────────────────────────────────────────────────────

    def _get_cache(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        ts, data = entry
        if time.time() - ts > _CACHE_TTL:
            del self._cache[key]
            return None
        return data

    def _set_cache(self, key: str, data: Any) -> None:
        self._cache[key] = (time.time(), data)
