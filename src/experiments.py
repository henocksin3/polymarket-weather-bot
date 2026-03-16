"""Experimental learning system with automated hypothesis testing and calibration."""

import logging
import random
import sqlite3
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Database schema for experiments
_CREATE_EXPERIMENTS_SQL = """
CREATE TABLE IF NOT EXISTS experiments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    city                TEXT    NOT NULL,
    hypothesis          TEXT    NOT NULL,
    param_name          TEXT    NOT NULL,
    baseline_value      REAL    NOT NULL,
    experiment_value    REAL    NOT NULL,
    status              TEXT    NOT NULL,  -- 'active', 'completed', 'aborted'
    baseline_trades     INTEGER DEFAULT 0,
    baseline_wins       INTEGER DEFAULT 0,
    experiment_trades   INTEGER DEFAULT 0,
    experiment_wins     INTEGER DEFAULT 0,
    winner              TEXT,               -- 'baseline', 'experiment', NULL
    reason              TEXT,
    created_at          TEXT    NOT NULL,
    completed_at        TEXT,
    UNIQUE(city, param_name, status) ON CONFLICT IGNORE
)
"""

# Database schema for calibration data
_CREATE_CALIBRATION_SQL = """
CREATE TABLE IF NOT EXISTS calibration_data (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id            INTEGER NOT NULL,
    city                TEXT    NOT NULL,
    gfs_forecast        REAL    NOT NULL,
    actual_temp         REAL,               -- Fetched from Polymarket resolution
    resolution_source   TEXT,
    forecast_error      REAL,               -- actual - forecast
    created_at          TEXT    NOT NULL,
    FOREIGN KEY (trade_id) REFERENCES trades(id)
)
"""

# Maximum deviation from baseline (30%)
MAX_DEVIATION = 0.30

# Safety threshold: abort if WR < 30% after 5 trades
SAFETY_MIN_WR = 0.30
SAFETY_MIN_TRADES = 5

# Minimum trades per variant before evaluation
MIN_EVAL_TRADES = 10


def _connect(db_path: str) -> sqlite3.Connection:
    import os
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    return sqlite3.connect(db_path)


def create_experiment_tables(db_path: str) -> None:
    """Create experiment and calibration tables if they don't exist.

    Args:
        db_path: Path to the SQLite database file.
    """
    con = _connect(db_path)
    try:
        con.execute(_CREATE_EXPERIMENTS_SQL)
        con.execute(_CREATE_CALIBRATION_SQL)
        con.commit()
        logger.info("Experiment and calibration tables ready")
    finally:
        con.close()


def _get_losing_patterns(db_path: str) -> list[dict[str, Any]]:
    """Identify patterns from losing trades.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        List of patterns that could be hypotheses.
    """
    con = _connect(db_path)
    try:
        # Find cities with poor performance
        cur = con.execute(
            """
            SELECT city, side,
                   COUNT(*) as total,
                   SUM(CASE WHEN hit = 1 THEN 1 ELSE 0 END) as wins,
                   AVG(edge) as avg_edge
            FROM trades
            WHERE resolved = 1
              AND created_at >= datetime('now', '-7 days')
            GROUP BY city, side
            HAVING total >= 5 AND wins * 1.0 / total < 0.45
            ORDER BY wins * 1.0 / total ASC
            """
        )

        patterns = []
        for row in cur.fetchall():
            city, side, total, wins, avg_edge = row
            win_rate = wins / total if total > 0 else 0.0

            patterns.append({
                "city": city,
                "side": side,
                "win_rate": win_rate,
                "total_trades": total,
                "avg_edge": avg_edge or 0.0,
            })

        return patterns
    finally:
        con.close()


