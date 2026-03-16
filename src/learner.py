"""Adaptive learning system with long-term analysis and historical tracking."""

import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

_CREATE_LEARNING_PARAMS_SQL = """
CREATE TABLE IF NOT EXISTS learning_params (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    city          TEXT    NOT NULL,
    side          TEXT    NOT NULL,
    win_rate_10   REAL    DEFAULT 0.0,    -- Last 10 trades
    win_rate_30   REAL    DEFAULT 0.0,    -- Last 30 trades
    win_rate_all  REAL    DEFAULT 0.0,    -- All trades
    total_trades  INTEGER DEFAULT 0,
    avg_edge      REAL    DEFAULT 0.0,
    gfs_bias      REAL    DEFAULT 0.0,
    position_size REAL    DEFAULT 5.0,
    confidence_score INTEGER DEFAULT 0,   -- 0-100 based on data volume
    active        INTEGER DEFAULT 1,
    last_updated  TEXT    NOT NULL,
    UNIQUE(city, side)
)
"""

_CREATE_LEARNING_HISTORY_SQL = """
CREATE TABLE IF NOT EXISTS learning_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    city          TEXT    NOT NULL,
    side          TEXT    NOT NULL,
    week_start    TEXT    NOT NULL,       -- ISO date of Monday
    trades_count  INTEGER DEFAULT 0,
    wins_count    INTEGER DEFAULT 0,
    win_rate      REAL    DEFAULT 0.0,
    avg_pnl       REAL    DEFAULT 0.0,
    created_at    TEXT    NOT NULL,
    UNIQUE(city, side, week_start)
)
"""


def _connect(db_path: str) -> sqlite3.Connection:
    import os
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    return sqlite3.connect(db_path)


def create_learning_tables(db_path: str) -> None:
    """Create learning tables if they don't exist.

    Args:
        db_path: Path to the SQLite database file.
    """
    con = _connect(db_path)
    try:
        # Check if old table exists
        cur = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='learning_params'"
        )
        table_exists = cur.fetchone() is not None

        if table_exists:
            # Check if migration is needed
            cur = con.execute("PRAGMA table_info(learning_params)")
            columns = [row[1] for row in cur.fetchall()]

            if "win_rate_10" not in columns:
                # Migration: rename old table and create new one
                logger.info("Migrating learning_params table to new schema...")
                con.execute("ALTER TABLE learning_params RENAME TO learning_params_old")

                # Create new table
                con.execute(_CREATE_LEARNING_PARAMS_SQL)

                # Migrate data if any exists
                try:
                    con.execute(
                        """
                        INSERT INTO learning_params
                            (city, side, win_rate_10, win_rate_30, win_rate_all,
                             total_trades, avg_edge, gfs_bias, position_size,
                             confidence_score, active, last_updated)
                        SELECT
                            city, side,
                            win_rate, win_rate, win_rate,  -- Use old win_rate for all periods
                            total_trades, avg_edge, gfs_bias, position_size,
                            0,  -- confidence_score (will be recalculated)
                            active, last_updated
                        FROM learning_params_old
                        """
                    )
                    con.execute("DROP TABLE learning_params_old")
                    logger.info("Migration complete: old data preserved")
                except sqlite3.OperationalError:
                    logger.warning("Migration failed, starting fresh")
                    pass

        else:
            # Fresh install
            con.execute(_CREATE_LEARNING_PARAMS_SQL)

        # Create history table
        con.execute(_CREATE_LEARNING_HISTORY_SQL)

        con.commit()
        logger.debug("Learning tables verified: %s", db_path)
    finally:
        con.close()


