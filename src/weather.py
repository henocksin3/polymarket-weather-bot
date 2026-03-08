"""Fetch GFS ensemble forecasts from Open-Meteo and calculate weather probabilities."""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

import httpx

logger = logging.getLogger(__name__)

ENSEMBLE_API_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
NUM_MEMBERS = 31
TIMEOUT_SECONDS = 30
MAX_RETRIES = 3


@dataclass
class EnsembleForecast:
    """Holds raw ensemble forecast data for a location."""
    lat: float
    lon: float
    times: list[str]              # ISO timestamps (e.g. "2024-03-10T14:00")
    members: list[list[float]]    # 31 lists, each with hourly temperatures


@dataclass
class ProbabilityResult:
    """Result of probability calculation over ensemble members."""
    probability: float    # Fraction of members in the temperature range
    confidence: float     # How strongly members agree (max agreement / total)
    count_in_range: int
    total_members: int


def fetch_ensemble_forecast(lat: float, lon: float, forecast_days: int = 2) -> EnsembleForecast:
    """Fetch GFS ensemble temperature forecast from Open-Meteo.

    Args:
        lat: Latitude of location.
        lon: Longitude of location.
        forecast_days: Number of days to forecast (default 2).

    Returns:
        EnsembleForecast with hourly data for all 31 ensemble members.

    Raises:
        httpx.TimeoutException: If the request times out after retries.
        httpx.HTTPStatusError: If the API returns an error status.
        ValueError: If the response format is unexpected.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m",
        "models": "gfs_seamless",
        "forecast_days": forecast_days,
    }

    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.debug(f"Fetching ensemble forecast (attempt {attempt}/{MAX_RETRIES}): lat={lat}, lon={lon}")
            with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
                response = client.get(ENSEMBLE_API_URL, params=params)
                response.raise_for_status()
                data = response.json()
                return _parse_ensemble_response(lat, lon, data)
        except httpx.TimeoutException as exc:
            logger.warning(f"Timeout on attempt {attempt}: {exc}")
            last_exc = exc
        except httpx.HTTPStatusError as exc:
            logger.error(f"HTTP error {exc.response.status_code}: {exc}")
            raise

    raise httpx.TimeoutException(f"All {MAX_RETRIES} attempts timed out") from last_exc


def _parse_ensemble_response(lat: float, lon: float, data: dict) -> EnsembleForecast:
    """Parse raw Open-Meteo ensemble JSON into EnsembleForecast."""
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    if not times:
        raise ValueError("No time data in ensemble response")

    # Open-Meteo names the control member "temperature_2m" (no suffix),
    # then ensemble members as "temperature_2m_member01" … "temperature_2m_member30".
    members: list[list[float]] = []
    control_key = "temperature_2m"
    control = hourly.get(control_key)
    if control is None:
        raise ValueError(f"Missing ensemble control key: {control_key}")
    members.append(control)

    for i in range(1, NUM_MEMBERS):
        key = f"temperature_2m_member{i:02d}"
        member_temps = hourly.get(key)
        if member_temps is None:
            raise ValueError(f"Missing ensemble member key: {key}")
        members.append(member_temps)

    logger.debug(f"Parsed {NUM_MEMBERS} members with {len(times)} time steps")
    return EnsembleForecast(lat=lat, lon=lon, times=times, members=members)


def calculate_probability(
    ensemble_data: EnsembleForecast,
    threshold_low: float,
    threshold_high: float,
    target_date: date,
    target_hour: int,
) -> ProbabilityResult:
    """Calculate the probability that temperature falls within a range.

    Finds the target hour across all ensemble members and counts how many
    have temperature in [threshold_low, threshold_high].

    Args:
        ensemble_data: EnsembleForecast returned by fetch_ensemble_forecast.
        threshold_low: Lower bound of temperature range (inclusive).
        threshold_high: Upper bound of temperature range (inclusive).
        target_date: The date to evaluate.
        target_hour: The hour (0-23) to evaluate.

    Returns:
        ProbabilityResult with probability and confidence.

    Raises:
        ValueError: If target_date/target_hour is not found in forecast times.
    """
    target_str = f"{target_date.isoformat()}T{target_hour:02d}:00"
    try:
        time_index = ensemble_data.times.index(target_str)
    except ValueError:
        raise ValueError(f"Target time {target_str} not found in forecast. "
                         f"Available range: {ensemble_data.times[0]} to {ensemble_data.times[-1]}")

    count_in_range = sum(
        1 for member in ensemble_data.members
        if threshold_low <= member[time_index] <= threshold_high
    )
    total = len(ensemble_data.members)
    probability = count_in_range / total

    # Confidence: how strongly members agree on a direction.
    # max(count_in, count_out) / total — high when majority agrees.
    count_out = total - count_in_range
    confidence = max(count_in_range, count_out) / total

    logger.debug(
        f"Probability for {target_str} in [{threshold_low}, {threshold_high}]: "
        f"{count_in_range}/{total} = {probability:.3f}, confidence={confidence:.3f}"
    )
    return ProbabilityResult(
        probability=probability,
        confidence=confidence,
        count_in_range=count_in_range,
        total_members=total,
    )


def get_forecasts_for_cities(cities_config: dict[str, dict]) -> dict[str, EnsembleForecast]:
    """Fetch ensemble forecasts for all configured cities.

    Args:
        cities_config: Dict mapping city name to {"lat": float, "lon": float}.
            Typically config.CITIES.

    Returns:
        Dict mapping city name to EnsembleForecast. Cities that fail are skipped
        with a logged warning.
    """
    results: dict[str, EnsembleForecast] = {}
    for city, coords in cities_config.items():
        try:
            forecast = fetch_ensemble_forecast(coords["lat"], coords["lon"])
            results[city] = forecast
            logger.info(f"Fetched forecast for {city}")
        except Exception as exc:
            logger.warning(f"Failed to fetch forecast for {city}: {exc}")
    return results