def _generate_hypotheses(db_path: str) -> list[dict[str, Any]]:
    """Generate hypotheses based on losing patterns.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        List of hypothesis dictionaries.
    """
    patterns = _get_losing_patterns(db_path)

    if not patterns:
        logger.debug("No losing patterns found - system is performing well")
        return []

    hypotheses = []

    for pattern in patterns:
        city = pattern["city"]

        # Hypothesis 1: GFS bias adjustment
        # Example: "Chicago GFS bias should be -1.5° not -2.0°"
        hypotheses.append({
            "city": city,
            "param_name": "gfs_bias",
            "hypothesis": f"{city.replace('_', ' ').title()} GFS bias should be adjusted by ±0.5°",
            "baseline_value": pattern.get("avg_edge", 0.0),
            "experiment_value": pattern.get("avg_edge", 0.0) * 0.85,  # Reduce edge requirement
            "reason": f"Win rate {pattern['win_rate']:.1%} is below target - testing bias adjustment",
        })

        # Hypothesis 2: Minimum edge threshold
        # Example: "Min edge for Miami should be 20% not 15%"
        if pattern["avg_edge"] > 0:
            hypotheses.append({
                "city": city,
                "param_name": "min_edge",
                "hypothesis": f"{city.replace('_', ' ').title()} minimum edge threshold should be higher",
                "baseline_value": 0.15,
                "experiment_value": min(0.15 * 1.30, 0.25),  # Increase by up to 30% or cap at 25%
                "reason": f"Avg edge {pattern['avg_edge']:.1%} not translating to wins",
            })

    return hypotheses


