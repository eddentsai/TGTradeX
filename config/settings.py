"""
設定管理

從環境變數讀取所有設定，支援 .env 檔案（需安裝 python-dotenv）。
"""

from __future__ import annotations

import os


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


_load_dotenv()


# ── Telegram ──────────────────────────────────────────────────────────────────
TG_BOT_TOKEN: str = os.environ.get("TG_BOT_TOKEN", "")

# ── Bitunix ───────────────────────────────────────────────────────────────────
BITUNIX_API_KEY: str = os.environ.get("BITUNIX_API_KEY", "")
BITUNIX_SECRET_KEY: str = os.environ.get("BITUNIX_SECRET_KEY", "")

# ── Binance ───────────────────────────────────────────────────────────────────
BINANCE_API_KEY: str = os.environ.get("BINANCE_API_KEY", "")
BINANCE_SECRET_KEY: str = os.environ.get("BINANCE_SECRET_KEY", "")

# ── Redis（可選，用於黑名單持久化）────────────────────────────────────────────
REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


_EXCHANGE_KEYS: dict[str, tuple[str, str]] = {
    "bitunix": ("BITUNIX_API_KEY", "BITUNIX_SECRET_KEY"),
    "binance": ("BINANCE_API_KEY", "BINANCE_SECRET_KEY"),
}


def validate(exchange: str = "bitunix") -> None:
    """啟動前驗證必要設定，缺少時拋出 RuntimeError"""
    missing = []
    if not TG_BOT_TOKEN:
        missing.append("TG_BOT_TOKEN")

    key_name, secret_name = _EXCHANGE_KEYS.get(exchange, ("", ""))
    if key_name and not os.environ.get(key_name):
        missing.append(key_name)
    if secret_name and not os.environ.get(secret_name):
        missing.append(secret_name)

    if missing:
        raise RuntimeError(f"缺少必要環境變數: {', '.join(missing)}")
