"""Tests for src/signals.py — signal generation logic."""

from datetime import date, datetime

import pytest

from src.markets import WeatherMarket
from src.signals import Signal, _kelly_size, _target_hour_for_question, generate_signals
from src.weather import EnsembleForecast


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TARGET_DATE = date(2026, 3, 10)
TARGET_HOUR = 14
TIME_LABEL = f"{TARGET_DATE.isoformat()}T{TARGET_HOUR:02d}:00"


def _make_forecast(n_in: int, in_temp: float = 20.0, out_temp: float = 40.0) -> EnsembleForecast:
    """Create a 31-member EnsembleForecast with n_in members inside [10, 30°C].

    Members 0..n_in-1 get in_temp (inside range), rest get out_temp (outside).
    """
    members = []
    for i in range(31):
        temp = in_temp if i < n_in else out_temp
        members.append([temp])
    return EnsembleForecast(lat=40.71, lon=-74.0, times=[TIME_LABEL], members=members)


def _make_market(
    market_id: str,
    question: str,
    outcomes: list[str],
    prices: list[float],
    tokens: list[dict] | None = None,
) -> WeatherMarket:
    """Create a minimal WeatherMarket for testing."""
    if tokens is None:
        tokens = [{"token_id": f"tok_{i}"} for i in range(len(outcomes))]
    return WeatherMarket(
        market_id=market_id,
        condition_id=f"cond_{market_id}",
        question=question,
        outcomes=outcomes,
        outcome_prices=prices,
        tokens=tokens,
        active=True,
    )


# ---------------------------------------------------------------------------
# Tests: signal generation scenarios
# ---------------------------------------------------------------------------

class TestGenerateSignals:
    """Core signal-generation scenarios from the build plan."""

    def test_strong_yes_signal(self):
        """Forecast 90% (28/31), market 15% → edge 75%, side YES."""
        # 28 of 31 members inside range [10, 30°C]
        forecast = _make_forecast(n_in=28)
        market = _make_market(
            market_id="m1",
            question="Will the high temperature in New York City be 10-30°C on March 10, 2026?",
            outcomes=["10-30°C"],
            prices=[0.15],
        )

        signals = generate_signals({"new_york": forecast}, [market])

        assert len(signals) == 1
        sig = signals[0]
        assert sig.market_id == "m1"
        assert sig.recommended_side == "YES"
        assert pytest.approx(sig.forecast_prob, abs=0.01) == 28 / 31
        assert sig.market_price == 0.15
        assert pytest.approx(sig.edge, abs=0.01) == 28 / 31 - 0.15

    def test_strong_no_signal(self):
        """Forecast 10% (3/31), market 85% → edge -75%, side NO."""
        # Only 3 of 31 members inside range
        forecast = _make_forecast(n_in=3)
        market = _make_market(
            market_id="m2",
            question="Will the high temperature in New York City be 10-30°C on March 10, 2026?",
            outcomes=["10-30°C"],
            prices=[0.85],
        )

        signals = generate_signals({"new_york": forecast}, [market])

        assert len(signals) == 1
        sig = signals[0]
        assert sig.recommended_side == "NO"
        assert pytest.approx(sig.forecast_prob, abs=0.01) == 3 / 31
        assert sig.market_price == 0.85
        assert sig.edge < 0

    def test_below_edge_threshold_no_signal(self):
        """Forecast ~52% (16/31), market 48% → edge ~4%, below 8% MIN_EDGE → no signal."""
        forecast = _make_forecast(n_in=16)
        market = _make_market(
            market_id="m3",
            question="Will the high temperature in New York City be 10-30°C on March 10, 2026?",
            outcomes=["10-30°C"],
            prices=[0.48],
        )

        signals = generate_signals({"new_york": forecast}, [market])

        assert len(signals) == 0

    def test_below_confidence_threshold_no_signal(self):
        """Forecast 90% but confidence is exactly at boundary — still emits.

        16/31 in, 15/31 out → confidence = 16/31 ≈ 0.516, below MIN_CONFIDENCE (0.70).
        Even with a large edge the signal should be suppressed.
        """
        # 24/31 in — confidence = 24/31 ≈ 0.774, prob = 24/31 ≈ 0.774
        # market price = 0.15, edge ≈ 0.62 — but confidence 0.774 > 0.70, so signal IS emitted.
        # Use 17/31 in: prob = 0.548, conf = 0.548. Even with low price, edge < 0.08.
        # To get high edge + low confidence, use 25/31 in (prob=0.806, conf=0.806) vs price=0.10:
        # Actually confidence = max(25, 6)/31 = 25/31 = 0.806. Both threshold ok.
        # True low-confidence + high edge: not really possible with this formula.
        # Instead, test with 15/31 in (50%), market price=0.02, edge=0.48, confidence=0.516<0.70.
        forecast = _make_forecast(n_in=15)
        market = _make_market(
            market_id="m4",
            question="Will the high temperature in New York City be 10-30°C on March 10, 2026?",
            outcomes=["10-30°C"],
            prices=[0.02],
        )

        signals = generate_signals({"new_york": forecast}, [market])
        # confidence = max(15,16)/31 ≈ 0.516 < MIN_CONFIDENCE → no signal
        assert len(signals) == 0

    def test_unknown_city_skipped(self):
        """Market for a city not in forecast dict → no signal."""
        forecast = _make_forecast(n_in=28)
        market = _make_market(
            market_id="m5",
            question="Will the high temperature in Tokyo be 10-30°C on March 10, 2026?",
            outcomes=["10-30°C"],
            prices=[0.15],
        )

        signals = generate_signals({"new_york": forecast}, [market])
        assert len(signals) == 0

    def test_no_date_in_question_skipped(self):
        """Market without a parseable date → no signal."""
        forecast = _make_forecast(n_in=28)
        market = _make_market(
            market_id="m6",
            question="Will it be warm in New York City?",
            outcomes=["10-30°C"],
            prices=[0.15],
        )

        signals = generate_signals({"new_york": forecast}, [market])
        assert len(signals) == 0

    def test_signals_sorted_by_edge_descending(self):
        """Multiple signals should be sorted by abs(edge) highest first."""
        # Signal A: 28/31 in, price 0.15 → edge ≈ 0.75
        forecast_a = _make_forecast(n_in=28)
        market_a = _make_market(
            "ma",
            "Will the high temperature in New York City be 10-30°C on March 10, 2026?",
            ["10-30°C"],
            [0.15],
        )

        # Signal B: 3/31 in, price 0.85 → edge ≈ -0.75 (abs same as A)
        # Use price 0.95 instead to get larger abs edge for B
        forecast_b = _make_forecast(n_in=3)
        market_b = _make_market(
            "mb",
            "Will the high temperature in Chicago be 10-30°C on March 10, 2026?",
            ["10-30°C"],
            [0.95],
        )

        signals = generate_signals(
            {"new_york": forecast_a, "chicago": forecast_b},
            [market_a, market_b],
        )

        assert len(signals) == 2
        # Signal B has larger abs edge (3/31 - 0.95 ≈ -0.853 vs 28/31 - 0.15 ≈ 0.754)
        assert abs(signals[0].edge) >= abs(signals[1].edge)

    def test_market_price_at_ceiling_skipped(self):
        """market_price >= 0.99 (near-ceiling, insufficient NO liquidity) → no signal."""
        forecast = _make_forecast(n_in=0)
        for price in [1.0, 0.99, 0.995]:
            market = _make_market(
                market_id="m8",
                question="Will the high temperature in New York City be 10-30°C on March 10, 2026?",
                outcomes=["10-30°C"],
                prices=[price],
            )
            signals = generate_signals({"new_york": forecast}, [market])
            assert len(signals) == 0, f"Expected no signal for price={price}"

    def test_market_price_just_below_ceiling_passes(self):
        """market_price = 0.989 is below threshold — should still be evaluated."""
        forecast = _make_forecast(n_in=3)  # 10% prob, edge = -0.889, conf=100%
        market = _make_market(
            market_id="m8b",
            question="Will the high temperature in New York City be 10-30°C on March 10, 2026?",
            outcomes=["10-30°C"],
            prices=[0.989],
        )
        signals = generate_signals({"new_york": forecast}, [market])
        # edge=-88.9%, conf=100% → exceeds MIN_EDGE and MIN_CONFIDENCE → signal emitted
        assert len(signals) == 1
        assert signals[0].recommended_side == "NO"

    def test_market_date_too_far_ahead_skipped(self):
        """Market date more than 2 days from today → no signal."""
        forecast = _make_forecast(n_in=28)
        # March 15 is well beyond 2 days from test run date (March 8)
        market = _make_market(
            market_id="m9",
            question="Will the high temperature in New York City be 10-30°C on March 15, 2026?",
            outcomes=["10-30°C"],
            prices=[0.15],
        )
        signals = generate_signals({"new_york": forecast}, [market])
        assert len(signals) == 0

    def test_token_id_populated(self):
        """token_id should be extracted from market.tokens."""
        forecast = _make_forecast(n_in=28)
        market = _make_market(
            "m7",
            "Will the high temperature in New York City be 10-30°C on March 10, 2026?",
            ["10-30°C"],
            [0.15],
            tokens=[{"token_id": "abc123"}],
        )

        signals = generate_signals({"new_york": forecast}, [market])
        assert len(signals) == 1
        assert signals[0].token_id == "abc123"


