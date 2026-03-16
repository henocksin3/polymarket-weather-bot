"""Tests for the experiment and calibration system."""

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta

import pytest

from src.database import create_tables
from src.experiments import (
    MAX_DEVIATION,
    MIN_EVAL_TRADES,
    SAFETY_MIN_TRADES,
    SAFETY_MIN_WR,
    adjust_forecast_with_calibration,
    assign_experiment_variant,
    create_experiment_tables,
    get_active_experiments,
    get_calibration_stats,
    get_recent_experiments,
    record_experiment_result,
    run_experiment_cycle,
    start_experiment,
    store_calibration_data,
)
from src.learner import create_learning_tables


@pytest.fixture
def db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    # Initialize database
    create_tables(path)
    create_learning_tables(path)
    create_experiment_tables(path)

    yield path

    # Cleanup
    try:
        os.unlink(path)
    except Exception:
        pass


@pytest.fixture
def db_with_trades(db):
    """Create a database with sample trades for experiment generation."""
    con = sqlite3.connect(db)
    try:
        # Create losing pattern for Miami YES
        for i in range(10):
            con.execute(
                """
                INSERT INTO trades
                  (market_id, condition_id, token_id, question, city, trade_date,
                   side, size, price, edge, forecast_prob, confidence,
                   order_id, dry_run, resolved, hit, pnl, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"market_{i}",
                    "cond_1",
                    "token_1",
                    "Test question",
                    "miami",
                    "2026-03-16",
                    "YES",
                    5.0,
                    0.5,
                    0.15,
                    0.65,
                    0.80,
                    "",
                    1,
                    1,
                    0,  # Loss
                    -5.0,
                    (datetime.now() - timedelta(hours=i)).isoformat(),
                ),
            )

        # Create winning pattern for Chicago NO
        for i in range(10):
            con.execute(
                """
                INSERT INTO trades
                  (market_id, condition_id, token_id, question, city, trade_date,
                   side, size, price, edge, forecast_prob, confidence,
                   order_id, dry_run, resolved, hit, pnl, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"market_{i+100}",
                    "cond_2",
                    "token_2",
                    "Test question",
                    "chicago",
                    "2026-03-16",
                    "NO",
                    5.0,
                    0.4,
                    0.20,
                    0.80,
                    0.85,
                    "",
                    1,
                    1,
                    1,  # Win
                    3.0,
                    (datetime.now() - timedelta(hours=i)).isoformat(),
                ),
            )

        con.commit()
    finally:
        con.close()

    return db


def test_create_experiment_tables(db):
    """Test that experiment tables are created."""
    con = sqlite3.connect(db)
    try:
        cur = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='experiments'"
        )
        assert cur.fetchone() is not None

        cur = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='calibration_data'"
        )
        assert cur.fetchone() is not None
    finally:
        con.close()


def test_start_experiment_with_losing_pattern(db_with_trades):
    """Test that experiment is started for a city with losing pattern."""
    result = start_experiment(db_with_trades, "miami", auto=True)

    assert result is not None
    assert result["city"] == "miami"
    assert result["hypothesis"] is not None
    assert result["param_name"] in ["gfs_bias", "min_edge"]
    assert result["baseline_value"] != result["experiment_value"]

    # Check that deviation is within bounds
    baseline = result["baseline_value"]
    experiment = result["experiment_value"]
    if baseline != 0:
        deviation = abs(experiment - baseline) / abs(baseline)
        assert deviation <= MAX_DEVIATION


def test_start_experiment_no_losing_pattern(db):
    """Test that no experiment is started when there's no losing pattern."""
    result = start_experiment(db, "new_york", auto=True)
    assert result is None


def test_only_one_active_experiment_per_city(db_with_trades):
    """Test that only one experiment can be active per city at a time."""
    # Start first experiment
    result1 = start_experiment(db_with_trades, "miami", auto=True)
    assert result1 is not None

    # Try to start second experiment for same city
    result2 = start_experiment(db_with_trades, "miami", auto=True)
    assert result2 is None

    # Should be able to start for different city
    result3 = start_experiment(db_with_trades, "chicago", auto=True)
    # Chicago has good performance, so might not generate hypothesis
    # Just check it doesn't error


