# CLAUDE.md — Polymarket Weather Trading Bot

## Project overview
A Python bot that trades weather temperature markets on Polymarket by comparing NOAA/GFS ensemble forecasts to market prices. When weather science disagrees with market odds by >8%, the bot buys the underpriced side.

## Architecture (keep it simple)
Single Python application. No frontend. No React. No dashboard. Just a bot that runs, logs to SQLite, and sends alerts to Telegram.

```
polymarket-weather-bot/
├── CLAUDE.md              # This file
├── .env.example           # Environment variables template
├── requirements.txt       # Python dependencies
├── config.py              # All configuration in one place
├── main.py                # Entry point — run loop
├── src/
│   ├── weather.py         # Fetch GFS ensemble from Open-Meteo
│   ├── markets.py         # Fetch Polymarket weather markets via Gamma API
│   ├── signals.py         # Compare forecast vs market, calculate edge
│   ├── trader.py          # Execute trades via py-clob-client
│   ├── risk.py            # Position sizing (Kelly), daily limits
│   └── alerts.py          # Telegram notifications
├── db/
│   └── trades.db          # SQLite — created at runtime
└── tests/
    ├── test_weather.py
    ├── test_signals.py
    └── test_risk.py
```

## Tech stack
- Python 3.11+
- `py-clob-client` — Polymarket CLOB API (pip install py-clob-client)
- `httpx` — async HTTP for Open-Meteo and Gamma API
- `sqlite3` — built-in, no ORM needed
- `python-telegram-bot` — alerts
- `python-dotenv` — environment variables
- `schedule` — simple cron-like scheduling

## DO NOT use
- No React, no frontend, no dashboard
- No Docker (Railway handles this)
- No SQLAlchemy or heavy ORM — raw sqlite3 is fine
- No FastAPI or web server — this is a background worker
- No complex class hierarchies — prefer functions and simple dataclasses
- No pandas or numpy unless truly needed — stdlib math is enough

## Key data sources (all free, no API keys)

### Open-Meteo GFS Ensemble
```
GET https://ensemble-api.open-meteo.com/v1/ensemble
?latitude=40.7128&longitude=-74.006
&hourly=temperature_2m
&models=gfs_seamless
&forecast_days=2
```
Returns 31 ensemble members. Count members above/below threshold = probability.

### Polymarket Gamma API (market discovery)
```
GET https://gamma-api.polymarket.com/markets
?tag=weather&active=true&closed=false
```
Returns weather markets with condition_id, question, outcomes, prices.

### Polymarket CLOB API (trading)
Uses py-clob-client. Requires API key + secret + passphrase from Polymarket account.
Rate limit: 60 orders/minute.

## Trading logic (pseudocode)

```python
# Every 5 minutes:
for market in get_weather_markets():
    city, date, temp_range = parse_market_question(market)
    forecast_prob = get_ensemble_probability(city, date, temp_range)
    market_price = market.yes_price  # e.g. 0.15 = 15%
    
    edge = forecast_prob - market_price
    
    if edge > 0.08 and forecast_confidence > 0.70:
        size = kelly_size(edge, market_price, bankroll)
        place_order(market, side="YES", size=size)
        log_trade(market, edge, size)
        send_telegram_alert(market, edge, size)
```

## Configuration defaults (config.py)
```python
BANKROLL = 100              # Starting USDC
MIN_EDGE = 0.08             # 8% minimum edge to trade
MIN_CONFIDENCE = 0.70       # 70% ensemble agreement minimum
KELLY_FRACTION = 0.15       # 15% Kelly (conservative)
MAX_POSITION_USD = 5.0      # Max $5 per trade
MAX_DAILY_TRADES = 20       # Safety cap
MAX_DAILY_LOSS_PCT = 0.15   # Stop if down 15% today
SCAN_INTERVAL_MINUTES = 5
CITIES = {
    "new_york": {"lat": 40.7128, "lon": -74.006},
    "chicago":  {"lat": 41.8781, "lon": -87.6298},
    "miami":    {"lat": 25.7617, "lon": -80.1918},
    "london":   {"lat": 51.5074, "lon": -0.1278},
}
```

## Code style
- Type hints on all functions
- Docstrings on public functions
- f-strings for formatting
- logging module (not print)
- Dataclasses for structured data, not dicts
- Handle errors explicitly — never bare except

## Build commands
```bash
pip install -r requirements.txt
python main.py              # Run the bot
python main.py --dry-run    # Monitor mode (no real trades)
python -m pytest tests/     # Run tests
```

## Deployment
Railway with `railway.json`:
```json
{
  "build": {"builder": "NIXPACKS"},
  "deploy": {"startCommand": "python main.py"}
}
```

## Reference repos to study
- github.com/suislanchez/polymarket-kalshi-weather-bot (architecture reference)
- github.com/Polymarket/agents (official SDK patterns)
- github.com/discountry/polymarket-trading-bot (CLOB client usage)