def start_experiment(db_path: str, city: str, auto: bool = True) -> dict[str, Any] | None:
    """Start a new experiment for a city.

    Args:
        db_path: Path to the SQLite database file.
        city: City to experiment on.
        auto: If True, automatically generate hypothesis. If False, use manual params.

    Returns:
        Experiment details or None if no experiment started.
    """
    con = _connect(db_path)
    try:
        # Safety check: max 1 active experiment per city
        cur = con.execute(
            """
            SELECT COUNT(*) FROM experiments
            WHERE city = ? AND status = 'active'
            """,
            (city,)
        )
        active_count = cur.fetchone()[0]

        if active_count > 0:
            logger.warning(f"Experiment already active for {city} - skipping")
            return None

        # Generate hypotheses
        hypotheses = _generate_hypotheses(db_path)

        # Find hypothesis for this city
        city_hypotheses = [h for h in hypotheses if h["city"] == city]

        if not city_hypotheses:
            logger.debug(f"No hypotheses generated for {city}")
            return None

        # Pick first hypothesis
        hyp = city_hypotheses[0]

        # Safety check: max 30% deviation
        deviation = abs(hyp["experiment_value"] - hyp["baseline_value"]) / abs(hyp["baseline_value"]) if hyp["baseline_value"] != 0 else 0
        if deviation > MAX_DEVIATION:
            logger.warning(f"Hypothesis deviation {deviation:.1%} exceeds {MAX_DEVIATION:.0%} - capping")
            if hyp["experiment_value"] > hyp["baseline_value"]:
                hyp["experiment_value"] = hyp["baseline_value"] * (1 + MAX_DEVIATION)
            else:
                hyp["experiment_value"] = hyp["baseline_value"] * (1 - MAX_DEVIATION)

        # Create experiment
        con.execute(
            """
            INSERT INTO experiments
                (city, hypothesis, param_name, baseline_value, experiment_value,
                 status, reason, created_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                city,
                hyp["hypothesis"],
                hyp["param_name"],
                hyp["baseline_value"],
                hyp["experiment_value"],
                hyp["reason"],
                datetime.now().isoformat(),
            )
        )
        con.commit()

        experiment_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]

        logger.info(f"Started experiment #{experiment_id}: {hyp['hypothesis']}")

        return {
            "id": experiment_id,
            "city": city,
            "hypothesis": hyp["hypothesis"],
            "param_name": hyp["param_name"],
            "baseline_value": hyp["baseline_value"],
            "experiment_value": hyp["experiment_value"],
        }

    finally:
        con.close()


def assign_experiment_variant(db_path: str, city: str) -> str | None:
    """Assign a trade to baseline or experiment variant (50/50 split).

    Args:
        db_path: Path to the SQLite database file.
        city: City for this trade.

    Returns:
        'baseline', 'experiment', or None if no active experiment.
    """
    con = _connect(db_path)
    try:
        cur = con.execute(
            """
            SELECT id, baseline_trades, experiment_trades
            FROM experiments
            WHERE city = ? AND status = 'active'
            LIMIT 1
            """,
            (city,)
        )
        row = cur.fetchone()

        if not row:
            return None

        exp_id, baseline_count, experiment_count = row

        # 50/50 split with balancing
        if baseline_count == experiment_count:
            variant = random.choice(["baseline", "experiment"])
        elif baseline_count < experiment_count:
            variant = "baseline"
        else:
            variant = "experiment"

        return variant

    finally:
        con.close()


def record_experiment_result(
    db_path: str,
    city: str,
    variant: str,
    hit: bool,
) -> None:
    """Record result of an experiment trade.

    Args:
        db_path: Path to the SQLite database file.
        city: City for this trade.
        variant: 'baseline' or 'experiment'.
        hit: True if trade won, False if lost.
    """
    con = _connect(db_path)
    try:
        if variant == "baseline":
            con.execute(
                """
                UPDATE experiments
                SET baseline_trades = baseline_trades + 1,
                    baseline_wins = baseline_wins + ?
                WHERE city = ? AND status = 'active'
                """,
                (1 if hit else 0, city)
            )
        elif variant == "experiment":
            con.execute(
                """
                UPDATE experiments
                SET experiment_trades = experiment_trades + 1,
                    experiment_wins = experiment_wins + ?
                WHERE city = ? AND status = 'active'
                """,
                (1 if hit else 0, city)
            )

        con.commit()

        # Check safety threshold
        _check_safety_abort(db_path, city)

        # Check if ready for evaluation
        _evaluate_experiment(db_path, city)

    finally:
        con.close()


def _check_safety_abort(db_path: str, city: str) -> None:
    """Abort experiment if experiment variant performs dangerously poorly.

    Args:
        db_path: Path to the SQLite database file.
        city: City to check.
    """
    con = _connect(db_path)
    try:
        cur = con.execute(
            """
            SELECT id, experiment_trades, experiment_wins
            FROM experiments
            WHERE city = ? AND status = 'active'
            """,
            (city,)
        )
        row = cur.fetchone()

        if not row:
            return

        exp_id, exp_trades, exp_wins = row

        if exp_trades >= SAFETY_MIN_TRADES:
            win_rate = exp_wins / exp_trades

            if win_rate < SAFETY_MIN_WR:
                con.execute(
                    """
                    UPDATE experiments
                    SET status = 'aborted',
                        winner = 'baseline',
                        reason = 'Safety abort: experiment WR ' || ? || ' < ' || ? || ' after ' || ? || ' trades',
                        completed_at = ?
                    WHERE id = ?
                    """,
                    (f"{win_rate:.1%}", f"{SAFETY_MIN_WR:.0%}", exp_trades, datetime.now().isoformat(), exp_id)
                )
                con.commit()

                logger.warning(
                    f"SAFETY ABORT: Experiment #{exp_id} for {city} - "
                    f"WR {win_rate:.1%} < {SAFETY_MIN_WR:.0%} after {exp_trades} trades"
                )

    finally:
        con.close()


def _evaluate_experiment(db_path: str, city: str) -> None:
    """Evaluate experiment if both variants have enough trades.

    Args:
        db_path: Path to the SQLite database file.
        city: City to evaluate.
    """
    con = _connect(db_path)
    try:
        cur = con.execute(
            """
            SELECT id, param_name, baseline_value, experiment_value,
                   baseline_trades, baseline_wins,
                   experiment_trades, experiment_wins
            FROM experiments
            WHERE city = ? AND status = 'active'
            """,
            (city,)
        )
        row = cur.fetchone()

        if not row:
            return

        (exp_id, param_name, baseline_val, experiment_val,
         baseline_trades, baseline_wins,
         experiment_trades, experiment_wins) = row

        # Need minimum trades in both variants
        if baseline_trades < MIN_EVAL_TRADES or experiment_trades < MIN_EVAL_TRADES:
            return

        baseline_wr = baseline_wins / baseline_trades
        experiment_wr = experiment_wins / experiment_trades

        # Determine winner (need >5% improvement to switch)
        if experiment_wr > baseline_wr + 0.05:
            winner = "experiment"
            reason = f"Experiment WR {experiment_wr:.1%} > Baseline WR {baseline_wr:.1%}"

            # Apply experiment value to learning params
            _apply_experiment_result(db_path, city, param_name, experiment_val)
        else:
            winner = "baseline"
            reason = f"Baseline WR {baseline_wr:.1%} >= Experiment WR {experiment_wr:.1%}"

        # Mark experiment as completed
        con.execute(
            """
            UPDATE experiments
            SET status = 'completed',
                winner = ?,
                reason = ?,
                completed_at = ?
            WHERE id = ?
            """,
            (winner, reason, datetime.now().isoformat(), exp_id)
        )
        con.commit()

        logger.info(
            f"Experiment #{exp_id} completed for {city}: "
            f"Winner={winner} | Baseline={baseline_wr:.1%} | Experiment={experiment_wr:.1%}"
        )

    finally:
        con.close()


def _apply_experiment_result(
    db_path: str,
    city: str,
    param_name: str,
    new_value: float,
) -> None:
    """Apply winning experiment value to learning params.

    Args:
        db_path: Path to the SQLite database file.
        city: City to update.
        param_name: Parameter name to update.
        new_value: New value to apply.
    """
    con = _connect(db_path)
    try:
        # Update learning_params table
        if param_name == "gfs_bias":
            con.execute(
                """
                UPDATE learning_params
                SET gfs_bias = ?
                WHERE city = ?
                """,
                (new_value, city)
            )
        elif param_name == "min_edge":
            # Store min_edge in avg_edge field (or create new column if needed)
            con.execute(
                """
                UPDATE learning_params
                SET avg_edge = ?
                WHERE city = ?
                """,
                (new_value, city)
            )

        con.commit()
        logger.info(f"Applied experiment result: {city} {param_name}={new_value:.2f}")

    finally:
        con.close()


def get_active_experiments(db_path: str) -> list[dict[str, Any]]:
    """Get all active experiments.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        List of active experiment details.
    """
    con = _connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        cur = con.execute(
            """
            SELECT * FROM experiments
            WHERE status = 'active'
            ORDER BY created_at DESC
            """
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        con.close()


def get_recent_experiments(db_path: str, days: int = 7) -> list[dict[str, Any]]:
    """Get recently completed/aborted experiments.

    Args:
        db_path: Path to the SQLite database file.
        days: Number of days to look back.

    Returns:
        List of experiment details.
    """
    con = _connect(db_path)
    try:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        con.row_factory = sqlite3.Row
        cur = con.execute(
            """
            SELECT * FROM experiments
            WHERE status IN ('completed', 'aborted')
              AND completed_at >= ?
            ORDER BY completed_at DESC
            """,
            (cutoff,)
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        con.close()


# ============================================================================
# CALIBRATION SYSTEM
# ============================================================================

def store_calibration_data(
    db_path: str,
    trade_id: int,
    city: str,
    gfs_forecast: float,
    actual_temp: float | None = None,
    resolution_source: str | None = None,
) -> None:
    """Store calibration data for a resolved trade.

    Args:
        db_path: Path to the SQLite database file.
        trade_id: Trade ID from trades table.
        city: City name.
        gfs_forecast: GFS forecast temperature.
        actual_temp: Actual temperature (if available).
        resolution_source: Source of resolution data.
    """
    con = _connect(db_path)
    try:
        forecast_error = None
        if actual_temp is not None:
            forecast_error = actual_temp - gfs_forecast

        con.execute(
            """
            INSERT INTO calibration_data
                (trade_id, city, gfs_forecast, actual_temp,
                 resolution_source, forecast_error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                city,
                gfs_forecast,
                actual_temp,
                resolution_source,
                forecast_error,
                datetime.now().isoformat(),
            )
        )
        con.commit()

        if actual_temp is not None:
            logger.info(
                f"Stored calibration: {city} GFS={gfs_forecast:.1f}°F "
                f"Actual={actual_temp:.1f}°F Error={forecast_error:+.1f}°F"
            )

    finally:
        con.close()


