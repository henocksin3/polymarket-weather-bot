"""Fetch and parse Polymarket weather markets via the Gamma API."""

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime

import httpx

from config import GAMMA_API_BASE

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 30
MAX_RETRIES = 3

# Map city names found in questions to config keys
CITY_NAME_MAP: dict[str, str] = {
    "new york city": "new_york",
    "new york": "new_york",
    "nyc": "new_york",
    "chicago": "chicago",
    "miami": "miami",
    "london": "london",
}

MONTH_MAP: dict[str, int] = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


@dataclass
class TemperatureRange:
    """A temperature bucket, e.g. '50-60°F'."""
    low: float | None   # None = -infinity
    high: float | None  # None = +infinity
    label: str
    unit: str = "F"


@dataclass
class WeatherMarket:
    """A Polymarket weather market."""
    market_id: str
    condition_id: str
    question: str
    outcomes: list[str]
    outcome_prices: list[float]
    tokens: list[dict]
    active: bool


@dataclass
class ParsedMarket:
    """Parsed data extracted from a market question."""
    city: str               # Raw city name found in question
    city_key: str | None    # Key in CITIES config (e.g. "new_york")
    date: date | None
    temperature_ranges: list[TemperatureRange]
    original_question: str


def _http_get(url: str, params: dict | None = None) -> list | dict:
    """GET with retry on timeout."""
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException as exc:
            logger.warning(f"Timeout on attempt {attempt}: {exc}")
            last_exc = exc
        except httpx.HTTPStatusError as exc:
            logger.error(f"HTTP error {exc.response.status_code}: {exc}")
            raise
    raise httpx.TimeoutException(f"All {MAX_RETRIES} attempts timed out") from last_exc


