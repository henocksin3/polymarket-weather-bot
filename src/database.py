"""SQLite trade logging for the Polymarket weather bot."""

import logging
import os
import sqlite3
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

_CREATE_TRADES_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id     TEXT    NOT NULL,
    condition_id  TEXT    NOT NULL,
    token_id      TEXT    NOT NULL,
    question      TEXT    NOT NULL,
    city          TEXT    NOT NULL,
    trade_date    TEXT    NOT NULL,   -- ISO date YYYY-MM-DD
    side          TEXT    NOT NULL,   -- YES or NO
    size          REAL    NOT NULL,   -- USDC
    price         REAL    NOT NULL,   -- limit price 0–1
    edge          REAL    NOT NULL,
    forecast_prob REAL    NOT NULL,
    confidence    REAL    NOT NULL,
    order_id      TEXT    DEFAULT '',
    dry_run       INTEGER DEFAULT 0,  -- 1 if dry run
    resolved      INTEGER DEFAULT 0,  -- 1 once market settles
    pnl           REAL    DEFAULT 0.0,
    created_at    TEXT    NOT NULL
)
"""


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    return sqlite3.connect(db_path)


def create_tables(db_path: str) -> None:
    """Create the trades table if it doesn't already exist.

    Args:
        db_path: Path to the SQLite database file.
    """
    con = _connect(db_path)
    try:
        con.execute(_CREATE_TRADES_SQL)
        con.commit()
        logger.debug("Database tables verified: %s", db_path)
    finally:
        con.close()


def log_trade(
    db_path: str,
    signal: Any,
    order_result: dict[str, Any] | None,
    dry_run: bool = False,
) -> int:
    """Insert a new trade row and return its row ID.

    Args:
        db_path:      Path to the SQLite database file.
        signal:       Signal dataclass (from src/signals.py).
        order_result: Dict from place_order, or None for dry-run.
        dry_run:      True if no real order was placed.

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
               confidence, order_id, dry_run, resolved, pnl, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0.0,?)
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
                datetime.now().isoformat(),
            ),
        )
        con.commit()
        row_id = cur.lastrowid
        logger.info(
            "Trade logged: id=%d %s %s size=%.2f price=%.4f edge=%.1f%%",
            row_id,
            signal.city,
            signal.recommended_side,
            signal.recommended_size,
            signal.market_price,
            signal.edge * 100,
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
