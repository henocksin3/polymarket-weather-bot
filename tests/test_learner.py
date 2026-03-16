"""Tests for src/learner.py — adaptive learning system."""

import sqlite3
import tempfile
from datetime import date, datetime

import pytest

from src.database import create_tables, log_trade
from src.learner import (
    _calculate_confidence_score,
    _calculate_gfs_bias,
    _get_multi_period_stats,
    analyze_and_update_params,
    create_learning_tables,
    get_learning_params,
    get_recommended_position_size,
    is_side_active,
)
from src.signals import Signal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db() -> str:
    """Create a temp DB with trades and learning_params tables."""
    tmp = tempfile.mktemp(suffix=".db")
    create_tables(tmp)
    create_learning_tables(tmp)
    return tmp


def _insert_resolved_trade(
    db_path: str,
    city: str,
    side: str,
    hit: bool,
    edge: float = 0.10,
    price: float = 0.50,
    size: float = 5.0,
) -> int:
    """Insert a resolved trade for testing."""
    sig = Signal(
        market_id="m1",
        condition_id="0xcond",
        token_id="tok1",
        question=f"Will the temp in {city} be above 50°F?",
        city=city,
        date=date(2026, 3, 10),
        forecast_prob=0.8 if side == "YES" else 0.2,
        market_price=price,
        edge=edge,
        confidence=0.9,
        recommended_side=side,
        recommended_size=size,
        timestamp=datetime.now(),
    )

    # Insert trade
    trade_id = log_trade(db_path, sig, order_result=None, dry_run=True)

    # Mark as resolved
    con = sqlite3.connect(db_path)
    try:
        pnl = size if hit else -size
        con.execute(
            "UPDATE trades SET resolved = 1, hit = ?, pnl = ? WHERE id = ?",
            (int(hit), pnl, trade_id),
        )
        con.commit()
    finally:
        con.close()

    return trade_id


# ---------------------------------------------------------------------------
# _get_multi_period_stats
# ---------------------------------------------------------------------------

class TestGetMultiPeriodStats:

    def test_returns_correct_stats(self):
        db = _make_db()
        # Insert 5 trades for new_york YES: 3 wins, 2 losses
        for hit in [True, True, True, False, False]:
            _insert_resolved_trade(db, "new_york", "YES", hit, edge=0.12)

        con = sqlite3.connect(db)
        try:
            stats = _get_multi_period_stats(con, "new_york", "YES")
        finally:
            con.close()

        assert stats["total_trades"] == 5
        assert stats["win_rate_10"] == pytest.approx(0.6, abs=0.01)  # 3/5
        assert stats["win_rate_30"] == pytest.approx(0.6, abs=0.01)  # Same with only 5 trades
        assert stats["win_rate_all"] == pytest.approx(0.6, abs=0.01)
        assert stats["avg_edge"] == pytest.approx(0.12, abs=0.01)

    def test_returns_zero_for_no_trades(self):
        db = _make_db()
        con = sqlite3.connect(db)
        try:
            stats = _get_multi_period_stats(con, "chicago", "NO")
        finally:
            con.close()

        assert stats["total_trades"] == 0
        assert stats["win_rate_10"] == 0.0
        assert stats["win_rate_30"] == 0.0
        assert stats["win_rate_all"] == 0.0
        assert stats["avg_edge"] == 0.0

    def test_multi_period_calculation(self):
        db = _make_db()
        # Insert 15 trades with different win rates in different periods
        # Last 10: 6 wins (60%)
        # Last 30 (all 15): 9 wins (60%)
        hits = [True, True, True, False, False,  # 5: 3W
                True, True, True, False, False,  # 10: 6W
                True, True, True, False, False]  # 15: 9W
        for i, hit in enumerate(hits):
            _insert_resolved_trade(db, "miami", "YES", hit=hit, edge=0.08)

        con = sqlite3.connect(db)
        try:
            stats = _get_multi_period_stats(con, "miami", "YES")
        finally:
            con.close()

        assert stats["total_trades"] == 15
        assert stats["win_rate_10"] == pytest.approx(0.6, abs=0.01)  # Last 10: 6/10
        assert stats["win_rate_30"] == pytest.approx(0.6, abs=0.01)  # All 15: 9/15
        assert stats["win_rate_all"] == pytest.approx(0.6, abs=0.01)


