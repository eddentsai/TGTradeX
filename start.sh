#!/bin/bash
nohup python run_mix_strategies.py --exchange bitunix --max-positions 3 --min-volume=100000 \
        --leverage 4 --risk-pct 0.8 --interval 1h --scan-interval 14400 \
        > logs/auto_bu.log 2>&1 & echo $! > logs/auto_bu.pid
