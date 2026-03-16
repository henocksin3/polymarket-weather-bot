#!/bin/bash
# Run backtest analysis directly on Railway server

railway run bash -c "cd /app && python3 backtest_analysis.py"