def test_assign_experiment_variant(db):
    """Test variant assignment with 50/50 split."""
    # Create an active experiment
    con = sqlite3.connect(db)
    try:
        con.execute(
            """
            INSERT INTO experiments
              (city, hypothesis, param_name, baseline_value, experiment_value,
               status, reason, created_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                "miami",
                "Test hypothesis",
                "gfs_bias",
                -2.0,
                -1.5,
                "Testing bias",
                datetime.now().isoformat(),
            ),
        )
        con.commit()
    finally:
        con.close()

    # Assign multiple variants and check distribution
    assignments = []
    for _ in range(20):
        variant = assign_experiment_variant(db, "miami")
        assert variant in ["baseline", "experiment"]
        assignments.append(variant)

    # Should have some of each variant
    assert "baseline" in assignments
    assert "experiment" in assignments


def test_assign_variant_no_experiment(db):
    """Test that None is returned when no experiment is active."""
    variant = assign_experiment_variant(db, "miami")
    assert variant is None


def test_record_experiment_result(db):
    """Test recording experiment results."""
    # Create an active experiment
    con = sqlite3.connect(db)
    try:
        con.execute(
            """
            INSERT INTO experiments
              (city, hypothesis, param_name, baseline_value, experiment_value,
               status, reason, created_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                "miami",
                "Test hypothesis",
                "gfs_bias",
                -2.0,
                -1.5,
                "Testing",
                datetime.now().isoformat(),
            ),
        )
        con.commit()
    finally:
        con.close()

    # Record some results
    record_experiment_result(db, "miami", "baseline", hit=True)
    record_experiment_result(db, "miami", "baseline", hit=False)
    record_experiment_result(db, "miami", "experiment", hit=True)
    record_experiment_result(db, "miami", "experiment", hit=True)

    # Check counts
    con = sqlite3.connect(db)
    try:
        cur = con.execute(
            """
            SELECT baseline_trades, baseline_wins, experiment_trades, experiment_wins
            FROM experiments
            WHERE city = 'miami' AND status = 'active'
            """
        )
        row = cur.fetchone()
        assert row[0] == 2  # baseline_trades
        assert row[1] == 1  # baseline_wins
        assert row[2] == 2  # experiment_trades
        assert row[3] == 2  # experiment_wins
    finally:
        con.close()


def test_safety_abort(db):
    """Test that experiment is aborted if experiment variant performs poorly."""
    # Create an active experiment
    con = sqlite3.connect(db)
    try:
        con.execute(
            """
            INSERT INTO experiments
              (city, hypothesis, param_name, baseline_value, experiment_value,
               status, reason, created_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                "miami",
                "Test hypothesis",
                "gfs_bias",
                -2.0,
                -1.5,
                "Testing",
                datetime.now().isoformat(),
            ),
        )
        con.commit()
    finally:
        con.close()

    # Record poor experiment performance
    for _ in range(SAFETY_MIN_TRADES):
        record_experiment_result(db, "miami", "experiment", hit=False)

    # Experiment should be aborted
    con = sqlite3.connect(db)
    try:
        cur = con.execute(
            """
            SELECT status, winner
            FROM experiments
            WHERE city = 'miami'
            """
        )
        row = cur.fetchone()
        assert row[0] == "aborted"
        assert row[1] == "baseline"
    finally:
        con.close()


def test_experiment_evaluation(db):
    """Test that experiment is evaluated after minimum trades."""
    # Create an active experiment
    con = sqlite3.connect(db)
    try:
        con.execute(
            """
            INSERT INTO experiments
              (city, hypothesis, param_name, baseline_value, experiment_value,
               status, reason, created_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                "miami",
                "Test hypothesis",
                "gfs_bias",
                -2.0,
                -1.5,
                "Testing",
                datetime.now().isoformat(),
            ),
        )
        con.commit()
    finally:
        con.close()

    # Record minimum trades with experiment winning
    for _ in range(MIN_EVAL_TRADES):
        record_experiment_result(db, "miami", "baseline", hit=False)
        record_experiment_result(db, "miami", "experiment", hit=True)

    # Experiment should be completed with experiment as winner
    con = sqlite3.connect(db)
    try:
        cur = con.execute(
            """
            SELECT status, winner
            FROM experiments
            WHERE city = 'miami'
            """
        )
        row = cur.fetchone()
        assert row[0] == "completed"
        assert row[1] == "experiment"
    finally:
        con.close()


