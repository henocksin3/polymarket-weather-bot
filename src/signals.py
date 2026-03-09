"""Compare ensemble forecasts to market prices and generate trading signals."""

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime

from config import (
    BANKROLL,
    KELLY_FRACTION,
    MAX_POSITION_USD,
    MIN_CONFIDENCE,
    MIN_EDGE,
)
from src.markets import WeatherMarket, parse_market_question
from src.weather import EnsembleForecast, calculate_probability

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    """A trading signal with all relevant data for execution."""
    market_id: str
    condition_id: str
    token_id: str
    question: str
    city: str
    date: date | None
    forecast_prob: float
    market_price: float
    edge: float
    confidence: float
    recommended_side: str    # "YES" or "NO"
    recommended_size: float  # USDC
    timestamp: datetime


def _fahrenheit_to_celsius(f: float) -> float:
    """Convert Fahrenheit to Celsius."""
    return (f - 32) * 5 / 9


def _kelly_size(forecast_prob: float, market_price: float) -> float:
    """Compute conservative fractional Kelly position size in USDC.

    Args:
        forecast_prob: Our estimated probability of winning (0–1).
        market_price:  Current market price for the side we're buying (0–1).

    Returns:
        Position size in USDC, capped at MAX_POSITION_USD.
    """
    if market_price <= 0 or market_price >= 1:
        return 0.0
    b = (1 / market_price) - 1   # decimal odds minus 1
    p = forecast_prob
    q = 1 - p
    kelly_f = (p * b - q) / b
    if kelly_f <= 0:
        return 0.0
    size = kelly_f * KELLY_FRACTION * BANKROLL
    return min(size, MAX_POSITION_USD)


def _target_hour_for_question(question: str) -> int:
    """Return the forecast hour to evaluate based on the market question.

    Uses word boundaries for "low" so that "below" does not incorrectly
    trigger the daily-low hour.
    """
    q = question.lower()
    if re.search(r"\blow(?:est)?\b|overnight|minimum|min\s+temp", q):
        return 6   # 6 AM — typical daily low
    return 14     # 2 PM — typical daily high (default)


def generate_signals(
    forecasts: dict[str, EnsembleForecast],
    markets: list[WeatherMarket],
) -> list[Signal]:
    """Generate trading signals by comparing ensemble forecasts to market prices.

    For each weather market outcome bucket:
      1. Match the market to a city/date in our forecast data.
      2. Calculate the ensemble probability for that temperature range.
      3. Compute edge = forecast_probability - market_price.
      4. Emit a Signal when abs(edge) > MIN_EDGE and confidence > MIN_CONFIDENCE.

    Args:
        forecasts: Dict mapping city_key to EnsembleForecast (from get_forecasts_for_cities).
        markets:   List of WeatherMarket from fetch_weather_markets.

    Returns:
        Signals sorted by abs(edge) descending.
    """
    signals: list[Signal] = []
    now = datetime.now()
    today = now.date()

    for market in markets:
        parsed = parse_market_question(market.question, market.outcomes)

        if not parsed.city_key or parsed.city_key not in forecasts:
            logger.debug(
                f"No forecast for city '{parsed.city}' — skipping market {market.market_id}"
            )
            continue
        if parsed.date is None:
            logger.debug(f"No date in market {market.market_id} — skipping")
            continue
        if (parsed.date - today).days > 2:
            logger.debug(
                f"Market date {parsed.date} is more than 2 days out — skipping {market.market_id}"
            )
            continue
        if not parsed.temperature_ranges:
            logger.debug(f"No temp ranges in market {market.market_id} — skipping")
            continue

        forecast = forecasts[parsed.city_key]
        target_hour = _target_hour_for_question(market.question)

        for i, temp_range in enumerate(parsed.temperature_ranges):
            if i >= len(market.outcome_prices):
                continue
            market_price = market.outcome_prices[i]
            if market_price <= 0:
                continue
            if market_price >= 0.99:
                logger.debug(
                    f"Market price {market_price:.1%} near ceiling — insufficient liquidity, skipping "
                    f"(market {market.market_id}, bucket {i})"
                )
                continue

            # Convert thresholds to Celsius (Open-Meteo returns °C)
            if temp_range.unit == "F":
                low_c = _fahrenheit_to_celsius(temp_range.low) if temp_range.low is not None else None
                high_c = _fahrenheit_to_celsius(temp_range.high) if temp_range.high is not None else None
            else:
                low_c = temp_range.low
                high_c = temp_range.high

            effective_low = low_c if low_c is not None else -999.0
            effective_high = high_c if high_c is not None else 999.0

            try:
                prob_result = calculate_probability(
                    forecast,
                    threshold_low=effective_low,
                    threshold_high=effective_high,
                    target_date=parsed.date,
                    target_hour=target_hour,
                )
            except ValueError as exc:
                logger.debug(
                    f"Probability calc failed for market {market.market_id}, "
                    f"bucket {i}: {exc}"
                )
                continue

            edge = prob_result.probability - market_price

            if abs(edge) < MIN_EDGE or prob_result.confidence < MIN_CONFIDENCE:
                logger.debug(
                    f"Below threshold — edge={edge:.1%}, conf={prob_result.confidence:.1%} "
                    f"(market {market.market_id}, bucket {i})"
                )
                continue

            if edge > 0:
                # Forecast says YES is underpriced — buy YES
                side = "YES"
                sizing_prob = prob_result.probability
                sizing_price = market_price
            else:
                # Forecast says YES is overpriced — buy NO
                side = "NO"
                sizing_prob = 1 - prob_result.probability
                sizing_price = 1 - market_price

            token_id = ""
            if i < len(market.tokens) and isinstance(market.tokens[i], dict):
                token_id = str(market.tokens[i].get("token_id", ""))

            size = _kelly_size(sizing_prob, sizing_price)

            signal = Signal(
                market_id=market.market_id,
                condition_id=market.condition_id,
                token_id=token_id,
                question=market.question,
                city=parsed.city_key,
                date=parsed.date,
                forecast_prob=prob_result.probability,
                market_price=market_price,
                edge=edge,
                confidence=prob_result.confidence,
                recommended_side=side,
                recommended_size=size,
                timestamp=now,
            )
            signals.append(signal)
            logger.info(
                f"Signal: {market.question[:55]} | {side} | "
                f"edge={edge:.1%} | forecast={prob_result.probability:.1%} | "
                f"price={market_price:.1%} | conf={prob_result.confidence:.1%} | "
                f"size=${size:.2f}"
            )

    signals.sort(key=lambda s: abs(s.edge), reverse=True)
    logger.info(f"Generated {len(signals)} signal(s) from {len(markets)} market(s)")
    return signals