def _get_multi_period_stats(
    con: sqlite3.Connection,
    city: str,
    side: str,
) -> dict[str, Any]:
    """Get statistics across multiple time periods.

    Args:
        con: Database connection.
        city: City name.
        side: Trading side (YES or NO).

    Returns:
        Dict with win_rate_10, win_rate_30, win_rate_all, total_trades, avg_edge.
    """
    # Last 10 trades
    cur = con.execute(
        """
        SELECT
            COUNT(*) as total,
            COALESCE(SUM(CASE WHEN hit = 1 THEN 1 ELSE 0 END), 0) as wins,
            COALESCE(AVG(edge), 0.0) as avg_edge
        FROM (
            SELECT * FROM trades
            WHERE city = ? AND side = ? AND resolved = 1
            ORDER BY id DESC LIMIT 10
        )
        """,
        (city, side),
    )
    row = cur.fetchone()
    total_10 = row[0] or 0
    wins_10 = row[1] or 0
    avg_edge = row[2] or 0.0

    # Last 30 trades
    cur = con.execute(
        """
        SELECT
            COUNT(*) as total,
            COALESCE(SUM(CASE WHEN hit = 1 THEN 1 ELSE 0 END), 0) as wins
        FROM (
            SELECT * FROM trades
            WHERE city = ? AND side = ? AND resolved = 1
            ORDER BY id DESC LIMIT 30
        )
        """,
        (city, side),
    )
    row = cur.fetchone()
    total_30 = row[0] or 0
    wins_30 = row[1] or 0

    # All trades
    cur = con.execute(
        """
        SELECT
            COUNT(*) as total,
            COALESCE(SUM(CASE WHEN hit = 1 THEN 1 ELSE 0 END), 0) as wins
        FROM trades
        WHERE city = ? AND side = ? AND resolved = 1
        """,
        (city, side),
    )
    row = cur.fetchone()
    total_all = row[0] or 0
    wins_all = row[1] or 0

    return {
        "total_trades": total_all,
        "win_rate_10": wins_10 / total_10 if total_10 > 0 else 0.0,
        "win_rate_30": wins_30 / total_30 if total_30 > 0 else 0.0,
        "win_rate_all": wins_all / total_all if total_all > 0 else 0.0,
        "avg_edge": avg_edge,
    }


def _calculate_confidence_score(total_trades: int) -> int:
    """Calculate confidence score (0-100) based on number of trades.

    Args:
        total_trades: Total number of resolved trades.

    Returns:
        Confidence score 0-100.
        - 0-9 trades: 0-45% confidence
        - 10-29 trades: 50-75% confidence
        - 30-99 trades: 80-95% confidence
        - 100+ trades: 100% confidence
    """
    if total_trades == 0:
        return 0
    elif total_trades < 10:
        return int(total_trades * 5)  # 0-45
    elif total_trades < 30:
        return 50 + int((total_trades - 10) * 1.25)  # 50-75
    elif total_trades < 100:
        return 80 + int((total_trades - 30) * 0.21)  # 80-95
    else:
        return 100


def _calculate_gfs_bias(con: sqlite3.Connection, city: str) -> float:
    """Calculate GFS temperature bias for a city.

    Estimates bias by looking at which side wins:
    - If YES wins more → GFS underestimates (negative bias)
    - If NO wins more → GFS overestimates (positive bias)

    Args:
        con: Database connection.
        city: City name.

    Returns:
        Estimated bias in degrees (positive = overestimate).
    """
    cur = con.execute(
        """
        SELECT
            side,
            COUNT(*) as total,
            SUM(CASE WHEN hit = 1 THEN 1 ELSE 0 END) as wins
        FROM trades
        WHERE city = ? AND resolved = 1
        GROUP BY side
        """,
        (city,),
    )

    yes_total = yes_wins = no_total = no_wins = 0
    for row in cur.fetchall():
        side, total, wins = row
        if side == "YES":
            yes_total, yes_wins = total, wins
        else:
            no_total, no_wins = total, wins

    yes_rate = yes_wins / yes_total if yes_total > 0 else 0.5
    no_rate = no_wins / no_total if no_total > 0 else 0.5

    # Bias estimate: positive if GFS overestimates
    bias = (0.5 - yes_rate) * 4.0

    return round(bias, 2)


def _should_deactivate(stats: dict[str, Any]) -> bool:
    """Determine if a strategy should be deactivated.

    A strategy is deactivated ONLY if ALL three periods are below 45%.

    Args:
        stats: Multi-period statistics.

    Returns:
        True if should deactivate, False otherwise.
    """
    # Need at least 10 trades to deactivate
    if stats["total_trades"] < 10:
        return False

    # All three periods must be below 45%
    return (
        stats["win_rate_10"] < 0.45
        and stats["win_rate_30"] < 0.45
        and stats["win_rate_all"] < 0.45
    )


def _calculate_position_size(stats: dict[str, Any]) -> float:
    """Calculate recommended position size based on performance.

    Position size is increased ONLY if both 30-trades and all-trades are > 60%.

    Args:
        stats: Multi-period statistics.

    Returns:
        Recommended position size in USD.
    """
    default_size = 5.0

    # Need at least 30 trades to increase size
    if stats["total_trades"] < 30:
        return default_size

    # Both 30-trade and all-time must be > 60%
    if stats["win_rate_30"] > 0.60 and stats["win_rate_all"] > 0.60:
        # Scale based on the lower of the two win rates
        min_wr = min(stats["win_rate_30"], stats["win_rate_all"])
        scale_factor = min((min_wr - 0.60) / 0.20, 1.0)  # 0-1
        position_size = 5.0 + (15.0 * scale_factor)  # $5 to $20
        return round(position_size, 2)

    return default_size


