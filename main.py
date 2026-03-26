"""
TGTradeX 主程式

啟動方式：
    python main.py

必要環境變數（或 .env 檔案）：
    TG_BOT_TOKEN      Telegram Bot Token
    BITUNIX_API_KEY   Bitunix API Key
    BITUNIX_SECRET_KEY Bitunix Secret Key
"""
import logging

import config.settings as settings
from bot.listener import TGListener
from exchanges.bitunix.adapter import BitunixExchange
from trader.dispatcher import TradeDispatcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main() -> None:
    settings.validate()

    dispatcher = TradeDispatcher()
    dispatcher.register(
        BitunixExchange(
            api_key=settings.BITUNIX_API_KEY,
            secret_key=settings.BITUNIX_SECRET_KEY,
        )
    )

    bot = TGListener(token=settings.TG_BOT_TOKEN, dispatcher=dispatcher)
    bot.run()


if __name__ == "__main__":
    main()
