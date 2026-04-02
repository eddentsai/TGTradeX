#!/bin/bash
# BTC 延遲 0 秒啟動
nohup python -u run_service.py \
  --exchange binance --symbol BTCUSDT --leverage 3 --risk-pct 1 --interval 1h \
  > logs/bn_btc.log 2>&1 & echo $! > logs/bn_btc.pid

# ETH 延遲 1 秒啟動
nohup python -u run_service.py \
  --exchange binance --symbol ETHUSDT --leverage 3 --risk-pct 1 --interval 1h \
  --start-delay 1 \
  > logs/bn_eth.log 2>&1 & echo $! > logs/bn_eth.pid

# SOL 延遲 2 秒啟動
nohup python -u run_service.py \
  --exchange binance --symbol SOLUSDT --leverage 3 --risk-pct 1 --interval 1h \
  --start-delay 2 \
  > logs/bn_sol.log 2>&1 & echo $! > logs/bn_sol.pid

# BNB 延遲 3 秒啟動
nohup python -u run_service.py \
  --exchange binance --symbol BNBUSDT --leverage 4 --risk-pct 4 --interval 1h \
  --start-delay 3 \
  > logs/bn_bnb.log 2>&1 & echo $! > logs/bn_bnb.pid
