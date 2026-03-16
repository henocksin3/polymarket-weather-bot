"""Export trades data from database for backtest analysis."""

import os
import sqlite3
import json

DB_PATH = os.getenv("DB_PATH", "db/trades.db")

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Get all resolved trades
cursor.execute("""
    SELECT city, side, edge, forecast_prob, market_price, size, outcome, pnl
    FROM trades
    WHERE outcome IN ('win', 'loss')
    ORDER BY timestamp ASC
""")

trades = cursor.fetchall()
conn.close()

print(json.dumps(trades, indent=2))
