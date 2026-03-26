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


def validate() -> None:
    """啟動前驗證必要設定，缺少時拋出 RuntimeError"""
    missing = []
    if not TG_BOT_TOKEN:
        missing.append("TG_BOT_TOKEN")
    if not BITUNIX_API_KEY:
        missing.append("BITUNIX_API_KEY")
    if not BITUNIX_SECRET_KEY:
        missing.append("BITUNIX_SECRET_KEY")
    if missing:
        raise RuntimeError(f"缺少必要環境變數: {', '.join(missing)}")
