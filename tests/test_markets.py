"""Tests for src/markets.py."""

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.markets import (
    ParsedMarket,
    TemperatureRange,
    WeatherMarket,
    fetch_weather_markets,
    get_market_prices,
    parse_market_question,
    _parse_temperature_range,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOCK_MARKET_1 = {
    "id": "market-001",
    "conditionId": "0xabc123",
    "question": "What will the high temperature be in New York City on March 10, 2026?",
    "outcomes": json.dumps(["<40°F", "40-50°F", "50-60°F", "60-70°F", ">70°F"]),
    "outcomePrices": json.dumps(["0.05", "0.20", "0.40", "0.25", "0.10"]),
    "tokens": [
        {"token_id": "tok-001", "outcome": "<40°F"},
        {"token_id": "tok-002", "outcome": "40-50°F"},
        {"token_id": "tok-003", "outcome": "50-60°F"},
        {"token_id": "tok-004", "outcome": "60-70°F"},
        {"token_id": "tok-005", "outcome": ">70°F"},
    ],
    "active": True,
    "closed": False,
    "tags": [{"label": "weather"}],
}

MOCK_MARKET_2 = {
    "id": "market-002",
    "conditionId": "0xdef456",
    "question": "Will the high temperature in Chicago be above 32°F on March 15, 2026?",
    "outcomes": json.dumps(["Yes", "No"]),
    "outcomePrices": json.dumps(["0.72", "0.28"]),
    "tokens": [
        {"token_id": "tok-010", "outcome": "Yes"},
        {"token_id": "tok-011", "outcome": "No"},
    ],
    "active": True,
    "closed": False,
    "tags": [{"label": "weather"}],
}

NON_WEATHER_MARKET = {
    "id": "market-999",
    "conditionId": "0xfff000",
    "question": "Will Bitcoin reach $100k by end of 2026?",
    "outcomes": json.dumps(["Yes", "No"]),
    "outcomePrices": json.dumps(["0.50", "0.50"]),
    "tokens": [],
    "active": True,
    "closed": False,
    "tags": [{"label": "crypto"}],
}


# ---------------------------------------------------------------------------
# parse_market_question — 5 different question formats
# ---------------------------------------------------------------------------

class TestParseMarketQuestion:

    def test_full_date_new_york_city_with_outcomes(self):
        """Standard format: city name, full date, outcomes provided."""
        q = "What will the high temperature be in New York City on March 10, 2026?"
        outcomes = ["<40°F", "40-50°F", "50-60°F", "60-70°F", ">70°F"]
        result = parse_market_question(q, outcomes=outcomes)

        assert result.city_key == "new_york"
        assert result.date == date(2026, 3, 10)
        assert len(result.temperature_ranges) == 5
        assert result.temperature_ranges[0].low is None
        assert result.temperature_ranges[0].high == 40.0
        assert result.temperature_ranges[2].low == 50.0
        assert result.temperature_ranges[2].high == 60.0
        assert result.temperature_ranges[4].low == 70.0
        assert result.temperature_ranges[4].high is None

    def test_yes_no_above_threshold_chicago(self):
        """Yes/No market with 'above X°F' phrasing."""
        q = "Will the high temperature in Chicago be above 32°F on March 15, 2026?"
        result = parse_market_question(q)

        assert result.city_key == "chicago"
        assert result.date == date(2026, 3, 15)

    def test_partial_date_no_year(self):
        """Date without year — should resolve to nearest future date."""
        q = "London temperature on April 5th — will it be above 10°C?"
        result = parse_market_question(q)

        assert result.city_key == "london"
        assert result.date is not None
        assert result.date.month == 4
        assert result.date.day == 5

    def test_numeric_date_format_miami(self):
        """MM/DD/YYYY numeric date format."""
        q = "Miami high temp on 03/20/2026: above or below 75°F?"
        result = parse_market_question(q)

        assert result.city_key == "miami"
        assert result.date == date(2026, 3, 20)

    def test_below_freezing_new_york(self):
        """'below freezing' phrasing — city and date extraction."""
        q = "Will it be below freezing in New York on January 15, 2027?"
        result = parse_market_question(q)

        assert result.city_key == "new_york"
        assert result.date == date(2027, 1, 15)

    def test_unknown_city_returns_empty_city_key(self):
        """City not in our config should give city_key=None."""
        q = "Will it be above 80°F in Los Angeles on June 1, 2026?"
        result = parse_market_question(q)

        assert result.city_key is None
        assert result.date == date(2026, 6, 1)

    def test_inline_range_without_outcomes(self):
        """When no outcomes given, parse ranges from question text."""
        q = "Will NYC temperature be between 50 and 60°F on April 1, 2026?"
        result = parse_market_question(q)

        assert result.city_key == "new_york"
        assert len(result.temperature_ranges) >= 1
        assert result.temperature_ranges[0].low == 50.0
        assert result.temperature_ranges[0].high == 60.0

    def test_binary_yes_no_celsius_or_below(self):
        """Binary Yes/No: 'X°C or below' → TemperatureRange(low=None, high=X)."""
        q = "Will the highest temperature in London be 11°C or below on March 8?"
        result = parse_market_question(q, outcomes=["Yes", "No"])

        assert result.city_key == "london"
        assert len(result.temperature_ranges) == 1
        assert result.temperature_ranges[0].low is None
        assert result.temperature_ranges[0].high == 11.0
        assert result.temperature_ranges[0].unit == "C"

    def test_binary_yes_no_exact_celsius(self):
        """Binary Yes/No: 'be X°C' → TemperatureRange(low=X, high=X)."""
        q = "Will the highest temperature in London be 12°C on March 8?"
        result = parse_market_question(q, outcomes=["Yes", "No"])

        assert result.city_key == "london"
        assert len(result.temperature_ranges) == 1
        assert result.temperature_ranges[0].low == 12.0
        assert result.temperature_ranges[0].high == 12.0
        assert result.temperature_ranges[0].unit == "C"

    def test_binary_yes_no_above_fahrenheit_with_outcomes(self):
        """Binary Yes/No: 'above X°F' with outcomes provided."""
        q = "Will the high temperature in Chicago be above 32°F on March 15, 2026?"
        result = parse_market_question(q, outcomes=["Yes", "No"])

        assert result.city_key == "chicago"
        assert result.date == date(2026, 3, 15)
        assert len(result.temperature_ranges) == 1
        assert result.temperature_ranges[0].low == 32.0
        assert result.temperature_ranges[0].high is None
        assert result.temperature_ranges[0].unit == "F"


# ---------------------------------------------------------------------------
# _parse_temperature_range
# ---------------------------------------------------------------------------

class TestParseTemperatureRange:

    def test_range_50_60(self):
        r = _parse_temperature_range("50-60°F")
        assert r.low == 50.0
        assert r.high == 60.0
        assert r.unit == "F"

    def test_less_than(self):
        r = _parse_temperature_range("<40°F")
        assert r.low is None
        assert r.high == 40.0

    def test_greater_than(self):
        r = _parse_temperature_range(">70°F")
        assert r.low == 70.0
        assert r.high is None

    def test_below_prefix(self):
        r = _parse_temperature_range("Below 32°F")
        assert r.low is None
        assert r.high == 32.0

    def test_above_prefix(self):
        r = _parse_temperature_range("Above 90°F")
        assert r.low == 90.0
        assert r.high is None

    def test_celsius_unit(self):
        r = _parse_temperature_range("10-20°C")
        assert r.unit == "C"
        assert r.low == 10.0
        assert r.high == 20.0

    def test_negative_low(self):
        r = _parse_temperature_range("-10-0°C")
        assert r.low == -10.0
        assert r.high == 0.0


# ---------------------------------------------------------------------------
# fetch_weather_markets — mocked Gamma API
# ---------------------------------------------------------------------------

class TestFetchWeatherMarkets:

    @patch("src.markets._http_get")
    def test_returns_weather_markets_only(self, mock_get):
        """Non-weather markets are filtered out."""
        mock_get.return_value = [MOCK_MARKET_1, MOCK_MARKET_2, NON_WEATHER_MARKET]

        markets = fetch_weather_markets()

        assert len(markets) == 2
        assert all(isinstance(m, WeatherMarket) for m in markets)
        assert markets[0].market_id == "market-001"
        assert markets[1].market_id == "market-002"

    @patch("src.markets._http_get")
    def test_parses_json_string_outcomes(self, mock_get):
        """outcomes and outcomePrices may be JSON strings — must be decoded."""
        mock_get.return_value = [MOCK_MARKET_1]

        markets = fetch_weather_markets()

        assert markets[0].outcomes == ["<40°F", "40-50°F", "50-60°F", "60-70°F", ">70°F"]
        assert len(markets[0].outcome_prices) == 5
        assert abs(markets[0].outcome_prices[2] - 0.40) < 1e-9

    @patch("src.markets._http_get")
    def test_empty_page_stops_pagination(self, mock_get):
        """Empty response page terminates the pagination loop."""
        mock_get.return_value = []

        markets = fetch_weather_markets()

        assert markets == []

    @patch("src.markets._http_get")
    def test_http_error_returns_empty_list(self, mock_get):
        """HTTP errors are caught and an empty list is returned."""
        import httpx
        mock_get.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock(status_code=500)
        )

        markets = fetch_weather_markets()

        assert markets == []


# ---------------------------------------------------------------------------
# get_market_prices — mocked Gamma API
# ---------------------------------------------------------------------------

class TestGetMarketPrices:

    @patch("src.markets._http_get")
    def test_returns_token_price_mapping(self, mock_get):
        mock_get.return_value = [MOCK_MARKET_1]

        prices = get_market_prices("0xabc123")

        assert prices["tok-001"] == pytest.approx(0.05)
        assert prices["tok-003"] == pytest.approx(0.40)
        assert prices["tok-005"] == pytest.approx(0.10)

    @patch("src.markets._http_get")
    def test_raises_value_error_for_missing_market(self, mock_get):
        mock_get.return_value = []

        with pytest.raises(ValueError, match="No market found"):
            get_market_prices("0xnonexistent")

    @patch("src.markets._http_get")
    def test_two_outcome_market(self, mock_get):
        mock_get.return_value = [MOCK_MARKET_2]

        prices = get_market_prices("0xdef456")

        assert prices["tok-010"] == pytest.approx(0.72)
        assert prices["tok-011"] == pytest.approx(0.28)
