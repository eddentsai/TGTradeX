from __future__ import annotations

from typing import Any

from .http import BitunixHttpTransport


class BitunixFuturesPublicHttpApi:
    def __init__(self, transport: BitunixHttpTransport):
        self._transport = transport

    def get_tickers(self, symbols: str | None = None) -> list[dict]:
        """取得一個或多個交易對的 ticker 資訊"""
        query: dict = {}
        if symbols:
            query["symbols"] = symbols
        result = self._transport.public_request("GET", "/api/v1/futures/market/tickers", query=query)
        return result if isinstance(result, list) else []

    def get_depth(self, symbol: str, limit: int = 100) -> dict:
        """取得指定交易對的委託簿深度"""
        query = {"symbol": symbol, "limit": limit}
        return self._transport.public_request("GET", "/api/v1/futures/market/depth", query=query)

    def get_kline(
        self,
        symbol: str,
        interval: str,
        limit: int | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        type_: str | None = None,
    ) -> list[dict]:
        """取得 K 線資料"""
        query: dict[str, Any] = {"symbol": symbol, "interval": interval}
        if limit is not None:
            query["limit"] = limit
        if start_time is not None:
            query["startTime"] = start_time
        if end_time is not None:
            query["endTime"] = end_time
        if type_ is not None:
            query["type"] = type_
        result = self._transport.public_request("GET", "/api/v1/futures/market/kline", query=query)
        return result if isinstance(result, list) else []

    def get_funding_rate(self, symbol: str) -> dict | None:
        """取得指定交易對的資金費率"""
        query = {"symbol": symbol}
        return self._transport.public_request("GET", "/api/v1/futures/market/funding_rate", query=query)

    def get_batch_funding_rate(self) -> list[dict]:
        """取得所有交易對的資金費率"""
        result = self._transport.public_request("GET", "/api/v1/futures/market/funding_rate/batch")
        return result if isinstance(result, list) else []

    def get_trading_pairs(self, symbols: str | None = None) -> list[dict]:
        """取得交易對資訊"""
        query: dict = {}
        if symbols:
            query["symbols"] = symbols
        result = self._transport.public_request("GET", "/api/v1/futures/market/trading_pairs", query=query)
        return result if isinstance(result, list) else []
