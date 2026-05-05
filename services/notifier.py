"""
Telegram 交易通知器

在開倉、平倉、幣種封鎖、風控停止時發送 Telegram 訊息。
訊息透過背景執行緒發送，不阻塞交易主迴圈。

使用方式：
    notifier = TelegramNotifier(token="...", chat_id="...")
    notifier.notify_open(...)

所需環境變數：
    TG_BOT_TOKEN — Telegram Bot Token
    TG_CHAT_ID   — 接收通知的 Telegram Chat ID（個人帳號或群組）
"""

from __future__ import annotations

import logging
import threading

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 5  # Telegram API 逾時秒數


class TelegramNotifier:
    """Telegram 通知器（非同步，不阻塞交易邏輯）"""

    def __init__(self, token: str, chat_id: str) -> None:
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._chat_id = str(chat_id)

    # ── 公開介面 ──────────────────────────────────────────────────────────────

    def send(self, text: str) -> None:
        """非同步發送 Telegram 訊息（背景執行緒，失敗不拋例外）"""
        threading.Thread(target=self._send_sync, args=(text,), daemon=True).start()

    def notify_open(
        self,
        symbol: str,
        side: str,
        entry: float,
        sl: float,
        tp: float,
        strategy: str,
        qty: str,
        interval: str,
        exchange: str,
    ) -> None:
        sl_pct = abs(entry - sl) / entry * 100 if entry > 0 else 0.0
        tp_pct = abs(tp - entry) / entry * 100 if entry > 0 else 0.0
        rr = tp_pct / sl_pct if sl_pct > 0 else 0.0
        direction = "做多 📈" if side == "BUY" else "做空 📉"
        self.send(
            f"🟢 <b>開倉</b>  {symbol}\n"
            f"方向: {direction}\n"
            f"策略: {strategy}  週期: {interval}\n"
            f"進場: {entry:.4f}\n"
            f"止損: {sl:.4f}  (-{sl_pct:.1f}%)\n"
            f"止盈: {tp:.4f}  (+{tp_pct:.1f}%)\n"
            f"R:R = {rr:.1f}  數量: {qty}\n"
            f"交易所: {exchange}"
        )

    def notify_close(
        self,
        symbol: str,
        reason: str,
        entry: float,
        close: float,
        side: str,
        exchange: str,
    ) -> None:
        if entry > 0:
            pnl_pct = (close - entry) / entry * 100
            if side != "BUY":
                pnl_pct = -pnl_pct
        else:
            pnl_pct = 0.0
        emoji = "✅" if pnl_pct >= 0 else "🔴"
        sign = "+" if pnl_pct >= 0 else ""
        self.send(
            f"{emoji} <b>平倉</b>  {symbol}\n"
            f"進場: {entry:.4f}  出場: {close:.4f}\n"
            f"損益: {sign}{pnl_pct:.2f}%\n"
            f"原因: {reason}\n"
            f"交易所: {exchange}"
        )

    def notify_ban(self, symbol: str, exchange: str) -> None:
        self.send(
            f"⛔ <b>幣種封鎖</b>  {symbol}\n"
            f"此幣種不支援 API 交易，已加入黑名單。\n"
            f"交易所: {exchange}"
        )

    def notify_risk_stop(self, reason: str) -> None:
        self.send(
            f"🚨 <b>風控觸發：暫停開倉</b>\n"
            f"原因: {reason}\n"
            f"今日不再開新倉，次日 UTC 00:00 自動恢復。"
        )

    def notify_funding_short(
        self,
        symbol: str,
        funding_rate_pct: float,
        qty: str,
        entry_price: float,
        tp_price: float,
        sl_price: float,
        order_id: str,
        ack_delay_ms: int,
    ) -> None:
        tp_pct = abs(entry_price - tp_price) / entry_price * 100 if entry_price > 0 else 0.0
        sl_pct = abs(sl_price - entry_price) / entry_price * 100 if entry_price > 0 else 0.0
        self.send(
            f"🟢 <b>資金費率空單</b>  {symbol}\n"
            f"費率: {funding_rate_pct:.4f}%\n"
            f"進場參考: {entry_price:.4f}\n"
            f"止盈: {tp_price:.4f}  (-{tp_pct:.2f}%)\n"
            f"止損: {sl_price:.4f}  (+{sl_pct:.2f}%)\n"
            f"數量: {qty}  延遲: {ack_delay_ms}ms\n"
            f"訂單 ID: {order_id}"
        )

    def notify_start(self, exchange: str, mode: str, interval: str) -> None:
        self.send(
            f"🚀 <b>TGTradeX 啟動</b>\n"
            f"交易所: {exchange}  模式: {mode}\n"
            f"週期: {interval}"
        )

    # ── 內部 ──────────────────────────────────────────────────────────────────

    def _send_sync(self, text: str) -> None:
        try:
            resp = requests.post(
                self._url,
                json={
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                },
                timeout=_TIMEOUT,
            )
            if not resp.ok:
                logger.warning(
                    f"[Notifier] Telegram 發送失敗: {resp.status_code} {resp.text[:120]}"
                )
        except Exception as e:
            logger.warning(f"[Notifier] Telegram 發送異常: {e}")
