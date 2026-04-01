"""
Coinglass 數據提供者

實作 BaseDataProvider 介面，透過 Coinglass REST API 取得：
  - 資金費率 (Funding Rate)
  - 清算數據 (Liquidations)
  - 多空比   (Long/Short Ratio)

所有請求皆設有 timeout，HTTP 錯誤會拋出 requests.HTTPError，
由上層 MarketBiasCalculator 各自捕捉。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import requests

from .base import BaseDataProvider

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10  # 秒


class CoinglassProvider(BaseDataProvider):
    """Coinglass 數據提供者"""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.base_url = "https://api.coinglass.com/api"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

    # ── 公開介面（實作 BaseDataProvider）────────────────────────────────────

    def get_funding_rate(self, symbol: str) -> float:
        """
        取得最新資金費率。

        Returns:
            float: 資金費率，例如 0.0001 代表 0.01%
        Raises:
            requests.HTTPError: HTTP 狀態碼非 2xx
            requests.RequestException: 網路錯誤 / timeout
        """
        data = self._get(
            "/futures/funding-rate",
            params={"symbol": symbol},
        )
        rate = float(data.get("rate", 0))
        logger.debug(f"[Coinglass] {symbol} 資金費率: {rate}")
        return rate

    def get_liquidations(
        self,
        symbol: str,
        period: str = "1h",
    ) -> Dict[str, float]:
        """
        取得指定週期內的清算量。

        Returns:
            dict: {"long": float, "short": float}，單位 USDT
        Raises:
            requests.HTTPError / requests.RequestException
        """
        data = self._get(
            "/futures/liquidation",
            params={"symbol": symbol, "period": period},
        )
        result = {
            "long": float(data.get("longLiquidation", 0)),
            "short": float(data.get("shortLiquidation", 0)),
        }
        logger.debug(f"[Coinglass] {symbol} 清算量 ({period}): {result}")
        return result

    def get_long_short_ratio(self, symbol: str) -> float:
        """
        取得多空比（散戶持倉比例）。

        Returns:
            float: 多空比，> 1 代表多頭佔多數，例如 1.5 代表多:空 = 1.5:1
        Raises:
            requests.HTTPError / requests.RequestException
        """
        data = self._get(
            "/futures/long-short-ratio",
            params={"symbol": symbol},
        )
        ratio = float(data.get("longShortRatio", 1.0))
        logger.debug(f"[Coinglass] {symbol} 多空比: {ratio}")
        return ratio

    def get_liquidation_heatmap(self, symbol: str) -> list[Any]:
        """
        取得清算熱力圖資料（Coinglass 獨有，不在 BaseDataProvider 介面）。

        Returns:
            list: 熱力圖資料點列表
        Raises:
            requests.HTTPError / requests.RequestException
        """
        data = self._get(
            "/futures/liquidation-heatmap",
            params={"symbol": symbol},
        )
        return data.get("data", [])

    # ── 內部工具 ─────────────────────────────────────────────────────────────

    def _get(
        self,
        path: str,
        params: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """
        統一的 GET 請求入口。

        - 自動帶入 headers 與 timeout
        - 非 2xx 狀態碼拋出 HTTPError
        - 回傳已解析的 JSON dict

        Raises:
            requests.HTTPError:       HTTP 4xx / 5xx
            requests.RequestException: 網路錯誤 / timeout
            ValueError:               回應不是合法 JSON
        """
        url = f"{self.base_url}{path}"
        logger.debug(f"[Coinglass] GET {url} params={params}")

        response = requests.get(
            url,
            params=params,
            headers=self.headers,
            timeout=_DEFAULT_TIMEOUT,
        )
        response.raise_for_status()  # 非 2xx → 拋出 HTTPError

        return response.json()