def _update_learning_params(
    db_path: str,
    city: str,
    side: str,
    stats: dict[str, Any],
    gfs_bias: float,
) -> dict[str, Any]:
    """Update or insert learning parameters for a city/side combination.

    Args:
        db_path: Path to the SQLite database file.
        city: City name.
        side: Trading side (YES or NO).
        stats: Statistics dict from _get_multi_period_stats.
        gfs_bias: GFS temperature bias.

    Returns:
        Dict with changes made (deactivated, position_changed, etc.).
    """
    active = 0 if _should_deactivate(stats) else 1
    position_size = _calculate_position_size(stats)
    confidence_score = _calculate_confidence_score(stats["total_trades"])

    # Get previous state to track changes
    con = _connect(db_path)
    try:
        prev_cur = con.execute(
            "SELECT active, position_size FROM learning_params WHERE city = ? AND side = ?",
            (city, side),
        )
        prev_row = prev_cur.fetchone()
        prev_active = prev_row[0] if prev_row else 1
        prev_size = prev_row[1] if prev_row else 5.0

        # Insert or update
        con.execute(
            """
            INSERT INTO learning_params
                (city, side, win_rate_10, win_rate_30, win_rate_all,
                 total_trades, avg_edge, gfs_bias, position_size,
                 confidence_score, active, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(city, side) DO UPDATE SET
                win_rate_10 = excluded.win_rate_10,
                win_rate_30 = excluded.win_rate_30,
                win_rate_all = excluded.win_rate_all,
                total_trades = excluded.total_trades,
                avg_edge = excluded.avg_edge,
                gfs_bias = excluded.gfs_bias,
                position_size = excluded.position_size,
                confidence_score = excluded.confidence_score,
                active = excluded.active,
                last_updated = excluded.last_updated
            """,
            (
                city,
                side,
                round(stats["win_rate_10"], 4),
                round(stats["win_rate_30"], 4),
                round(stats["win_rate_all"], 4),
                stats["total_trades"],
                round(stats["avg_edge"], 4),
                gfs_bias,
                position_size,
                confidence_score,
                active,
                datetime.now().isoformat(),
            ),
        )
        con.commit()

        # Track changes
        changes = {
            "deactivated": prev_active == 1 and active == 0,
            "reactivated": prev_active == 0 and active == 1,
            "position_increased": position_size > prev_size + 0.5,
            "position_decreased": position_size < prev_size - 0.5,
            "prev_size": prev_size,
            "new_size": position_size,
        }

        if changes["deactivated"]:
            logger.warning(
                "Deactivating %s %s: WR_10=%.1f%%, WR_30=%.1f%%, WR_all=%.1f%% (all <45%%)",
                city,
                side,
                stats["win_rate_10"] * 100,
                stats["win_rate_30"] * 100,
                stats["win_rate_all"] * 100,
            )
        elif changes["reactivated"]:
            logger.info(
                "Reactivating %s %s: WR improved",
                city,
                side,
            )

        if changes["position_increased"]:
            logger.info(
                "Increasing position size for %s %s: $%.2f → $%.2f (WR_30=%.1f%%, WR_all=%.1f%%)",
                city,
                side,
                prev_size,
                position_size,
                stats["win_rate_30"] * 100,
                stats["win_rate_all"] * 100,
            )

        return changes

    finally:
        con.close()


def _update_weekly_history(db_path: str) -> None:
    """Update weekly historical performance data.

    Creates one record per city/side/week with aggregated stats.

    Args:
        db_path: Path to the SQLite database file.
    """
    con = _connect(db_path)
    try:
        # Calculate Monday of current week
        today = datetime.now().date()
        days_since_monday = today.weekday()
        monday = today - timedelta(days=days_since_monday)
        week_start = monday.isoformat()

        # Get all city/side combinations with trades this week
        cur = con.execute(
            """
            SELECT
                city,
                side,
                COUNT(*) as trades_count,
                SUM(CASE WHEN hit = 1 THEN 1 ELSE 0 END) as wins_count,
                AVG(pnl) as avg_pnl
            FROM trades
            WHERE resolved = 1
            AND trade_date >= ?
            GROUP BY city, side
            """,
            (week_start,),
        )

        for row in cur.fetchall():
            city, side, trades_count, wins_count, avg_pnl = row
            win_rate = wins_count / trades_count if trades_count > 0 else 0.0

            # Insert or update weekly record
            con.execute(
                """
                INSERT INTO learning_history
                    (city, side, week_start, trades_count, wins_count,
                     win_rate, avg_pnl, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(city, side, week_start) DO UPDATE SET
                    trades_count = excluded.trades_count,
                    wins_count = excluded.wins_count,
                    win_rate = excluded.win_rate,
                    avg_pnl = excluded.avg_pnl,
                    created_at = excluded.created_at
                """,
                (
                    city,
                    side,
                    week_start,
                    trades_count,
                    wins_count,
                    round(win_rate, 4),
                    round(avg_pnl, 4) if avg_pnl else 0.0,
                    datetime.now().isoformat(),
                ),
            )

        con.commit()
        logger.debug("Updated weekly history for week starting %s", week_start)

    finally:
        con.close()


