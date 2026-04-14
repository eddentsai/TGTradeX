"""
倉位持久化

將 ActivePosition 存成 JSON 檔案，服務重啟後可還原完整的 SL/TP 資訊。
檔案路徑：storage/positions/{exchange}_{symbol}.json

儲存的欄位：
  - 倉位識別：exchange, symbol, position_id, side
  - 倉位狀態：entry_price, qty, stop_loss, take_profit
  - 開倉參數：strategy_name, interval
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from services.strategies.base import ActivePosition

logger = logging.getLogger(__name__)

_STORE_DIR = Path("storage/positions")


def _path(exchange: str, symbol: str) -> Path:
    return _STORE_DIR / f"{exchange}_{symbol}.json"


def save(exchange: str, symbol: str, pos: ActivePosition) -> None:
    """開倉後儲存倉位狀態（position_id 更新時也應呼叫）"""
    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "exchange":      exchange,
        "symbol":        symbol,
        "position_id":   pos.position_id,
        "side":          pos.side,
        "entry_price":   round(pos.entry_price, 8),
        "qty":           pos.qty,
        "stop_loss":     round(pos.stop_loss, 8),
        "take_profit":   round(pos.take_profit, 8),
        "strategy_name": pos.strategy_name,
        "interval":      pos.interval,
        "peak_price":    round(pos.peak_price, 8) if pos.peak_price is not None else None,
    }
    _path(exchange, symbol).write_text(json.dumps(data, indent=2), encoding="utf-8")
    logger.debug(f"[{exchange}/{symbol}] 倉位狀態已儲存 → {_path(exchange, symbol)}")


def load(exchange: str, symbol: str) -> ActivePosition | None:
    """服務啟動時嘗試讀取上次的倉位狀態，找不到或格式錯誤回傳 None"""
    path = _path(exchange, symbol)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        peak = data.get("peak_price")
        return ActivePosition(
            position_id=data["position_id"],
            side=data["side"],
            entry_price=float(data["entry_price"]),
            qty=str(data["qty"]),
            stop_loss=float(data["stop_loss"]),
            take_profit=float(data["take_profit"]),
            strategy_name=data.get("strategy_name", "recovered"),
            exchange=data.get("exchange", exchange),
            interval=data.get("interval", ""),
            peak_price=float(peak) if peak is not None else None,
        )
    except Exception as e:
        logger.warning(f"[{exchange}/{symbol}] 讀取倉位快取失敗，忽略: {e}")
        return None


def delete(exchange: str, symbol: str) -> None:
    """平倉後刪除倉位狀態檔案"""
    try:
        _path(exchange, symbol).unlink(missing_ok=True)
        logger.debug(f"[{exchange}/{symbol}] 倉位狀態已清除")
    except Exception as e:
        logger.warning(f"[{exchange}/{symbol}] 刪除倉位快取失敗: {e}")
