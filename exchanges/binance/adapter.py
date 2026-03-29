"""
Binance USDS-M Futures 交易所 Adapter

將 binance-sdk-derivatives-trading-usds-futures 包裝為 BaseExchange 介面。

Binance 期貨與 Bitunix 的主要差異：
  - 倉位模式：Binance 預設為單向持倉（One-way Mode），positionSide = BOTH
    平倉用 reduce_only="true"，不需要 positionId
  - 下單欄位：side("BUY"/"SELL") + type + quantity + reduce_only
  - K 線回傳：list of list，每項為
    [open_time, open, high, low, close, volume, close_time, ...]
"""
from __future__ import annotations

import time
from typing import Any

import requests as _requests
from binance_common.configuration import ConfigurationRestAPI
from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import (
    DerivativesTradingUsdsFutures,
)
from binance_sdk_derivatives_trading_usds_futures.rest_api.models.enums import (
    KlineCandlestickDataIntervalEnum,
    NewOrderSideEnum,
)

from exchanges.base import BaseExchange


class BinanceExchange(BaseExchange):
    """BaseExchange 的 Binance USDS-M Futures 實作"""

    def __init__(self, api_key: str, secret_key: str, testnet: bool = False) -> None:
        base_path = (
            "https://testnet.binancefuture.com"
            if testnet
            else "https://fapi.binance.com"
        )
        config = ConfigurationRestAPI(
            api_key=api_key,
            api_secret=secret_key,
            base_path=base_path,
            time_offset=_server_time_offset(base_path),
        )
        self._client = DerivativesTradingUsdsFutures(config_rest_api=config)

    @property
    def name(self) -> str:
        return "binance"

    # ── 帳戶 ──────────────────────────────────────────────────────────────────

    def get_account(self) -> dict[str, Any]:
        """
        回傳格式（對齊 BaseExchange 慣例）：
          available     → totalAvailableBalance
          unrealizedPnl → totalUnrealizedProfit
        """
        resp = self._client.rest_api.account_information_v3()
        data = resp.data() if callable(resp.data) else resp.data
        return {
            "available":     getattr(data, "available_balance", None),
            "unrealizedPnl": getattr(data, "total_unrealized_profit", None),
            "_raw": data,
        }

    # ── 持倉 / 訂單查詢 ───────────────────────────────────────────────────────

    def get_pending_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """
        只回傳有實際倉位的記錄（positionAmt != 0）。
        回傳格式對齊 Bitunix：symbol, side, qty, unrealizedPnl, openPrice
        """
        resp = self._client.rest_api.position_information_v3(symbol=symbol)
        data = resp.data() if callable(resp.data) else resp.data
        rows = data if isinstance(data, list) else getattr(data, "root", [data])
        result = []
        for p in rows:
            amt = float(getattr(p, "position_amt", 0) or 0)
            if amt == 0:
                continue
            result.append({
                "symbol":        getattr(p, "symbol", ""),
                "side":          "BUY" if amt > 0 else "SELL",
                "qty":           str(abs(amt)),
                "unrealizedPnl": str(getattr(p, "un_realized_profit", "") or ""),
                "openPrice":     str(getattr(p, "entry_price", "") or ""),
                "positionId":    getattr(p, "symbol", ""),  # Binance 無 positionId，用 symbol 代替
                "_raw": p,
            })
        return result

    def get_pending_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """
        回傳格式對齊 Bitunix：orderId, side, qty, price
        """
        resp = self._client.rest_api.current_all_open_orders(symbol=symbol)
        data = resp.data() if callable(resp.data) else resp.data
        rows = data if isinstance(data, list) else getattr(data, "root", [data])
        result = []
        for o in rows:
            result.append({
                "orderId": str(getattr(o, "order_id", "") or ""),
                "side":    str(getattr(o, "side", "") or ""),
                "qty":     str(getattr(o, "orig_qty", "") or ""),
                "price":   str(getattr(o, "price", "") or ""),
                "symbol":  str(getattr(o, "symbol", "") or ""),
                "_raw": o,
            })
        return result

    # ── 下單 ──────────────────────────────────────────────────────────────────

    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        接受與 Bitunix 相容的 payload：
          symbol, side("BUY"/"SELL"), orderType("MARKET"/"LIMIT"),
          qty, price(LIMIT), effect("GTC"/"IOC"/"FOK"), tradeSide("OPEN"/"CLOSE")

        Binance 不需要 positionId，平倉用 reduce_only="true"。
        """
        symbol     = payload["symbol"]
        side       = NewOrderSideEnum(payload["side"])
        order_type = payload["orderType"]
        qty        = float(payload["qty"])
        trade_side = payload.get("tradeSide", "OPEN")
        reduce_only = "true" if trade_side == "CLOSE" else "false"

        kwargs: dict[str, Any] = {
            "symbol":      symbol,
            "side":        side,
            "type":        order_type,
            "quantity":    qty,
            "reduce_only": reduce_only,
        }

        if order_type == "LIMIT":
            kwargs["price"]         = float(payload["price"])
            kwargs["time_in_force"] = _map_effect(payload.get("effect", "GTC"))

        resp   = self._client.rest_api.new_order(**kwargs)
        data   = resp.data() if callable(resp.data) else resp.data
        return {
            "orderId": str(getattr(data, "order_id", "") or ""),
            "symbol":  str(getattr(data, "symbol", "") or ""),
            "side":    str(getattr(data, "side", "") or ""),
            "status":  str(getattr(data, "status", "") or ""),
            "_raw":    data,
        }

    def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        resp = self._client.rest_api.cancel_order(
            symbol=symbol,
            order_id=int(order_id),
        )
        data = resp.data() if callable(resp.data) else resp.data
        return {
            "orderId": str(getattr(data, "order_id", "") or ""),
            "status":  str(getattr(data, "status", "") or ""),
            "_raw":    data,
        }

    # ── 市場資料 ──────────────────────────────────────────────────────────────

    def get_klines(self, symbol: str, interval: str, limit: int = 250) -> list[dict[str, Any]]:
        """
        Binance K 線回傳 list of list：
          [0]=open_time [1]=open [2]=high [3]=low [4]=close [5]=volume ...
        轉換為 BaseExchange 標準格式（time, open, high, low, close, volume）。
        """
        interval_enum = KlineCandlestickDataIntervalEnum(interval)
        resp  = self._client.rest_api.kline_candlestick_data(
            symbol=symbol,
            interval=interval_enum,
            limit=limit,
        )
        data = resp.data() if callable(resp.data) else resp.data
        rows = data if isinstance(data, list) else getattr(data, "root", [])
        result = []
        for row in rows:
            # row 可能是 list 或有屬性的物件
            if isinstance(row, (list, tuple)):
                result.append({
                    "time":   int(row[0]),
                    "open":   float(row[1]),
                    "high":   float(row[2]),
                    "low":    float(row[3]),
                    "close":  float(row[4]),
                    "volume": float(row[5]),
                })
            else:
                result.append({
                    "time":   int(getattr(row, "open_time", 0) or 0),
                    "open":   float(getattr(row, "open", 0) or 0),
                    "high":   float(getattr(row, "high", 0) or 0),
                    "low":    float(getattr(row, "low", 0) or 0),
                    "close":  float(getattr(row, "close", 0) or 0),
                    "volume": float(getattr(row, "volume", 0) or 0),
                })
        # 確保由舊到新
        if len(result) >= 2 and result[0]["time"] > result[-1]["time"]:
            result = list(reversed(result))
        return result

    def get_qty_precision(self, symbol: str) -> int:
        """從 Binance 合約規格取得數量精度（quantity_precision 欄位）"""
        resp = self._client.rest_api.exchange_information()
        data = resp.data() if callable(resp.data) else resp.data
        for s in getattr(data, "symbols", []):
            if getattr(s, "symbol", "") == symbol:
                return int(getattr(s, "quantity_precision", 3))
        raise ValueError(f"找不到交易對: {symbol}")


# ── 內部輔助 ──────────────────────────────────────────────────────────────────

def _server_time_offset(base_path: str) -> int:
    """
    取得本機與 Binance 伺服器的時間差（毫秒）。
    伺服器時間 - 本機時間，傳給 ConfigurationRestAPI(time_offset=...) 使用。
    若請求失敗則回傳 0（不修正）。
    """
    try:
        resp        = _requests.get(f"{base_path}/fapi/v1/time", timeout=5)
        server_ms   = resp.json()["serverTime"]
        local_ms    = int(time.time() * 1000)
        offset      = server_ms - local_ms
        return offset
    except Exception:
        return 0


def _map_effect(effect: str) -> str:
    """將 Bitunix 的 effect 字串對應到 Binance 的 timeInForce"""
    mapping = {
        "GTC":       "GTC",
        "IOC":       "IOC",
        "FOK":       "FOK",
        "POST_ONLY": "GTX",
    }
    return mapping.get(effect.upper(), "GTC")