# ---------------------------------------------------------------------------
# _calculate_gfs_bias
# ---------------------------------------------------------------------------

class TestCalculateGfsBias:

    def test_bias_positive_when_yes_loses_more(self):
        """If YES trades lose more, GFS is overestimating temps → positive bias."""
        db = _make_db()
        # YES: 2 wins, 8 losses (20% win rate) → GFS overestimates
        for i in range(10):
            _insert_resolved_trade(db, "new_york", "YES", hit=(i < 2))

        con = sqlite3.connect(db)
        try:
            bias = _calculate_gfs_bias(con, "new_york")
        finally:
            con.close()

        # (0.5 - 0.2) * 4 = 1.2
        assert bias == pytest.approx(1.2, abs=0.1)

    def test_bias_negative_when_yes_wins_more(self):
        """If YES trades win more, GFS is underestimating temps → negative bias."""
        db = _make_db()
        # YES: 8 wins, 2 losses (80% win rate) → GFS underestimates
        for i in range(10):
            _insert_resolved_trade(db, "chicago", "YES", hit=(i < 8))

        con = sqlite3.connect(db)
        try:
            bias = _calculate_gfs_bias(con, "chicago")
        finally:
            con.close()

        # (0.5 - 0.8) * 4 = -1.2
        assert bias == pytest.approx(-1.2, abs=0.1)

    def test_no_bias_when_balanced(self):
        db = _make_db()
        # YES: 5 wins, 5 losses
        for i in range(10):
            _insert_resolved_trade(db, "london", "YES", hit=(i < 5))

        con = sqlite3.connect(db)
        try:
            bias = _calculate_gfs_bias(con, "london")
        finally:
            con.close()

        assert bias == pytest.approx(0.0, abs=0.1)


# ---------------------------------------------------------------------------
# analyze_and_update_params
# ---------------------------------------------------------------------------

class TestAnalyzeAndUpdateParams:

    def test_deactivates_side_with_low_win_rate(self):
        db = _make_db()
        # Insert 10 YES trades with 30% win rate
        # All 3 periods will be < 45%, so should deactivate
        for i in range(10):
            _insert_resolved_trade(db, "new_york", "YES", hit=(i < 3))

        summary = analyze_and_update_params(db)

        assert summary["analyzed"] == 1
        assert "new_york YES" in summary["deactivated"]

        # Check params were updated
        params = get_learning_params(db, "new_york", "YES")
        assert params is not None
        assert params["active"] == 0
        assert params["win_rate_10"] == pytest.approx(0.3, abs=0.01)
        assert params["win_rate_30"] == pytest.approx(0.3, abs=0.01)
        assert params["win_rate_all"] == pytest.approx(0.3, abs=0.01)

    def test_keeps_side_active_with_good_win_rate(self):
        db = _make_db()
        # Insert 10 NO trades with 70% win rate
        for i in range(10):
            _insert_resolved_trade(db, "chicago", "NO", hit=(i < 7))

        summary = analyze_and_update_params(db)

        assert summary["analyzed"] == 1
        assert len(summary["deactivated"]) == 0

        params = get_learning_params(db, "chicago", "NO")
        assert params is not None
        assert params["active"] == 1

    def test_increases_position_size_for_winning_side(self):
        db = _make_db()
        # Insert 35 trades with 70% win rate
        # New system requires WR_30 > 60% AND WR_all > 60% AND min 30 trades
        for i in range(35):
            _insert_resolved_trade(db, "miami", "NO", hit=(i % 10 < 7))

        summary = analyze_and_update_params(db)

        params = get_learning_params(db, "miami", "NO")
        assert params is not None
        assert params["total_trades"] >= 30
        assert params["win_rate_30"] > 0.60
        assert params["win_rate_all"] > 0.60
        assert params["position_size"] > 5.0  # Should be increased from default
        assert params["position_size"] <= 20.0  # Should not exceed max

    def test_analyzes_multiple_city_side_combinations(self):
        db = _make_db()
        # Insert trades for multiple combinations
        for i in range(10):
            _insert_resolved_trade(db, "new_york", "YES", hit=(i < 5))
            _insert_resolved_trade(db, "new_york", "NO", hit=(i < 6))
            _insert_resolved_trade(db, "chicago", "YES", hit=(i < 4))

        summary = analyze_and_update_params(db)

        assert summary["analyzed"] == 3  # 3 combinations

    def test_handles_empty_database(self):
        db = _make_db()
        summary = analyze_and_update_params(db)
        assert summary["analyzed"] == 0


