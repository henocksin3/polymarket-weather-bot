import os
from dotenv import load_dotenv

load_dotenv()

# Polymarket API
POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_API_SECRET = os.getenv("POLYMARKET_API_SECRET", "")
POLYMARKET_API_PASSPHRASE = os.getenv("POLYMARKET_API_PASSPHRASE", "")
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Trading parameters
BANKROLL = float(os.getenv("BANKROLL", "100"))
MIN_EDGE = 0.08             # 8% minimum edge to trade
MIN_CONFIDENCE = 0.70       # 70% ensemble agreement minimum
KELLY_FRACTION = 0.15       # 15% Kelly (conservative)
MAX_POSITION_USD = 5.0      # Max $5 per trade
MAX_DAILY_TRADES = 20       # Safety cap
MAX_DAILY_LOSS_PCT = 0.15   # Stop if down 15% today
SCAN_INTERVAL_MINUTES = 5

# Cities to monitor
CITIES = {
    "new_york": {"lat": 40.7128, "lon": -74.0060},
    "chicago":  {"lat": 41.8781, "lon": -87.6298},
    "miami":    {"lat": 25.7617, "lon": -80.1918},
    "london":   {"lat": 51.5074, "lon": -0.1278},
}

# Persistent storage — override with DATA_DIR=/data when a Railway Volume is mounted
_DATA_DIR = os.getenv("DATA_DIR", "")
DB_PATH = os.path.join(_DATA_DIR, "trades.db") if _DATA_DIR else "db/trades.db"

# Polymarket API endpoints
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"
