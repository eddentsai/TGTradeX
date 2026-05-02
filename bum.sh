#!/bin/bash
# TGTradeX — Bitunix OI 動能做多服務管理腳本
#
# 用法: ./bum.sh {start|stop|restart|log|status}

LOG_DIR="logs"
ARCHIVE_DIR="$LOG_DIR/bum"
mkdir -p "$LOG_DIR" "$ARCHIVE_DIR"

NAME="oi_bum"
LOG_FILE="$LOG_DIR/${NAME}.log"
PID_FILE="$LOG_DIR/${NAME}.pid"

SERVICE_ARGS="--exchange bitunix --max-positions 7 \
    --leverage 4 --risk-pct 4.0 --sl-pct 32 --interval 15m --confirm-period 1h --scan-interval 900 --min-volume 20000000 --top-volatile 100 \
    --oi-exit-pct 8.0 --ls-shift-pct 15.0 --rsi-max 80 --min-sl-buffer 0 --max-ema-ext 6.0 --pre-close-sec 60 \
    --trail-activate 30 --trail-distance 20 --tp-pct 100 --max-daily-loss 20 \
    --enable-reverse --reverse-tp-pct 20 --reverse-sl-pct 5"

# ── 工具函式 ───────────────────────────────────────────────────────────────────

is_running() {
    [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

archive_log() {
    if [[ -s "$LOG_FILE" ]]; then
        local ts; ts=$(date +"%Y%m%d_%H%M%S")
        mv "$LOG_FILE" "$ARCHIVE_DIR/${NAME}_${ts}.log"
        echo "  舊 log 已封存 → $ARCHIVE_DIR/${NAME}_${ts}.log"
    fi
}

do_start() {
    if is_running; then
        echo "  已在運行（PID=$(cat "$PID_FILE")），略過"
        return
    fi

    archive_log
    touch "$LOG_FILE"
    nohup python -u run_oi_momentum.py $SERVICE_ARGS \
        > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "  啟動（PID=$!  log=$LOG_FILE）"
}

do_stop() {
    if ! is_running; then
        echo "  未在運行"
        rm -f "$PID_FILE"
        return
    fi

    local pid; pid=$(cat "$PID_FILE")
    if kill "$pid" 2>/dev/null; then
        echo "  停止信號已送出（PID=$pid）"
    else
        echo "  停止失敗（PID=$pid）"
    fi
    rm -f "$PID_FILE"
}

do_status() {
    if is_running; then
        printf "  ✓ 運行中  PID=%-8s  log=%s\n" "$(cat "$PID_FILE")" "$LOG_FILE"
    else
        printf "  ✗ 未運行\n"
    fi
}

# ── 主指令 ────────────────────────────────────────────────────────────────────

CMD="${1:-help}"

case "$CMD" in
    start)
        echo "▶  啟動 Bitunix OI 動能做多服務..."
        do_start
        echo "─────────────────────────────"
        do_status
        ;;

    stop)
        echo "■  停止 Bitunix OI 動能做多服務..."
        do_stop
        echo "完成。"
        ;;

    restart)
        echo "↺  重啟 Bitunix OI 動能做多服務..."
        echo "── 停止 ──────────────────────"
        do_stop
        echo "── 等待程序退出（12s）────────"
        sleep 12
        echo "── 啟動 ──────────────────────"
        do_start
        echo "─────────────────────────────"
        do_status
        ;;

    log)
        touch "$LOG_FILE"
        echo "📋 即時 Log（Ctrl-C 離開）"
        echo "─────────────────────────────"
        tail -f "$LOG_FILE"
        ;;

    status)
        echo "● 服務狀態"
        echo "─────────────────────────────"
        do_status
        ;;

    *)
        echo "用法: $0 {start|stop|restart|log|status}"
        exit 1
        ;;
esac