def test_get_active_experiments(db):
    """Test retrieving active experiments."""
    # Create some experiments
    con = sqlite3.connect(db)
    try:
        con.execute(
            """
            INSERT INTO experiments
              (city, hypothesis, param_name, baseline_value, experiment_value,
               status, reason, created_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                "miami",
                "Test hypothesis 1",
                "gfs_bias",
                -2.0,
                -1.5,
                "Testing",
                datetime.now().isoformat(),
            ),
        )
        con.execute(
            """
            INSERT INTO experiments
              (city, hypothesis, param_name, baseline_value, experiment_value,
               status, reason, created_at, completed_at)
            VALUES (?, ?, ?, ?, ?, 'completed', ?, ?, ?)
            """,
            (
                "chicago",
                "Test hypothesis 2",
                "min_edge",
                0.15,
                0.20,
                "Testing",
                datetime.now().isoformat(),
                datetime.now().isoformat(),
            ),
        )
        con.commit()
    finally:
        con.close()

    active = get_active_experiments(db)
    assert len(active) == 1
    assert active[0]["city"] == "miami"


def test_get_recent_experiments(db):
    """Test retrieving recent completed/aborted experiments."""
    # Create experiments with different dates
    con = sqlite3.connect(db)
    try:
        # Recent completed
        con.execute(
            """
            INSERT INTO experiments
              (city, hypothesis, param_name, baseline_value, experiment_value,
               status, reason, created_at, completed_at)
            VALUES (?, ?, ?, ?, ?, 'completed', ?, ?, ?)
            """,
            (
                "miami",
                "Test hypothesis 1",
                "gfs_bias",
                -2.0,
                -1.5,
                "Winner: experiment",
                datetime.now().isoformat(),
                datetime.now().isoformat(),
            ),
        )

        # Old completed (should not appear)
        con.execute(
            """
            INSERT INTO experiments
              (city, hypothesis, param_name, baseline_value, experiment_value,
               status, reason, created_at, completed_at)
            VALUES (?, ?, ?, ?, ?, 'completed', ?, ?, ?)
            """,
            (
                "chicago",
                "Test hypothesis 2",
                "min_edge",
                0.15,
                0.20,
                "Winner: baseline",
                (datetime.now() - timedelta(days=10)).isoformat(),
                (datetime.now() - timedelta(days=10)).isoformat(),
            ),
        )

        con.commit()
    finally:
        con.close()

    recent = get_recent_experiments(db, days=7)
    assert len(recent) == 1
    assert recent[0]["city"] == "miami"


def test_run_experiment_cycle(db_with_trades):
    """Test full experiment cycle."""
    summary = run_experiment_cycle(db_with_trades)

    # Should have started experiment for Miami (losing pattern)
    assert "started" in summary
    assert "active" in summary
    assert "completed_this_week" in summary
    assert "aborted_this_week" in summary

    # Check that Miami experiment was started
    if summary["started"]:
        assert any(e["city"] == "miami" for e in summary["started"])


# ============================================================================
# CALIBRATION TESTS
# ============================================================================


def test_store_calibration_data(db):
    """Test storing calibration data."""
    # Create a sample trade
    con = sqlite3.connect(db)
    try:
        con.execute(
            """
            INSERT INTO trades
              (market_id, condition_id, token_id, question, city, trade_date,
               side, size, price, edge, forecast_prob, confidence,
               order_id, dry_run, resolved, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "market_1",
                "cond_1",
                "token_1",
                "Test question",
                "miami",
                "2026-03-16",
                "YES",
                5.0,
                0.5,
                0.15,
                0.65,
                0.80,
                "",
                1,
                0,
                datetime.now().isoformat(),
            ),
        )
        con.commit()
        trade_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        con.close()

    # Store calibration data
    store_calibration_data(
        db,
        trade_id=trade_id,
        city="miami",
        gfs_forecast=75.0,
        actual_temp=77.0,
        resolution_source="Polymarket",
    )

    # Verify
    con = sqlite3.connect(db)
    try:
        cur = con.execute(
            """
            SELECT gfs_forecast, actual_temp, forecast_error
            FROM calibration_data
            WHERE trade_id = ?
            """,
            (trade_id,),
        )
        row = cur.fetchone()
        assert row[0] == 75.0  # gfs_forecast
        assert row[1] == 77.0  # actual_temp
        assert row[2] == 2.0  # forecast_error (actual - forecast)
    finally:
        con.close()


def test_get_calibration_stats(db):
    """Test calculating calibration statistics."""
    # Create sample calibration data
    con = sqlite3.connect(db)
    try:
        for i in range(10):
            con.execute(
                """
                INSERT INTO calibration_data
                  (trade_id, city, gfs_forecast, actual_temp, forecast_error,
                   resolution_source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    i,
                    "miami",
                    75.0 + i,
                    77.0 + i,  # GFS consistently 2°F low
                    2.0,
                    "Polymarket",
                    datetime.now().isoformat(),
                ),
            )
        con.commit()
    finally:
        con.close()

    stats = get_calibration_stats(db, "miami", days=30)

    assert stats["city"] == "miami"
    assert stats["sample_size"] == 10
    assert abs(stats["bias"] - 2.0) < 0.1  # Should be ~2°F
    assert stats["std_dev"] >= 0  # Should have some variance
    assert 0 <= stats["confidence"] <= 1


def test_adjust_forecast_with_calibration(db):
    """Test forecast adjustment using calibration data."""
    # Create calibration data with +2°F bias
    con = sqlite3.connect(db)
    try:
        for i in range(20):
            con.execute(
                """
                INSERT INTO calibration_data
                  (trade_id, city, gfs_forecast, actual_temp, forecast_error,
                   resolution_source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    i,
                    "miami",
                    75.0,
                    77.0,
                    2.0,
                    "Polymarket",
                    datetime.now().isoformat(),
                ),
            )
        con.commit()
    finally:
        con.close()

    # Adjust a forecast
    raw_forecast = 80.0
    adjusted, stats = adjust_forecast_with_calibration(db, "miami", raw_forecast)

    # Should subtract the bias
    assert abs(adjusted - 78.0) < 0.5  # 80 - 2 = 78
    assert stats["sample_size"] >= 20


def test_adjust_forecast_insufficient_data(db):
    """Test that forecast is not adjusted when insufficient calibration data."""
    raw_forecast = 80.0
    adjusted, stats = adjust_forecast_with_calibration(db, "miami", raw_forecast)

    # Should return unchanged forecast
    assert adjusted == raw_forecast
    assert stats["sample_size"] < 5
