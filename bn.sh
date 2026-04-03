#!/bin/bash
# TGTradeX — Binance 主流幣服務管理腳本
#
# 用法: ./bn.sh {start|stop|restart|log|status}
#
#   start   — 啟動所有幣種（已在運行者自動略過）
#   stop    — 停止所有幣種
#   restart — 停止後重新啟動
#   log     — 即時查看所有 log（Ctrl-C 離開）
#   status  — 顯示各幣種運行狀態

LOG_DIR="logs"
mkdir -p "$LOG_DIR"

# ── 幣種設定 ───────────────────────────────────────────────────────────────────
# 格式：NAMES / SYMBOLS / DELAYS 三個陣列對應索引相同
NAMES=(btc eth sol bnb)
SYMBOLS=(BTCUSDT ETHUSDT SOLUSDT BNBUSDT)
DELAYS=(0 5 10 15)          # 錯開啟動秒數，避免同時打 API

COMMON_ARGS="--exchange binance --leverage 3 --risk-pct 1 --interval 4h"

# ── 工具函式 ───────────────────────────────────────────────────────────────────

pid_file() { echo "$LOG_DIR/bn_${1}.pid"; }
log_file() { echo "$LOG_DIR/bn_${1}.log"; }

is_running() {
    local pf; pf=$(pid_file "$1")
    [[ -f "$pf" ]] && kill -0 "$(cat "$pf")" 2>/dev/null
}

start_one() {
    local name="$1" symbol="$2" delay="$3"
    local pf; pf=$(pid_file "$name")
    local lf; lf=$(log_file "$name")

    if is_running "$name"; then
        echo "  [$symbol] 已在運行（PID=$(cat "$pf")），略過"
        return
    fi

    touch "$lf"
    nohup python -u run_service.py \
        $COMMON_ARGS \
        --symbol "$symbol" \
        --start-delay "$delay" \
        >> "$lf" 2>&1 &
    echo $! > "$pf"
    echo "  [$symbol] 啟動（PID=$!  log=$lf）"
}

stop_one() {
    local name="$1" symbol="$2"
    local pf; pf=$(pid_file "$name")

    if ! is_running "$name"; then
        echo "  [$symbol] 未在運行"
        rm -f "$pf"
        return
    fi

    local pid; pid=$(cat "$pf")
    if kill "$pid" 2>/dev/null; then
        echo "  [$symbol] 停止信號已送出（PID=$pid）"
    else
        echo "  [$symbol] 停止失敗（PID=$pid）"
    fi
    rm -f "$pf"
}

do_start() {
    for i in "${!NAMES[@]}"; do
        start_one "${NAMES[$i]}" "${SYMBOLS[$i]}" "${DELAYS[$i]}"
    done
}

do_stop() {
    for i in "${!NAMES[@]}"; do
        stop_one "${NAMES[$i]}" "${SYMBOLS[$i]}"
    done
}

do_status() {
    for i in "${!NAMES[@]}"; do
        local name="${NAMES[$i]}" symbol="${SYMBOLS[$i]}"
        local pf; pf=$(pid_file "$name")
        if is_running "$name"; then
            printf "  [%-10s] ✓ 運行中  PID=%-8s  log=%s\n" \
                "$symbol" "$(cat "$pf")" "$(log_file "$name")"
        else
            printf "  [%-10s] ✗ 未運行\n" "$symbol"
        fi
    done
}

# ── 主指令 ────────────────────────────────────────────────────────────────────

CMD="${1:-help}"

case "$CMD" in
    start)
        echo "▶  啟動 Binance 主流幣服務..."
        do_start
        echo "─────────────────────────────"
        do_status
        ;;

    stop)
        echo "■  停止 Binance 主流幣服務..."
        do_stop
        echo "完成。"
        ;;

    restart)
        echo "↺  重啟 Binance 主流幣服務..."
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
        LOG_FILES=()
        for name in "${NAMES[@]}"; do
            lf=$(log_file "$name")
            touch "$lf"
            LOG_FILES+=("$lf")
        done
        echo "📋 即時 Log（Ctrl-C 離開）"
        echo "─────────────────────────────"
        tail -f "${LOG_FILES[@]}"
        ;;

    status)
        echo "● 服務狀態"
        echo "─────────────────────────────"
        do_status
        ;;

    *)
        echo "用法: $0 {start|stop|restart|log|status}"
        echo ""
        echo "  start   — 啟動所有幣種（已在運行者自動略過）"
        echo "  stop    — 停止所有幣種"
        echo "  restart — 停止後重新啟動（等待 12s 確保程序退出）"
        echo "  log     — 即時查看所有 log（Ctrl-C 離開）"
        echo "  status  — 顯示各幣種運行狀態"
        exit 1
        ;;
esac