def get_calibration_stats(db_path: str, city: str, days: int = 30) -> dict[str, Any]:
    """Calculate calibration statistics for a city.

    Args:
        db_path: Path to the SQLite database file.
        city: City name.
        days: Number of days to look back.

    Returns:
        Dictionary with bias, std_dev, and sample_size.
    """
    con = _connect(db_path)
    try:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        cur = con.execute(
            """
            SELECT
                AVG(forecast_error) as mean_error,
                COUNT(*) as sample_size
            FROM calibration_data
            WHERE city = ?
              AND actual_temp IS NOT NULL
              AND created_at >= ?
            """,
            (city, cutoff)
        )
        row = cur.fetchone()

        mean_error = row[0] or 0.0
        sample_size = row[1] or 0

        # Calculate standard deviation
        cur = con.execute(
            """
            SELECT forecast_error
            FROM calibration_data
            WHERE city = ?
              AND actual_temp IS NOT NULL
              AND created_at >= ?
            """,
            (city, cutoff)
        )
        errors = [row[0] for row in cur.fetchall()]

        if len(errors) > 1:
            variance = sum((e - mean_error) ** 2 for e in errors) / (len(errors) - 1)
            std_dev = variance ** 0.5
        else:
            std_dev = 0.0

        return {
            "city": city,
            "bias": mean_error,
            "std_dev": std_dev,
            "sample_size": sample_size,
            "confidence": min(sample_size / 30.0, 1.0),  # 0-1 based on 30 samples
        }

    finally:
        con.close()


