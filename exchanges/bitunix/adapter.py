"""
Bitunix 交易所 Adapter

將 BitunixClient 包裝為 BaseExchange 介面。
"""
from __future__ import annotations

import logging
import time
from typing import Any

from exchanges.base import BaseExchange
from exchanges.bitunix import BitunixClient

logger = logging.getLogger(__name__)


class BitunixExchange(BaseExchange):
    """BaseExchange 的 Bitunix 實作"""

    def __init__(self, api_key: str, secret_key: str, **kwargs: Any) -> None:
        self._client = BitunixClient(api_key=api_key, secret_key=secret_key, **kwargs)

    @property
    def name(self) -> str:
        return "bitunix"

    def get_account(self) -> dict[str, Any]:
        if self._client.futures_private is None:
            raise RuntimeError("未設定 Bitunix credentials")
        return self._client.futures_private.get_account()

    def get_pending_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if self._client.futures_private is None:
            raise RuntimeError("未設定 Bitunix credentials")
        return self._client.futures_private.get_pending_positions(symbol=symbol)

    def get_pending_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if self._client.futures_private is None:
            raise RuntimeError("未設定 Bitunix credentials")
        return self._client.futures_private.get_pending_orders(symbol=symbol)

    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._client.futures_private is None:
            raise RuntimeError("未設定 Bitunix credentials")

        sym        = payload["symbol"]
        price_prec = self.get_price_precision(sym)

        # ── 把 SL/TP 欄位全部拆出來，開倉後再分別補掛 ──────────────────────
        # SL 嵌入 place_order 時若市價已超過 SL，交易所返回 [30031] 拒絕整筆單；
        # 改成開倉成功後用 tpsl 專用端點補掛，避免此問題。
        sl_price      = payload.pop("slPrice",      None)
        sl_stop_type  = payload.pop("slStopType",   "MARK_PRICE")
        sl_order_type = payload.pop("slOrderType",  "MARKET")
        payload.pop("slOrderPrice", None)

        tp_price  = payload.pop("tpPrice",      None)
        payload.pop("tpStopType",  None)
        payload.pop("tpOrderType", None)
        payload.pop("tpOrderPrice", None)

        result = self._client.futures_private.place_order(payload)

        # 開倉後才補掛保護單
        if payload.get("tradeSide") == "OPEN":
            side       = payload["side"]
            close_side = "BUY" if side == "SELL" else "SELL"
            qty        = payload["qty"]

            # 取得 positionId（tpsl 端點必填）
            position_id = self._fetch_position_id(sym)

            # 止損：tpsl 專用端點
            if sl_price is not None and position_id:
                try:
                    self._client.futures_private.place_tpsl_order(
                        symbol=sym,
                        position_id=position_id,
                        sl_price=round(float(sl_price), price_prec),
                        sl_stop_type=sl_stop_type,
                        sl_order_type=sl_order_type,
                        sl_qty=qty,
                    )
                except Exception as e:
                    logger.warning(f"[{sym}] 補掛 SL 失敗（主單已成交）: {e}")

            # 止盈：普通限價減倉單
            if tp_price is not None:
                try:
                    self._client.futures_private.place_order({
                        "symbol":     sym,
                        "side":       close_side,
                        "orderType":  "LIMIT",
                        "qty":        qty,
                        "price":      str(round(float(tp_price), price_prec)),
                        "effect":     "GTC",
                        "reduceOnly": True,
                    })
                except Exception as e:
                    logger.warning(f"[{sym}] 補掛 TP 失敗（主單已成交）: {e}")

        return result

    def _fetch_position_id(self, symbol: str) -> str:
        """開倉後查詢倉位取得 positionId（tpsl 端點必填）"""
        time.sleep(0.3)  # 等交易所確認開倉
        try:
            positions = self._client.futures_private.get_pending_positions(symbol=symbol)
            pos = next((p for p in positions if p.get("symbol") == symbol), None)
            if pos:
                return str(pos.get("positionId", ""))
        except Exception as e:
            logger.debug(f"[{symbol}] 查詢 positionId 失敗: {e}")
        return ""

    def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        if self._client.futures_private is None:
            raise RuntimeError("未設定 Bitunix credentials")
        return self._client.futures_private.cancel_orders(
            symbol=symbol, order_list=[{"orderId": order_id}]
        )

    def place_sl_tp_orders(
        self,
        symbol: str,
        side: str,
        qty: str,
        sl_price: float,
        tp_price: float,
        position_id: str = "",
    ) -> None:
        """補掛 SL/TP 保護單（平倉方向與倉位方向相反）"""
        if self._client.futures_private is None:
            raise RuntimeError("未設定 Bitunix credentials")
        if not position_id:
            raise ValueError(f"Bitunix place_sl_tp_orders 需要 position_id（symbol={symbol}）")
        close_side = "BUY" if side == "SELL" else "SELL"
        price_prec = self.get_price_precision(symbol)
        # 止損：tpsl 專用端點（條件市價單，觸發後市價平倉）
        self._client.futures_private.place_tpsl_order(
            symbol=symbol,
            position_id=position_id,
            sl_price=round(sl_price, price_prec),
            sl_stop_type="MARK_PRICE",
            sl_order_type="MARKET",
            sl_qty=qty,
        )
        # 止盈：普通限價減倉單（直接進委託簿，maker 手續費）
        self._client.futures_private.place_order({
            "symbol":     symbol,
            "side":       close_side,
            "orderType":  "LIMIT",
            "qty":        qty,
            "price":      str(round(tp_price, price_prec)),
            "effect":     "GTC",
            "reduceOnly": True,
        })

    def cancel_all_orders(self, symbol: str) -> None:
        """取消該交易對所有掛單（一般掛單 + tpsl 條件單）"""
        if self._client.futures_private is None:
            raise RuntimeError("未設定 Bitunix credentials")
        # 一般掛單（批次取消）
        self._client.futures_private.cancel_all_orders(symbol=symbol)
        # tpsl 條件單（需逐筆取消）
        tpsl_orders = self._client.futures_private.get_pending_tpsl_orders(symbol=symbol)
        for o in tpsl_orders:
            order_id = o.get("id", "")
            if order_id:
                try:
                    self._client.futures_private.cancel_tpsl_order(
                        symbol=symbol, order_id=order_id
                    )
                except Exception:
                    pass

    def get_klines(self, symbol: str, interval: str, limit: int = 250) -> list[dict[str, Any]]:
        """
        回傳由舊到新的 K 線列表。
        Bitunix 每次最多回傳 200 根，超過時自動分段往前取。
        """
        _BITUNIX_MAX = 200
        if limit <= _BITUNIX_MAX:
            result = self._client.futures_public.get_kline(
                symbol=symbol, interval=interval, limit=limit
            )
        else:
            # 分段往前取：先取最新一批，再用最早的 time 往前繼續取
            all_candles: list[dict] = []
            end_time: int | None = None
            while len(all_candles) < limit:
                batch = self._client.futures_public.get_kline(
                    symbol=symbol,
                    interval=interval,
                    limit=_BITUNIX_MAX,
                    end_time=end_time,
                )
                if not batch:
                    break
                # Bitunix 回傳由新到舊（降序），先確認方向
                is_desc = (
                    len(batch) >= 2
                    and int(batch[0].get("time", 0)) > int(batch[-1].get("time", 0))
                )
                # 最舊的那根用來設定下一頁的 endTime
                oldest_time = int(batch[-1].get("time", 0) if is_desc else batch[0].get("time", 0))
                # 轉為由舊到新後，prepend 到累積結果
                if is_desc:
                    batch = list(reversed(batch))
                all_candles = batch + all_candles
                if end_time is not None and oldest_time >= end_time:
                    break  # 沒有更舊的資料了
                end_time = oldest_time - 1

            result = all_candles[-limit:]  # 只保留最近 limit 根

        # 確保由舊到新
        if len(result) >= 2 and result[0].get("time", 0) > result[-1].get("time", 0):
            result = list(reversed(result))
        return result

    def _get_pair_info(self, symbol: str) -> dict:
        """取得交易對規格，結果快取於 _pair_cache"""
        if not hasattr(self, "_pair_cache"):
            self._pair_cache: dict[str, dict] = {}
        if symbol not in self._pair_cache:
            pairs = self._client.futures_public.get_trading_pairs(symbol)
            if not pairs:
                raise ValueError(f"找不到交易對: {symbol}")
            self._pair_cache[symbol] = pairs[0]
        return self._pair_cache[symbol]

    def get_qty_precision(self, symbol: str) -> int:
        """從 Bitunix 合約規格取得數量精度（basePrecision 欄位）"""
        return int(self._get_pair_info(symbol).get("basePrecision", 3))

    def get_price_precision(self, symbol: str) -> int:
        """從 Bitunix 合約規格取得價格精度（quotePrecision / pricePrecision 欄位）"""
        info = self._get_pair_info(symbol)
        # Bitunix 欄位名稱依版本不同，依序嘗試
        for key in ("quotePrecision", "pricePrecision", "priceDecimal"):
            if key in info:
                return int(info[key])
        # fallback：從當前價格的小數位數推算
        price_str = str(info.get("lastPrice", "") or "")
        if "." in price_str:
            return len(price_str.rstrip("0").split(".")[-1])
        return 4  # 保守預設

    def get_funding_rate(self, symbol: str) -> float:
        """
        取得指定交易對的當前資金費率（統一回傳 decimal 格式，例如 0.0001 = 0.01%）。

        注意：Bitunix API 回傳 % 格式（0.005 = 0.005%），需除以 100 轉為 decimal。
        """
        try:
            data = self._client.futures_public.get_funding_rate(symbol)
            if not data:
                return 0.0
            # Bitunix 回傳 % 格式（例如 0.005 = 0.005%），除以 100 轉為 decimal
            rate = data.get("fundingRate", data.get("rate", 0))
            return float(rate) / 100
        except Exception:
            return 0.0

    def get_tickers(self) -> list[dict]:
        """取得所有合約 ticker，正規化為 BaseExchange 標準格式"""
        raw = self._client.futures_public.get_tickers()
        result = []
        for t in raw:
            try:
                open_price = float(t.get("open", 0) or 0)
                last_price = float(t.get("lastPrice", 0) or 0)
                change_pct = (
                    (last_price - open_price) / open_price * 100
                    if open_price > 0 else 0.0
                )
                result.append({
                    "symbol":     str(t.get("symbol", "")),
                    "last_price": last_price,
                    "quote_vol":  float(t.get("quoteVol", 0) or 0),
                    "base_vol":   float(t.get("baseVol", 0) or 0),
                    "high":       float(t.get("high", 0) or 0),
                    "low":        float(t.get("low", 0) or 0),
                    "change_pct": change_pct,
                })
            except (TypeError, ValueError):
                continue
        return result
