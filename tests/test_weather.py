"""Tests for src/weather.py"""

import json
from datetime import date
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.weather import (
    EnsembleForecast,
    ProbabilityResult,
    _parse_ensemble_response,
    calculate_probability,
    fetch_ensemble_forecast,
    get_forecasts_for_cities,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NUM_MEMBERS = 31
NUM_HOURS = 48  # 2 days


def _make_open_meteo_response(
    temps_per_member: list[list[float]] | None = None,
    base_date: str = "2026-03-08",
) -> dict:
    """Build a realistic Open-Meteo ensemble JSON response.

    If temps_per_member is None, all members get the same value (15.0°C).
    """
    times = [f"{base_date}T{h:02d}:00" for h in range(24)] + \
            [f"2026-03-09T{h:02d}:00" for h in range(24)]

    if temps_per_member is None:
        temps_per_member = [[15.0] * NUM_HOURS for _ in range(NUM_MEMBERS)]

    # Match real Open-Meteo format: control is "temperature_2m", rest are "temperature_2m_member01" etc.
    hourly: dict = {"time": times}
    for i, temps in enumerate(temps_per_member):
        key = "temperature_2m" if i == 0 else f"temperature_2m_member{i:02d}"
        hourly[key] = temps

    return {
        "latitude": 40.7,
        "longitude": -74.0,
        "hourly": hourly,
    }


@pytest.fixture
def standard_forecast_response() -> dict:
    """All members at 15.0°C."""
    return _make_open_meteo_response()


@pytest.fixture
def mixed_forecast_response() -> dict:
    """28 members in range [12, 18], 3 members outside (at 5.0°C)."""
    temps_per_member = []
    for i in range(NUM_MEMBERS):
        if i < 28:
            temps_per_member.append([15.0] * NUM_HOURS)
        else:
            temps_per_member.append([5.0] * NUM_HOURS)
    return _make_open_meteo_response(temps_per_member)


@pytest.fixture
def split_forecast_response() -> dict:
    """16 members at 15.0°C, 15 members at 5.0°C — near 50/50 split."""
    temps_per_member = []
    for i in range(NUM_MEMBERS):
        val = 15.0 if i < 16 else 5.0
        temps_per_member.append([val] * NUM_HOURS)
    return _make_open_meteo_response(temps_per_member)


# ---------------------------------------------------------------------------
# fetch_ensemble_forecast (mocked HTTP)
# ---------------------------------------------------------------------------

class TestFetchEnsembleForecast:
    def test_returns_ensemble_forecast_on_success(self, standard_forecast_response):
        mock_response = MagicMock()
        mock_response.json.return_value = standard_forecast_response
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            mock_client.get.return_value = mock_response

            result = fetch_ensemble_forecast(40.7128, -74.006)

        assert isinstance(result, EnsembleForecast)
        assert result.lat == 40.7128
        assert result.lon == -74.006
        assert len(result.members) == NUM_MEMBERS
        assert len(result.times) == NUM_HOURS

    def test_raises_on_timeout_after_retries(self):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            mock_client.get.side_effect = httpx.TimeoutException("timed out")

            with pytest.raises(httpx.TimeoutException):
                fetch_ensemble_forecast(40.7128, -74.006)

            # Should have retried 3 times
            assert mock_client.get.call_count == 3

    def test_raises_immediately_on_http_error(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404)
        )

        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            mock_client.get.return_value = mock_response

            with pytest.raises(httpx.HTTPStatusError):
                fetch_ensemble_forecast(40.7128, -74.006)

            # Should NOT retry on HTTP errors
            assert mock_client.get.call_count == 1

    def test_raises_on_missing_member_key(self):
        bad_response = _make_open_meteo_response()
        # Remove one member key
        del bad_response["hourly"]["temperature_2m_member05"]

        mock_response = MagicMock()
        mock_response.json.return_value = bad_response
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            mock_client.get.return_value = mock_response

            with pytest.raises(ValueError, match="Missing ensemble member key"):
                fetch_ensemble_forecast(40.7128, -74.006)


# ---------------------------------------------------------------------------
# calculate_probability
# ---------------------------------------------------------------------------