def analyze_and_update_params(db_path: str) -> dict[str, Any]:
    """Analyze resolved trades and update learning parameters.

    Uses multi-period analysis and strict criteria for deactivation/size increases.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Summary dict with adjustments made.
    """
    create_learning_tables(db_path)

    con = _connect(db_path)
    try:
        # Get all unique city/side combinations from resolved trades
        cur = con.execute(
            """
            SELECT DISTINCT city, side FROM trades
            WHERE resolved = 1
            ORDER BY city, side
            """
        )
        combinations = cur.fetchall()

        summary = {
            "analyzed": 0,
            "deactivated": [],
            "reactivated": [],
            "position_increased": [],
            "position_decreased": [],
        }

        for city, side in combinations:
            stats = _get_multi_period_stats(con, city, side)

            if stats["total_trades"] == 0:
                continue

            gfs_bias = _calculate_gfs_bias(con, city)
            changes = _update_learning_params(db_path, city, side, stats, gfs_bias)

            summary["analyzed"] += 1

            if changes["deactivated"]:
                summary["deactivated"].append(f"{city} {side}")

            if changes["reactivated"]:
                summary["reactivated"].append(f"{city} {side}")

            if changes["position_increased"]:
                summary["position_increased"].append(
                    f"{city} {side}: ${changes['prev_size']:.2f} → ${changes['new_size']:.2f}"
                )

            if changes["position_decreased"]:
                summary["position_decreased"].append(
                    f"{city} {side}: ${changes['prev_size']:.2f} → ${changes['new_size']:.2f}"
                )

        # Update weekly history
        _update_weekly_history(db_path)

        logger.info(
            "Learning update complete: analyzed %d city/side combinations",
            summary["analyzed"],
        )

        if summary["deactivated"]:
            logger.warning("Deactivated: %s", ", ".join(summary["deactivated"]))

        if summary["reactivated"]:
            logger.info("Reactivated: %s", ", ".join(summary["reactivated"]))

        if summary["position_increased"]:
            logger.info("Position increased: %s", ", ".join(summary["position_increased"]))

        return summary

    finally:
        con.close()


def get_learning_params(db_path: str, city: str, side: str) -> dict[str, Any] | None:
    """Get learning parameters for a specific city/side.

    Args:
        db_path: Path to the SQLite database file.
        city: City name.
        side: Trading side (YES or NO).

    Returns:
        Dict with learning parameters, or None if not found.
    """
    con = _connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        cur = con.execute(
            "SELECT * FROM learning_params WHERE city = ? AND side = ?",
            (city, side),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def get_weekly_history(db_path: str, weeks: int = 4) -> list[dict[str, Any]]:
    """Get weekly historical performance data.

    Args:
        db_path: Path to the SQLite database file.
        weeks: Number of recent weeks to fetch.

    Returns:
        List of weekly performance records.
    """
    con = _connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        cur = con.execute(
            """
            SELECT * FROM learning_history
            ORDER BY week_start DESC, city, side
            LIMIT ?
            """,
            (weeks * 10,),  # Approx 10 city/side combos
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        con.close()


def is_side_active(db_path: str, city: str, side: str) -> bool:
    """Check if a specific city/side combination is active for trading.

    Args:
        db_path: Path to the SQLite database file.
        city: City name.
        side: Trading side (YES or NO).

    Returns:
        True if active (or no params exist yet), False if deactivated.
    """
    params = get_learning_params(db_path, city, side)
    if params is None:
        return True  # No params yet, allow trading
    return params["active"] == 1


def get_recommended_position_size(db_path: str, city: str, side: str) -> float:
    """Get recommended position size for a city/side based on learning.

    Args:
        db_path: Path to the SQLite database file.
        city: City name.
        side: Trading side (YES or NO).

    Returns:
        Recommended position size in USD.
    """
    params = get_learning_params(db_path, city, side)
    if params is None:
        return 5.0  # Default
    return params["position_size"]
