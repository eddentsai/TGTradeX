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

import hashlib
import hmac
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)
from urllib.parse import urlencode

_FILTERS_RETRY_COOLDOWN = 60  # 快取載入失敗後的重試冷卻時間（秒）

import requests as _requests
from binance_common.configuration import ConfigurationRestAPI
from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import (
    DerivativesTradingUsdsFutures,
)
from binance_sdk_derivatives_trading_usds_futures.rest_api.models.enums import (
    KlineCandlestickDataIntervalEnum,
    NewAlgoOrderSideEnum,
    NewAlgoOrderWorkingTypeEnum,
    NewOrderSideEnum,
)

from exchanges.base import BaseExchange


class BinanceExchange(BaseExchange):
    """BaseExchange 的 Binance USDS-M Futures 實作"""

    def __init__(self, api_key: str, secret_key: str, testnet: bool = False) -> None:
        self._base_path = (
            "https://testnet.binancefuture.com"
            if testnet
            else "https://fapi.binance.com"
        )
        self._api_key    = api_key
        self._secret_key = secret_key
        config = ConfigurationRestAPI(
            api_key=api_key,
            api_secret=secret_key,
            base_path=self._base_path,
        )
        self._client = DerivativesTradingUsdsFutures(config_rest_api=config)

    def _load_symbol_filters(self) -> None:
        """從 /fapi/v1/exchangeInfo 載入所有 symbol 的 tick_size 與 qty_precision。
        失敗時設旗標，_FILTERS_RETRY_COOLDOWN 秒內不重試。"""
        self._tick_cache: dict[str, float] = {}
        self._qty_precision_cache: dict[str, int] = {}
        try:
            url  = f"{self._base_path}/fapi/v1/exchangeInfo"
            resp = _requests.get(url, timeout=10)
            if not resp.ok:
                raise RuntimeError(f"HTTP {resp.status_code}")
            for s in resp.json().get("symbols", []):
                sym = s.get("symbol", "")
                self._qty_precision_cache[sym] = int(s.get("quantityPrecision", 3))
                for f in s.get("filters", []):
                    if f.get("filterType") == "PRICE_FILTER":
                        self._tick_cache[sym] = float(f.get("tickSize", "0.01"))
                        break
            self._filters_load_ok   = True
            self._filters_failed_at = 0.0
        except Exception as e:
            self._filters_load_ok   = False
            self._filters_failed_at = time.time()
            logger.warning(
                f"[BinanceExchange] 載入 symbol filters 失敗，"
                f"{_FILTERS_RETRY_COOLDOWN}s 後可重試: {e}"
            )

    def _ensure_filters(self) -> None:
        """確保 tick / qty-precision 快取已載入；失敗時 _FILTERS_RETRY_COOLDOWN 秒內不重試。"""
        if getattr(self, "_filters_load_ok", False):
            return
        failed_at = getattr(self, "_filters_failed_at", 0.0)
        if failed_at and time.time() - failed_at < _FILTERS_RETRY_COOLDOWN:
            return
        self._load_symbol_filters()

    def _tick_size(self, symbol: str) -> float:
        """回傳指定交易對的最小報價單位（tick size），有快取。"""
        self._ensure_filters()
        return self._tick_cache.get(symbol, 0.01)

    @staticmethod
    def _decimals_from_tick(tick: float) -> int:
        """從 tick size 推導價格小數位數。tick <= 0 回傳保守預設 2。"""
        if tick <= 0:
            return 2
        return len(f"{tick:.10f}".rstrip("0").split(".")[-1])

    def _align_price(self, price: float, symbol: str) -> float:
        """將價格對齊到交易所要求的 tick size，避免 -4014 錯誤。"""
        tick = self._tick_size(symbol)
        if tick <= 0:
            return price
        aligned = round(round(price / tick) * tick, 10)
        return round(aligned, self._decimals_from_tick(tick))

    def _signed_request(self, method: str, path: str, params: dict) -> dict:
        """直接送簽名 REST 請求（用於 SDK 不支援的參數）"""
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        sig   = hmac.new(
            self._secret_key.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        url  = f"{self._base_path}{path}?{query}&signature={sig}"
        resp = _requests.request(
            method, url,
            headers={"X-MBX-APIKEY": self._api_key},
            timeout=10,
        )
        if not resp.ok:
            raise RuntimeError(
                f"Binance API {resp.status_code}: {resp.text}"
            )
        return resp.json()

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
                "openPrice":      str(getattr(p, "entry_price", "") or ""),
                "breakEvenPrice": str(getattr(p, "break_even_price", "") or ""),
                "positionId":     getattr(p, "symbol", ""),  # Binance 無 positionId，用 symbol 代替
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
            kwargs["price"]         = self._align_price(float(payload["price"]), symbol)
            kwargs["time_in_force"] = _map_effect(payload.get("effect", "GTC"))

        resp   = self._client.rest_api.new_order(**kwargs)
        data   = resp.data() if callable(resp.data) else resp.data
        result = {
            "orderId": str(getattr(data, "order_id", "") or ""),
            "symbol":  str(getattr(data, "symbol", "") or ""),
            "side":    str(getattr(data, "side", "") or ""),
            "status":  str(getattr(data, "status", "") or ""),
            "_raw":    data,
        }

        # SL/TP 由 runner.py 在取得實際成交價後呼叫 place_sl_tp_orders() 補掛
        return result

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

    def cancel_all_orders(self, symbol: str) -> None:
        """取消該交易對所有掛單（含一般掛單與 algo 條件單）"""
        try:
            self._client.rest_api.cancel_all_open_orders(symbol=symbol)
        except Exception:
            pass
        try:
            self._client.rest_api.cancel_all_algo_open_orders(symbol=symbol)
        except Exception:
            pass

    def place_sl_tp_orders(
        self,
        symbol: str,
        side: str,
        qty: str,
        sl_price: float,
        tp_price: float,
        position_id: str = "",
    ) -> None:
        """
        補掛 SL/TP 保護單：
          SL → algo STOP_MARKET（觸發價，市價平倉）
          TP → 限價單 reduce_only（掛在止盈價，maker 手續費）
        """
        close_side     = NewAlgoOrderSideEnum("SELL" if side == "BUY" else "BUY")
        close_side_str = "SELL" if side == "BUY" else "BUY"

        # 止損：algo STOP_MARKET
        self._client.rest_api.new_algo_order(
            algo_type="CONDITIONAL",
            symbol=symbol,
            side=close_side,
            type="STOP_MARKET",
            trigger_price=self._align_price(sl_price, symbol),
            working_type=NewAlgoOrderWorkingTypeEnum.MARK_PRICE,
            close_position="true",
        )

        # 止盈：限價單 reduce_only（maker 手續費）
        self._client.rest_api.new_order(
            symbol=symbol,
            side=NewOrderSideEnum(close_side_str),
            type="LIMIT",
            quantity=float(qty),
            price=self._align_price(tp_price, symbol),
            time_in_force="GTC",
            reduce_only="true",
        )

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
        """從快取取得數量精度（quantityPrecision 欄位）"""
        self._ensure_filters()
        return self._qty_precision_cache.get(symbol, 3)

    def get_price_precision(self, symbol: str) -> int:
        """優先從 tickSize 快取推導小數位數，避免重複呼叫 exchange_information()；
        symbol 不在 tick cache 時 fallback 至全量 API。"""
        self._ensure_filters()
        tick = self._tick_cache.get(symbol)
        if tick and tick > 0:
            return self._decimals_from_tick(tick)
        # fallback：symbol 不在 tick cache，打全量 API
        resp = self._client.rest_api.exchange_information()
        data = resp.data() if callable(resp.data) else resp.data
        for s in getattr(data, "symbols", []):
            if getattr(s, "symbol", "") == symbol:
                return int(getattr(s, "price_precision", 2))
        return 2

    def get_funding_rate(self, symbol: str) -> float:
        """取得指定交易對的當前資金費率（十進位小數，例如 0.0001 = 0.01%）"""
        try:
            url  = f"{self._base_path}/fapi/v1/premiumIndex"
            resp = _requests.get(url, params={"symbol": symbol}, timeout=5)
            if resp.ok:
                return float(resp.json().get("lastFundingRate", 0) or 0)
            return 0.0
        except Exception:
            return 0.0

    def get_tickers(self) -> list[dict]:
        """
        取得 Binance 所有 USDS-M 合約 ticker，正規化為 BaseExchange 標準格式。
        使用 /fapi/v1/ticker/24hr（不需簽名）。
        """
        url  = f"{self._base_path}/fapi/v1/ticker/24hr"
        resp = _requests.get(url, timeout=10)
        if not resp.ok:
            raise RuntimeError(f"Binance tickers {resp.status_code}: {resp.text}")
        raw    = resp.json()
        result = []
        for t in raw:
            try:
                result.append({
                    "symbol":     str(t.get("symbol", "")),
                    "last_price": float(t.get("lastPrice", 0) or 0),
                    "quote_vol":  float(t.get("quoteVolume", 0) or 0),
                    "base_vol":   float(t.get("volume", 0) or 0),
                    "high":       float(t.get("highPrice", 0) or 0),
                    "low":        float(t.get("lowPrice", 0) or 0),
                    "change_pct": float(t.get("priceChangePercent", 0) or 0),
                })
            except (TypeError, ValueError):
                continue
        return result


# ── 內部輔助 ──────────────────────────────────────────────────────────────────

def _map_effect(effect: str) -> str:
    """將 Bitunix 的 effect 字串對應到 Binance 的 timeInForce"""
    mapping = {
        "GTC":       "GTC",
        "IOC":       "IOC",
        "FOK":       "FOK",
        "POST_ONLY": "GTX",
    }
    return mapping.get(effect.upper(), "GTC")
