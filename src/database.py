"""SQLite trade logging for the Polymarket weather bot."""

import logging
import os
import sqlite3
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

_CREATE_TRADES_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id          TEXT    NOT NULL,
    condition_id       TEXT    NOT NULL,
    token_id           TEXT    NOT NULL,
    question           TEXT    NOT NULL,
    city               TEXT    NOT NULL,
    trade_date         TEXT    NOT NULL,   -- ISO date YYYY-MM-DD
    side               TEXT    NOT NULL,   -- YES or NO
    size               REAL    NOT NULL,   -- USDC
    price              REAL    NOT NULL,   -- limit price 0–1
    edge               REAL    NOT NULL,
    forecast_prob      REAL    NOT NULL,
    confidence         REAL    NOT NULL,
    order_id           TEXT    DEFAULT '',
    dry_run            INTEGER DEFAULT 0,  -- 1 if dry run
    resolved           INTEGER DEFAULT 0,  -- 1 once market settles
    hit                INTEGER DEFAULT NULL, -- 1=correct, 0=wrong, NULL=pending
    pnl                REAL    DEFAULT 0.0,
    experiment_variant TEXT    DEFAULT NULL, -- 'baseline', 'experiment', or NULL
    created_at         TEXT    NOT NULL
)
"""

# Migration: add 'hit' column to existing databases that predate this schema
_MIGRATE_HIT_COLUMN_SQL = "ALTER TABLE trades ADD COLUMN hit INTEGER DEFAULT NULL"
_MIGRATE_EXPERIMENT_VARIANT_SQL = "ALTER TABLE trades ADD COLUMN experiment_variant TEXT DEFAULT NULL"


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    return sqlite3.connect(db_path)


def create_tables(db_path: str) -> None:
    """Create the trades table if it doesn't already exist, and run migrations.

    Args:
        db_path: Path to the SQLite database file.
    """
    con = _connect(db_path)
    try:
        con.execute(_CREATE_TRADES_SQL)
        # Migration: add 'hit' column if missing (databases created before this column existed)
        try:
            con.execute(_MIGRATE_HIT_COLUMN_SQL)
            logger.info("Migrated trades table: added 'hit' column")
        except sqlite3.OperationalError:
            pass  # Column already exists
        # Migration: add 'experiment_variant' column if missing
        try:
            con.execute(_MIGRATE_EXPERIMENT_VARIANT_SQL)
            logger.info("Migrated trades table: added 'experiment_variant' column")
        except sqlite3.OperationalError:
            pass  # Column already exists
        con.commit()
        logger.debug("Database tables verified: %s", db_path)
    finally:
        con.close()


def log_trade(
    db_path: str,
    signal: Any,
    order_result: dict[str, Any] | None,
    dry_run: bool = False,
    experiment_variant: str | None = None,
) -> int:
    """Insert a new trade row and return its row ID.

    Args:
        db_path:            Path to the SQLite database file.
        signal:             Signal dataclass (from src/signals.py).
        order_result:       Dict from place_order, or None for dry-run.
        dry_run:            True if no real order was placed.
        experiment_variant: 'baseline', 'experiment', or None.

    Returns:
        The auto-generated row ID of the inserted trade.
    """
    order_id = ""
    if order_result:
        order_id = str(
            order_result.get("orderID", order_result.get("id", ""))
        )

    trade_date = (
        signal.date.isoformat() if signal.date else date.today().isoformat()
    )

    con = _connect(db_path)
    try:
        cur = con.execute(
            """
            INSERT INTO trades
              (market_id, condition_id, token_id, question, city,
               trade_date, side, size, price, edge, forecast_prob,
               confidence, order_id, dry_run, resolved, pnl,
               experiment_variant, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0.0,?,?)
            """,
            (
                signal.market_id,
                signal.condition_id,
                signal.token_id,
                signal.question,
                signal.city,
                trade_date,
                signal.recommended_side,
                round(signal.recommended_size, 4),
                round(signal.market_price, 6),
                round(signal.edge, 6),
                round(signal.forecast_prob, 6),
                round(signal.confidence, 6),
                order_id,
                int(dry_run),
                experiment_variant,
                datetime.now().isoformat(),
            ),
        )
        con.commit()
        row_id = cur.lastrowid

        exp_info = f" [experiment:{experiment_variant}]" if experiment_variant else ""
        logger.info(
            "Trade logged: id=%d %s %s size=%.2f price=%.4f edge=%.1f%%%s",
            row_id,
            signal.city,
            signal.recommended_side,
            signal.recommended_size,
            signal.market_price,
            signal.edge * 100,
            exp_info,
        )
        return row_id
    finally:
        con.close()


def get_today_trades(db_path: str) -> list[dict[str, Any]]:
    """Return all trades logged for today.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        List of dicts, one per trade row.
    """
    today = date.today().isoformat()
    try:
        con = _connect(db_path)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT * FROM trades WHERE trade_date = ? ORDER BY id DESC",
                (today,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            con.close()
    except sqlite3.OperationalError as exc:
        logger.debug("get_today_trades: %s", exc)
        return []


def get_unresolved_trades(db_path: str) -> list[dict[str, Any]]:
    """Return all trades that have not yet been resolved.

    Returns:
        List of dicts with keys: id, market_id, condition_id, side, price, size, question.
    """
    try:
        con = _connect(db_path)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT id, market_id, condition_id, side, price, size, question "
                "FROM trades WHERE resolved = 0 ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            con.close()
    except sqlite3.OperationalError as exc:
        logger.debug("get_unresolved_trades: %s", exc)
        return []


def mark_trade_resolved(
    db_path: str,
    trade_id: int,
    hit: bool,
    pnl: float,
) -> None:
    """Mark a trade as resolved with its actual outcome.

    Args:
        db_path:  Path to the SQLite database file.
        trade_id: Row ID of the trade to update.
        hit:      True if the forecast was correct (our side won).
        pnl:      Actual profit/loss in USDC (positive = profit).
    """
    con = _connect(db_path)
    try:
        con.execute(
            "UPDATE trades SET resolved = 1, hit = ?, pnl = ? WHERE id = ?",
            (int(hit), round(pnl, 4), trade_id),
        )
        con.commit()
        logger.debug("Trade #%d marked resolved: hit=%s pnl=%.2f", trade_id, hit, pnl)
    finally:
        con.close()


def get_accuracy_stats(db_path: str) -> dict[str, Any]:
    """Return cumulative accuracy statistics across all resolved trades.

    Returns:
        Dict with keys: total_resolved, hits, misses, hit_rate, total_pnl.
    """
    try:
        con = _connect(db_path)
        try:
            row = con.execute(
                """
                SELECT
                    COUNT(*)                          AS total_resolved,
                    COALESCE(SUM(hit), 0)             AS hits,
                    COALESCE(SUM(pnl), 0.0)           AS total_pnl
                FROM trades
                WHERE resolved = 1
                """
            ).fetchone()
            total = row[0] or 0
            hits = row[1] or 0
            total_pnl = row[2] or 0.0
            return {
                "total_resolved": total,
                "hits": hits,
                "misses": total - hits,
                "hit_rate": hits / total if total else 0.0,
                "total_pnl": float(total_pnl),
            }
        finally:
            con.close()
    except sqlite3.OperationalError as exc:
        logger.debug("get_accuracy_stats: %s", exc)
        return {"total_resolved": 0, "hits": 0, "misses": 0, "hit_rate": 0.0, "total_pnl": 0.0}


def get_total_pnl(db_path: str) -> float:
    """Return the sum of realised P&L across all resolved trades.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Total P&L in USDC (positive = profit).
    """
    try:
        con = _connect(db_path)
        try:
            row = con.execute(
                "SELECT COALESCE(SUM(pnl), 0.0) FROM trades WHERE resolved = 1"
            ).fetchone()
            return float(row[0])
        finally:
            con.close()
    except sqlite3.OperationalError as exc:
        logger.debug("get_total_pnl: %s", exc)
        return 0.0
