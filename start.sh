# BTC 立即啟動
#nohup python -u run_service.py \
#  --exchange bitunix --symbol BTCUSDT --leverage 4 --risk-pct 5 --interval 15m \
#  > logs/btc.log 2>&1 & echo $! > logs/btc.pid

# ETH 延遲 3 秒啟動
#nohup python -u run_service.py \
#  --exchange bitunix --symbol ETHUSDT --leverage 4 --risk-pct 5 --interval 15m \
#  --start-delay 3 \
#  > logs/eth.log 2>&1 & echo $! > logs/eth.pid

# SOL 延遲 6 秒啟動
#nohup python -u run_service.py \
#  --exchange bitunix --symbol SOLUSDT --leverage 4 --risk-pct 5 --interval 15m \
#  --start-delay 6 \
#  > logs/sol.log 2>&1 & echo $! > logs/sol.pid

#nohup python -u run_service.py \
#  --exchange bitunix --symbol BNBUSDT --leverage 4 --risk-pct 5 --interval 15m \
#  --start-delay 9 \
#  > logs/bnb.log 2>&1 & echo $! > logs/bnb.pid


#nohup python run_auto.py --exchange bitunix --max-positions 4 --min-volume=100000 \
#	--leverage 4 --risk-pct 5 --interval 1h --scan-interval 14400 \
#	> logs/auto_bu.log 2>&1 & echo $! > logs/auto_bu.pid

nohup python run_mix_strategies.py --exchange bitunix --max-positions 3 --min-volume=100000 \
        --leverage 4 --risk-pct 0.8 --interval 1h --scan-interval 14400 \
        > logs/auto_bu.log 2>&1 & echo $! > logs/auto_bu.pid
