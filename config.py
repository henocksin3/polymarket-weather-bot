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
MIN_FORECAST_PROB = 0.05    # Never trade on <5% probability (too uncertain)
MAX_FORECAST_PROB = 0.95    # Never trade on >95% probability (too uncertain)
KELLY_FRACTION = 0.15       # 15% Kelly (conservative)
MAX_POSITION_USD = 5.0      # Max $5 per trade
MAX_DAILY_TRADES = 20       # Safety cap
MAX_DAILY_LOSS_PCT = 0.15   # Stop if down 15% today
SCAN_INTERVAL_MINUTES = 5

# Cities to monitor (using airport coordinates to match Polymarket resolution sources)
CITIES = {
    "new_york": {"lat": 40.6413, "lon": -73.7781},  # JFK Airport
    "chicago":  {"lat": 41.9742, "lon": -87.9073},  # O'Hare Intl Airport
    "miami":    {"lat": 25.7959, "lon": -80.2870},  # Miami Intl Airport
    "london":   {"lat": 51.5048, "lon": 0.0495},    # London City Airport
}

# Persistent storage — override with DATA_DIR=/data when a Railway Volume is mounted
_DATA_DIR = os.getenv("DATA_DIR", "")
DB_PATH = os.path.join(_DATA_DIR, "trades.db") if _DATA_DIR else "db/trades.db"

# Polymarket API endpoints
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"
