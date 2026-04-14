"""
Binance 公開期貨數據抓取器（不需要 API Key）

提供：
  - 多空比歷史（globalLongShortAccountRatio）
  - 持倉量歷史（openInterestHist）

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
