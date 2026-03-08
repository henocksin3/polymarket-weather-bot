"""Position sizing (Kelly criterion) and daily risk limit checks."""

import logging
import sqlite3
from datetime import date

from config import (
    BANKROLL,
    KELLY_FRACTION,
    MAX_DAILY_LOSS_PCT,
    MAX_DAILY_TRADES,
    MAX_POSITION_USD,
)

logger = logging.getLogger(__name__)


def kelly_size(
    forecast_prob: float,
    market_price: float,
    bankroll: float = BANKROLL,
    kelly_fraction: float = KELLY_FRACTION,
) -> float:
    """Compute conservative fractional Kelly position size in USDC.

    Args:
        forecast_prob:  Estimated probability of winning (0–1).
        market_price:   Current market price for the side we're buying (0–1).
        bankroll:       Total capital available (USDC).
        kelly_fraction: Fraction of full Kelly to use (conservative scaling).

    Returns:
        Position size in USDC, capped at MAX_POSITION_USD.
        Returns 0.0 if Kelly fraction is non-positive (no edge).
    """
    if market_price <= 0 or market_price >= 1:
        return 0.0

    b = (1 / market_price) - 1  # decimal odds
    p = forecast_prob
    q = 1 - p
    full_kelly = (p * b - q) / b

    if full_kelly <= 0:
        return 0.0

    size = full_kelly * kelly_fraction * bankroll
    return min(size, MAX_POSITION_USD)


def check_daily_limits(db_path: str) -> bool:
    """Check whether we are within daily trading limits.

    Reads today's trades from the SQLite database and verifies:
    - Number of trades is below MAX_DAILY_TRADES.
    - Total realised P&L today is above -MAX_DAILY_LOSS_PCT * BANKROLL.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        True if trading is allowed, False if a limit has been hit.
    """
    today = date.today().isoformat()

    try:
        con = sqlite3.connect(db_path)
        cur = con.cursor()

        cur.execute(
            "SELECT COUNT(*), COALESCE(SUM(pnl), 0) FROM trades WHERE trade_date = ?",
            (today,),
        )
        row = cur.fetchone()
        con.close()
    except sqlite3.OperationalError as exc:
        # Table may not exist yet on the very first run — treat as no trades.
        logger.debug(f"DB read failed (possibly first run): {exc}")
        return True

    trade_count, daily_pnl = row

    if trade_count >= MAX_DAILY_TRADES:
        logger.warning(
            f"Daily trade limit reached: {trade_count}/{MAX_DAILY_TRADES}"
        )
        return False

    max_loss = -MAX_DAILY_LOSS_PCT * BANKROLL
    if daily_pnl < max_loss:
        logger.warning(
            f"Daily loss limit reached: P&L={daily_pnl:.2f} < threshold={max_loss:.2f}"
        )
        return False

    logger.debug(
        f"Daily limits OK: trades={trade_count}/{MAX_DAILY_TRADES}, "
        f"P&L={daily_pnl:.2f} (min={max_loss:.2f})"
    )
    return True


def get_current_exposure(db_path: str) -> float:
    """Return total USDC currently committed to open (unresolved) positions.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Sum of `size` for all trades where `resolved` is 0 (open positions).
        Returns 0.0 if the table doesn't exist yet.
    """
    try:
        con = sqlite3.connect(db_path)
        cur = con.cursor()
        cur.execute(
            "SELECT COALESCE(SUM(size), 0) FROM trades WHERE resolved = 0"
        )
        row = cur.fetchone()
        con.close()
        return float(row[0])
    except sqlite3.OperationalError as exc:
        logger.debug(f"DB read failed (possibly first run): {exc}")
        return 0.0