class TestCalculateProbability:
    TARGET_DATE = date(2026, 3, 8)
    TARGET_HOUR = 12

    def _make_forecast(self, response_data: dict) -> EnsembleForecast:
        return _parse_ensemble_response(40.7, -74.0, response_data)

    def test_probability_28_of_31(self, mixed_forecast_response):
        """28 members in range → probability ≈ 0.903"""
        forecast = self._make_forecast(mixed_forecast_response)
        result = calculate_probability(forecast, 12.0, 18.0, self.TARGET_DATE, self.TARGET_HOUR)

        assert isinstance(result, ProbabilityResult)
        assert result.count_in_range == 28
        assert result.total_members == 31
        assert result.probability == pytest.approx(28 / 31, abs=1e-6)

    def test_confidence_high_when_28_of_31(self, mixed_forecast_response):
        """confidence = max(28, 3) / 31 = 28/31 ≈ 0.903 (high)"""
        forecast = self._make_forecast(mixed_forecast_response)
        result = calculate_probability(forecast, 12.0, 18.0, self.TARGET_DATE, self.TARGET_HOUR)

        assert result.confidence == pytest.approx(28 / 31, abs=1e-6)

    def test_confidence_low_when_16_of_31(self, split_forecast_response):
        """16/31 in range → confidence = max(16, 15)/31 = 16/31 ≈ 0.516 (low)"""
        forecast = self._make_forecast(split_forecast_response)
        result = calculate_probability(forecast, 12.0, 18.0, self.TARGET_DATE, self.TARGET_HOUR)

        assert result.count_in_range == 16
        assert result.confidence == pytest.approx(16 / 31, abs=1e-6)

    def test_all_members_in_range(self, standard_forecast_response):
        """All 31 members at 15°C in [12, 18] → probability = 1.0"""
        forecast = self._make_forecast(standard_forecast_response)
        result = calculate_probability(forecast, 12.0, 18.0, self.TARGET_DATE, self.TARGET_HOUR)

        assert result.probability == 1.0
        assert result.confidence == 1.0

    def test_no_members_in_range(self, standard_forecast_response):
        """All 31 members at 15°C, range [20, 30] → probability = 0.0"""
        forecast = self._make_forecast(standard_forecast_response)
        result = calculate_probability(forecast, 20.0, 30.0, self.TARGET_DATE, self.TARGET_HOUR)

        assert result.probability == 0.0
        assert result.confidence == 1.0  # max(0, 31)/31 = 1.0

    def test_boundary_values_inclusive(self, standard_forecast_response):
        """Temperature exactly at threshold_low or threshold_high should count."""
        forecast = self._make_forecast(standard_forecast_response)
        # All members at 15.0 — test exact boundary
        result = calculate_probability(forecast, 15.0, 15.0, self.TARGET_DATE, self.TARGET_HOUR)
        assert result.probability == 1.0

    def test_raises_for_unknown_target_time(self, standard_forecast_response):
        forecast = self._make_forecast(standard_forecast_response)
        with pytest.raises(ValueError, match="not found in forecast"):
            calculate_probability(
                forecast, 12.0, 18.0,
                date(2025, 1, 1),  # far past date not in forecast
                target_hour=12,
            )


# ---------------------------------------------------------------------------
# get_forecasts_for_cities
# ---------------------------------------------------------------------------

class TestGetForecastsForCities:
    CITIES = {
        "new_york": {"lat": 40.7128, "lon": -74.006},
        "chicago":  {"lat": 41.8781, "lon": -87.6298},
    }

    def test_returns_dict_for_all_cities(self, standard_forecast_response):
        mock_response = MagicMock()
        mock_response.json.return_value = standard_forecast_response
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            mock_client.get.return_value = mock_response

            result = get_forecasts_for_cities(self.CITIES)

        assert set(result.keys()) == {"new_york", "chicago"}
        for city, forecast in result.items():
            assert isinstance(forecast, EnsembleForecast)

    def test_skips_failed_cities_and_continues(self, standard_forecast_response):
        """If one city exhausts all retries, the others should still be fetched."""
        good_response = MagicMock()
        good_response.json.return_value = standard_forecast_response
        good_response.raise_for_status = MagicMock()

        # Patch fetch_ensemble_forecast directly: first city raises, second succeeds
        cities_list = list(self.CITIES.keys())
        call_count = 0

        def mock_fetch(lat, lon, forecast_days=2):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.TimeoutException("all retries exhausted")
            return _parse_ensemble_response(lat, lon, standard_forecast_response)

        with patch("src.weather.fetch_ensemble_forecast", side_effect=mock_fetch):
            result = get_forecasts_for_cities(self.CITIES)

        # One city failed, one succeeded
        assert len(result) == 1