# ---------------------------------------------------------------------------
# get_learning_params
# ---------------------------------------------------------------------------

class TestGetLearningParams:

    def test_returns_params_when_exist(self):
        db = _make_db()
        for i in range(10):
            _insert_resolved_trade(db, "london", "YES", hit=(i < 6))

        analyze_and_update_params(db)
        params = get_learning_params(db, "london", "YES")

        assert params is not None
        assert params["city"] == "london"
        assert params["side"] == "YES"
        assert params["win_rate_all"] > 0
        assert "position_size" in params

    def test_returns_none_when_not_exist(self):
        db = _make_db()
        params = get_learning_params(db, "tokyo", "YES")
        assert params is None


# ---------------------------------------------------------------------------
# is_side_active
# ---------------------------------------------------------------------------

class TestIsSideActive:

    def test_returns_true_when_no_params(self):
        """Default to active when no learning params exist yet."""
        db = _make_db()
        assert is_side_active(db, "new_york", "YES") is True

    def test_returns_false_when_deactivated(self):
        db = _make_db()
        # Create scenario that deactivates YES
        for i in range(10):
            _insert_resolved_trade(db, "new_york", "YES", hit=(i < 3))  # 30% win rate

        analyze_and_update_params(db)
        assert is_side_active(db, "new_york", "YES") is False

    def test_returns_true_when_active(self):
        db = _make_db()
        for i in range(10):
            _insert_resolved_trade(db, "chicago", "NO", hit=(i < 7))  # 70% win rate

        analyze_and_update_params(db)
        assert is_side_active(db, "chicago", "NO") is True


# ---------------------------------------------------------------------------
# get_recommended_position_size
# ---------------------------------------------------------------------------

class TestGetRecommendedPositionSize:

    def test_returns_default_when_no_params(self):
        db = _make_db()
        size = get_recommended_position_size(db, "new_york", "YES")
        assert size == 5.0  # Default

    def test_returns_learned_size_when_params_exist(self):
        db = _make_db()
        # Create high win rate with enough trades
        # Need 30+ trades with WR_30 > 60% and WR_all > 60%
        for i in range(35):
            _insert_resolved_trade(db, "miami", "NO", hit=(i % 10 < 8))  # 80% win rate

        analyze_and_update_params(db)
        size = get_recommended_position_size(db, "miami", "NO")

        assert size > 5.0
        assert size <= 20.0

    def test_position_size_scales_with_win_rate(self):
        db = _make_db()

        # 62% win rate → minimal increase (need 30+ trades)
        # Pattern: wins if i % 10 < 6 (0,1,2,3,4,5 win, 6,7,8,9 lose) = 60%
        # Plus a couple extra wins to reach ~62%
        for i in range(35):
            _insert_resolved_trade(db, "test1", "YES", hit=(i % 10 < 6 or i == 34))

        # 70% win rate → moderate increase
        # Pattern: wins if i % 10 < 7 (0-6 win, 7-9 lose) = 70%
        for i in range(35):
            _insert_resolved_trade(db, "test2", "YES", hit=(i % 10 < 7))

        analyze_and_update_params(db)

        size_62 = get_recommended_position_size(db, "test1", "YES")
        size_70 = get_recommended_position_size(db, "test2", "YES")

        # 70% should be larger than 62% (even if 62% is still at baseline)
        assert size_70 > size_62
        assert size_70 > 5.0  # 70% should definitely be above baseline