# ---------------------------------------------------------------------------
# Tests: Kelly sizing
# ---------------------------------------------------------------------------

class TestTargetHour:

    def test_below_does_not_trigger_low_hour(self):
        """'or below' must not be mistaken for the word 'low' (Bug #2)."""
        assert _target_hour_for_question(
            "Will the highest temperature in London be 11°C or below on March 8?"
        ) == 14

    def test_explicit_low_triggers_low_hour(self):
        """'low temperature' should still route to hour 6."""
        assert _target_hour_for_question(
            "Will the low temperature in Chicago be below 0°C on January 5?"
        ) == 6

    def test_highest_defaults_to_high_hour(self):
        assert _target_hour_for_question(
            "Will the highest temperature in Miami be above 30°C on July 4?"
        ) == 14

    def test_minimum_triggers_low_hour(self):
        assert _target_hour_for_question(
            "Will the minimum temperature in London be below 5°C?"
        ) == 6


class TestKellySize:

    def test_positive_edge_returns_positive_size(self):
        size = _kelly_size(forecast_prob=0.9, market_price=0.15)
        assert size > 0

    def test_negative_edge_returns_zero(self):
        # forecast < market_price → Kelly f < 0 → return 0
        size = _kelly_size(forecast_prob=0.1, market_price=0.85)
        assert size == 0.0

    def test_capped_at_max_position(self):
        # Extreme edge → Kelly would suggest large size, but cap applies
        size = _kelly_size(forecast_prob=0.99, market_price=0.01)
        from config import MAX_POSITION_USD
        assert size <= MAX_POSITION_USD

    def test_zero_price_returns_zero(self):
        assert _kelly_size(forecast_prob=0.9, market_price=0.0) == 0.0

    def test_price_one_returns_zero(self):
        assert _kelly_size(forecast_prob=0.9, market_price=1.0) == 0.0
