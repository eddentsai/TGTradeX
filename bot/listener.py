"""
Telegram Bot 監聽器

接收 TG 訊息 → 解析 → 交給 dispatcher 執行 → 回覆結果。
使用 python-telegram-bot (v20+, asyncio 版本)。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from bot.parser import ParseError, QueryCommand, parse
from trader.models import OrderRequest

if TYPE_CHECKING:
    from trader.dispatcher import TradeDispatcher

logger = logging.getLogger(__name__)


class TGListener:
    def __init__(self, token: str, dispatcher: "TradeDispatcher") -> None:
        self._token = token
        self._dispatcher = dispatcher
        self._app = Application.builder().token(token).build()
        self._register_handlers()

    def _register_handlers(self) -> None:
        for cmd in ("buy", "sell", "open", "close"):
            self._app.add_handler(CommandHandler(cmd, self._handle_order))
        for cmd in ("account", "positions", "orders"):
            self._app.add_handler(CommandHandler(cmd, self._handle_query))

    async def _handle_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None:
            return
        text = update.message.text or ""
        try:
            req = parse(text)
            if not isinstance(req, OrderRequest):
                await update.message.reply_text("指令解析錯誤")
                return
            result = self._dispatcher.execute(req)
            order_id = result.get("orderId", "—")
            await update.message.reply_text(f"✅ 訂單送出成功\norderID: {order_id}")
        except ParseError as e:
            await update.message.reply_text(f"❌ 指令格式錯誤: {e}")
        except Exception as e:
            logger.exception("執行訂單失敗")
            await update.message.reply_text(f"❌ 執行失敗: {e}")

    async def _handle_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None:
            return
        text = update.message.text or ""
        try:
            cmd = parse(text)
            if not isinstance(cmd, QueryCommand):
                await update.message.reply_text("指令解析錯誤")
                return

            if cmd.action == "account":
                info = self._dispatcher.get_account(cmd.exchange)
                msg = (
                    f"帳戶 [{cmd.exchange}]\n"
                    f"可用: {info.get('available', '—')}\n"
                    f"未實現盈虧: {info.get('unrealizedPnl', '—')}"
                )
            elif cmd.action == "positions":
                positions = self._dispatcher.get_positions(cmd.exchange, cmd.symbol)
                if not positions:
                    msg = "（無持倉）"
                else:
                    lines = [f"持倉 [{cmd.exchange}]"]
                    for p in positions:
                        lines.append(
                            f"  {p.get('symbol')} {p.get('side')} "
                            f"qty={p.get('qty')} pnl={p.get('unrealizedPnl')}"
                        )
                    msg = "\n".join(lines)
            elif cmd.action == "orders":
                orders = self._dispatcher.get_orders(cmd.exchange, cmd.symbol)
                if not orders:
                    msg = "（無未完成訂單）"
                else:
                    lines = [f"未完成訂單 [{cmd.exchange}]"]
                    for o in orders:
                        lines.append(
                            f"  {o.get('orderId')} {o.get('side')} "
                            f"qty={o.get('qty')} price={o.get('price')}"
                        )
                    msg = "\n".join(lines)
            else:
                msg = "未知查詢"

            await update.message.reply_text(msg)
        except ParseError as e:
            await update.message.reply_text(f"❌ 指令格式錯誤: {e}")
        except Exception as e:
            logger.exception("查詢失敗")
            await update.message.reply_text(f"❌ 查詢失敗: {e}")

    def run(self) -> None:
        """啟動 bot（blocking）"""
        logger.info("TGTradeX bot 啟動中...")
        self._app.run_polling()