def _parse_json_field(value: str | list) -> list:
    """Handle Gamma API fields that may be JSON strings or already lists."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
    return value or []


def _is_weather_market(raw: dict) -> bool:
    """Return True if this market is weather-related."""
    question = (raw.get("question") or "").lower()
    tags = raw.get("tags") or []

    if isinstance(tags, list):
        for tag in tags:
            tag_label = tag.get("label", "") if isinstance(tag, dict) else str(tag)
            if "weather" in tag_label.lower():
                return True

    weather_keywords = [
        "temperature", "high temp", "low temp", "°f", "°c",
        "weather", "fahrenheit", "celsius", "freezing",
    ]
    return any(kw in question for kw in weather_keywords)


def _parse_market_data(raw: dict) -> WeatherMarket | None:
    """Parse a raw Gamma API market dict into WeatherMarket."""
    try:
        outcomes = _parse_json_field(raw.get("outcomes", []))
        prices_raw = _parse_json_field(raw.get("outcomePrices", []))
        outcome_prices = [float(p) for p in prices_raw]
        tokens_raw = raw.get("tokens", [])
        if isinstance(tokens_raw, str):
            tokens_raw = json.loads(tokens_raw)
        return WeatherMarket(
            market_id=str(raw.get("id", "")),
            condition_id=str(raw.get("conditionId", "")),
            question=raw.get("question", ""),
            outcomes=outcomes,
            outcome_prices=outcome_prices,
            tokens=tokens_raw or [],
            active=bool(raw.get("active", False)),
        )
    except Exception as exc:
        logger.warning(f"Failed to parse market {raw.get('id')}: {exc}")
        return None


def fetch_weather_markets() -> list[WeatherMarket]:
    """Fetch active weather markets from Polymarket Gamma API.

    Queries for markets tagged 'weather' and additionally filters by
    temperature keywords in the question text.

    Returns:
        List of WeatherMarket dataclasses.
    """
    markets: list[WeatherMarket] = []
    limit = 100
    offset = 0

    while True:
        try:
            data = _http_get(
                f"{GAMMA_API_BASE}/markets",
                params={
                    "active": "true",
                    "closed": "false",
                    "tag": "weather",
                    "limit": limit,
                    "offset": offset,
                },
            )
        except Exception as exc:
            logger.error(f"Failed to fetch markets at offset={offset}: {exc}")
            break

        # Gamma API may return a list directly or wrap in a dict
        page: list = data if isinstance(data, list) else data.get("data", data.get("markets", []))

        if not page:
            break

        for raw in page:
            if not _is_weather_market(raw):
                continue
            market = _parse_market_data(raw)
            if market:
                markets.append(market)

        if len(page) < limit:
            break
        offset += limit

    logger.info(f"Fetched {len(markets)} weather markets")
    return markets


def _extract_city(text: str) -> tuple[str, str | None]:
    """Extract city name and config key from question text.

    Returns (raw_city_name, city_key) — city_key is None if not in CITIES config.
    """
    text_lower = text.lower()
    # Sort by length descending so "new york city" matches before "new york"
    for city_name in sorted(CITY_NAME_MAP, key=len, reverse=True):
        if city_name in text_lower:
            return city_name.title(), CITY_NAME_MAP[city_name]
    return "", None


def _extract_date(text: str) -> date | None:
    """Extract a date from question text. Handles several common formats."""
    today = datetime.now().date()
    months_pattern = "|".join(MONTH_MAP)

    # "March 10, 2026" or "March 10th, 2026"
    full_re = re.compile(
        rf"\b({months_pattern})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b",
        re.IGNORECASE,
    )
    m = full_re.search(text)
    if m:
        try:
            return date(int(m.group(3)), MONTH_MAP[m.group(1).lower()], int(m.group(2)))
        except ValueError:
            pass

    # "March 10" or "March 10th" — use nearest future year
    partial_re = re.compile(
        rf"\b({months_pattern})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b",
        re.IGNORECASE,
    )
    m = partial_re.search(text)
    if m:
        month = MONTH_MAP[m.group(1).lower()]
        day = int(m.group(2))
        for year in [today.year, today.year + 1]:
            try:
                d = date(year, month, day)
                if d >= today:
                    return d
            except ValueError:
                continue

    # MM/DD/YYYY or MM-DD-YYYY
    numeric_re = re.compile(r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})\b")
    m = numeric_re.search(text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass

    return None


def _is_binary_yes_no(outcomes: list[str]) -> bool:
    """Return True when outcomes are just ['Yes', 'No'] (no temp info)."""
    if len(outcomes) != 2:
        return False
    return {o.strip().lower() for o in outcomes} == {"yes", "no"}


def _extract_condition_from_question(text: str) -> "TemperatureRange | None":
    """Extract the YES temperature condition from a binary Yes/No market question.

    Tries patterns in order of specificity:
      - "X°C/F or below"  → TemperatureRange(low=None, high=X)
      - "X°C/F or above"  → TemperatureRange(low=X,    high=None)
      - "above/over X°C/F"→ TemperatureRange(low=X,    high=None)
      - "below/under X°C/F"→ TemperatureRange(low=None, high=X)
      - "X-Y°C/F" range   → TemperatureRange(low=X,    high=Y)
      - exact "X°C/F"     → TemperatureRange(low=X,    high=X)

    Returns None if no temperature could be extracted.
    """
    unit = "C" if re.search(r"[°°]C|celsius", text, re.IGNORECASE) else "F"

    # "11°C or below"
    m = re.search(r"(-?\d+\.?\d*)\s*[°°][CF]?\s*or\s+below", text, re.IGNORECASE)
    if m:
        return TemperatureRange(low=None, high=float(m.group(1)), label=m.group(0), unit=unit)

    # "11°C or above"
    m = re.search(r"(-?\d+\.?\d*)\s*[°°][CF]?\s*or\s+above", text, re.IGNORECASE)
    if m:
        return TemperatureRange(low=float(m.group(1)), high=None, label=m.group(0), unit=unit)

    # "above/over/more than X°C/F"
    m = re.search(r"\b(?:above|over|more\s+than)\s+(-?\d+\.?\d*)\s*[°°][CF]?", text, re.IGNORECASE)
    if m:
        return TemperatureRange(low=float(m.group(1)), high=None, label=m.group(0), unit=unit)

    # "below/under/less than X°C/F"
    m = re.search(r"\b(?:below|under|less\s+than)\s+(-?\d+\.?\d*)\s*[°°][CF]?", text, re.IGNORECASE)
    if m:
        return TemperatureRange(low=None, high=float(m.group(1)), label=m.group(0), unit=unit)

    # Range: "X to Y°C/F" or "X-Y°C/F"
    m = re.search(
        r"(-?\d+\.?\d*)\s*[°°]?[CF]?\s*(?:to|-)\s*(\d+\.?\d*)\s*[°°][CF]?",
        text,
        re.IGNORECASE,
    )
    if m:
        return TemperatureRange(
            low=float(m.group(1)), high=float(m.group(2)), label=m.group(0), unit=unit
        )

    # Exact single value: "12°C"
    m = re.search(r"(-?\d+\.?\d*)\s*[°°][CF]", text, re.IGNORECASE)
    if m:
        v = float(m.group(1))
        return TemperatureRange(low=v, high=v, label=m.group(0), unit=unit)

    return None


def _parse_temperature_range(label: str) -> TemperatureRange:
    """Parse a temperature bucket label into a TemperatureRange.

    Handles formats like: '50-60°F', '<40°F', '>70°F', 'below 32°F',
    'above 90°F', '-10 to 0°C'.
    """
    unit = "C" if re.search(r"[°°]C|celsius", label, re.IGNORECASE) else "F"
    # Strip unit notation for numeric work
    text = re.sub(r"[°°][FC]?", "", label, flags=re.IGNORECASE).strip()

    # Less-than patterns: "<40", "below 40", "under 40", "less than 40"
    if re.match(r"^[<(]|^below\b|^under\b|^less\s+than\b", text, re.IGNORECASE):
        val = re.search(r"-?\d+\.?\d*", text)
        if val:
            return TemperatureRange(low=None, high=float(val.group()), label=label, unit=unit)

    # Greater-than patterns: ">70", "above 70", "over 70", "more than 70"
    if re.match(r"^[>)]|^above\b|^over\b|^more\s+than\b", text, re.IGNORECASE):
        val = re.search(r"-?\d+\.?\d*", text)
        if val:
            return TemperatureRange(low=float(val.group()), high=None, label=label, unit=unit)

    # Range: "50-60", "50 to 60", "-10-0" (negative lower bound)
    m = re.search(r"(-?\d+\.?\d*)\s*(?:to|-)\s*(\d+\.?\d*)", text)
    if m:
        return TemperatureRange(
            low=float(m.group(1)), high=float(m.group(2)), label=label, unit=unit
        )

    # Single value fallback
    val = re.search(r"-?\d+\.?\d*", text)
    if val:
        v = float(val.group())
        return TemperatureRange(low=v, high=v, label=label, unit=unit)

    return TemperatureRange(low=None, high=None, label=label, unit=unit)


def parse_market_question(
    question_text: str,
    outcomes: list[str] | None = None,
) -> ParsedMarket:
    """Parse a Polymarket weather market question.

    Args:
        question_text: The market question string.
        outcomes: Outcome labels from the market (e.g. ["<50°F", "50-60°F"]).
                  When provided, temperature_ranges are built from these.

    Returns:
        ParsedMarket with extracted city, date, and temperature_ranges.
    """
    city, city_key = _extract_city(question_text)
    target_date = _extract_date(question_text)

    if outcomes and not _is_binary_yes_no(outcomes):
        # Multi-bucket market: each outcome label is a temperature range
        temperature_ranges = [_parse_temperature_range(o) for o in outcomes]
    elif outcomes and _is_binary_yes_no(outcomes):
        # Binary Yes/No market: extract the YES condition from the question text
        condition = _extract_condition_from_question(question_text)
        temperature_ranges = [condition] if condition is not None else []
    else:
        # Fall back to scanning the question text for inline ranges
        temp_re = re.compile(
            r"(\d+\.?\d*)\s*[°°]?[FC]?\s*(?:to|and|[-–])\s*(\d+\.?\d*)\s*[°°]?[FC]?",
            re.IGNORECASE,
        )
        temperature_ranges = [
            TemperatureRange(
                low=float(m.group(1)),
                high=float(m.group(2)),
                label=m.group(0),
            )
            for m in temp_re.finditer(question_text)
        ]

    return ParsedMarket(
        city=city,
        city_key=city_key,
        date=target_date,
        temperature_ranges=temperature_ranges,
        original_question=question_text,
    )


def get_market_prices(condition_id: str) -> dict[str, float]:
    """Fetch current outcome prices for a market by condition ID.

    Args:
        condition_id: Polymarket condition ID (hex string).

    Returns:
        Dict mapping token_id (str) to current price (0.0–1.0).

    Raises:
        ValueError: If no market is found for the given condition_id.
        httpx.HTTPStatusError: On API errors.
    """
    data = _http_get(
        f"{GAMMA_API_BASE}/markets",
        params={"conditionId": condition_id},
    )

    markets_list: list = data if isinstance(data, list) else data.get("data", data.get("markets", []))

    if not markets_list:
        raise ValueError(f"No market found for conditionId={condition_id}")

    raw = markets_list[0]
    tokens = raw.get("tokens", [])
    if isinstance(tokens, str):
        tokens = json.loads(tokens)

    prices_raw = _parse_json_field(raw.get("outcomePrices", []))
    prices = [float(p) for p in prices_raw]

    result: dict[str, float] = {}
    for i, token in enumerate(tokens):
        token_id = str(token.get("token_id", ""))
        if token_id and i < len(prices):
            result[token_id] = prices[i]

    logger.debug(f"Fetched {len(result)} token prices for conditionId={condition_id}")
    return result
