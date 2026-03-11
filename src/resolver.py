"""Resolve pending trades by checking Polymarket outcomes via the Gamma API."""

import json
import logging

import httpx

from config import GAMMA_API_BASE
from src.database import get_unresolved_trades, mark_trade_resolved

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 30


def _fetch_resolution(market_id: str) -> tuple[bool, bool | None]:
    """Check whether a market has resolved and which side won.

    Args:
        market_id: Polymarket market ID.

    Returns:
        (resolved, yes_won):
          - resolved=False → market still open, yes_won=None.
          - resolved=True, yes_won=True  → YES outcome paid out.
          - resolved=True, yes_won=False → NO outcome paid out.
          - resolved=True, yes_won=None  → could not determine winner.
    """
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
            response = client.get(f"{GAMMA_API_BASE}/markets/{market_id}")
            response.raise_for_status()
            raw = response.json()

        if not raw:
            return False, None

        # Check if market is closed with determined outcome prices
        prices_raw = raw.get("outcomePrices", [])
        if not raw.get("closed", False):
            return False, None
        if isinstance(prices_raw, str):
            prices_raw = json.loads(prices_raw)

        prices = [float(p) for p in prices_raw]
        if not prices or len(prices) < 2:
            return False, None

        # outcomePrices[0] corresponds to the first outcome (YES in binary markets).
        # A resolved winning outcome has price 1.0; losers have 0.0.
        # Only consider resolved if we have a clear winner
        if prices[0] >= 0.99:
            return True, True
        elif prices[1] >= 0.99:
            return True, False
        else:
            # Market closed but no clear winner yet
            return False, None

    except httpx.HTTPStatusError as exc:
        logger.warning("HTTP error checking resolution for %s: %s", market_id, exc)
        return False, None
    except Exception as exc:
        logger.warning("Unexpected error checking resolution for %s: %s", market_id, exc)
        return False, None


def _calculate_pnl(
    side: str,
    market_price: float,
    size: float,
    yes_won: bool,
) -> tuple[bool, float]:
    """Compute hit/miss and actual P&L for a resolved trade.

    Args:
        side:         "YES" or "NO" — the side we bought.
        market_price: YES price at the time of the trade (0–1).
        size:         Trade size in USDC.
        yes_won:      True if the YES outcome resolved to 1.0.

    Returns:
        (hit, pnl) — hit=True means our forecast was correct.
    """
    if side == "YES":
        hit = yes_won
        pnl = size * (1.0 / market_price - 1.0) if hit else -size
    else:  # NO
        no_price = 1.0 - market_price
        hit = not yes_won
        pnl = size * (1.0 / no_price - 1.0) if (hit and no_price > 0) else -size

    return hit, round(pnl, 4)


def resolve_pending_trades(db_path: str) -> int:
    """Check all unresolved trades against Polymarket and record outcomes.

    For each unresolved trade, fetches the current market state. If the
    market has resolved, computes hit/miss and actual P&L and writes the
    result back to the database.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Number of trades newly resolved in this call.
    """
    trades = get_unresolved_trades(db_path)
    if not trades:
        logger.debug("No unresolved trades to check.")
        return 0

    logger.info("Checking resolution for %d unresolved trade(s)…", len(trades))
    resolved_count = 0

    for trade in trades:
        resolved, yes_won = _fetch_resolution(trade["market_id"])
        if not resolved or yes_won is None:
            continue

        hit, pnl = _calculate_pnl(
            trade["side"],
            trade["price"],
            trade["size"],
            yes_won,
        )
        mark_trade_resolved(db_path, trade["id"], hit=hit, pnl=pnl)
        resolved_count += 1
        logger.info(
            "Trade #%d resolved — %s | %s → %s | pnl=$%+.2f | %s",
            trade["id"],
            trade["side"],
            "YES won" if yes_won else "NO won",
            "HIT ✓" if hit else "MISS ✗",
            pnl,
            trade["question"][:55],
        )

    logger.info("Resolved %d/%d pending trade(s).", resolved_count, len(trades))
    return resolved_count