def adjust_forecast_with_calibration(
    db_path: str,
    city: str,
    raw_forecast: float,
) -> tuple[float, dict[str, Any]]:
    """Adjust a GFS forecast using calibration data.

    Args:
        db_path: Path to the SQLite database file.
        city: City name.
        raw_forecast: Raw GFS forecast temperature.

    Returns:
        Tuple of (adjusted_forecast, calibration_stats).
    """
    stats = get_calibration_stats(db_path, city, days=30)

    if stats["sample_size"] < 5:
        # Not enough data for calibration
        return raw_forecast, stats

    # Adjust forecast by subtracting the bias
    adjusted = raw_forecast - stats["bias"]

    logger.debug(
        f"Calibration adjustment for {city}: "
        f"{raw_forecast:.1f}°F → {adjusted:.1f}°F "
        f"(bias={stats['bias']:+.1f}°F, n={stats['sample_size']})"
    )

    return adjusted, stats


def run_experiment_cycle(db_path: str) -> dict[str, Any]:
    """Run experiment cycle: check for new experiments, evaluate existing ones.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Summary of experiment cycle.
    """
    create_experiment_tables(db_path)

    # Get cities with poor recent performance
    patterns = _get_losing_patterns(db_path)

    started = []
    for pattern in patterns:
        city = pattern["city"]
        result = start_experiment(db_path, city, auto=True)
        if result:
            started.append(result)

    active = get_active_experiments(db_path)
    recent = get_recent_experiments(db_path, days=7)

    return {
        "started": started,
        "active": active,
        "completed_this_week": [e for e in recent if e["status"] == "completed"],
        "aborted_this_week": [e for e in recent if e["status"] == "aborted"],
    }
