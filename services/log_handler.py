import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

def setup_logging() -> None:
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)

    file_handler = TimedRotatingFileHandler(
        filename=log_dir / "funding_watcher.log",
        when="midnight",      # 每天切一檔
        interval=1,
        backupCount=14,       # 保留14天
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(file_handler)
