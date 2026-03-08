"""Polymarket Weather Trading Bot — entry point."""

import argparse
import logging
import os
import signal
import sys
import time

import config
from src.alerts import send_telegram_alert
from src.database import create_tables, log_trade
from src.markets import fetch_weather_markets
from src.risk import check_daily_limits
from src.signals import generate_signals
from src.weather import get_forecasts_for_cities

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_shutdown = False


def _handle_signal(signum: int, _frame: object) -> None:
    global _shutdown
    logger.info("Received signal %s — shutting down gracefully…", signum)
    _shutdown = True


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polymarket Weather Trading Bot")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Monitor mode: find signals but do not place real orders.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single scan cycle and exit (useful for testing).",
    )
    args = parser.parse_args()
    # Allow DRY_RUN env var to enable dry-run mode without changing the start command
    if not args.dry_run and os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes"):
        args.dry_run = True
    return args


def _get_clob_client():
    """Initialise the CLOB client if credentials are available."""
    key = config.POLYMARKET_API_KEY
    secret = config.POLYMARKET_API_SECRET
    passphrase = config.POLYMARKET_API_PASSPHRASE
    private_key = config.POLYMARKET_PRIVATE_KEY

    if not (key and secret and passphrase):
        logger.info("No Polymarket API credentials — live trading disabled.")
        return None

    from src.trader import initialize_client
    return initialize_client(key, secret, passphrase, private_key)


def run_scan(dry_run: bool, clob_client) -> None:
    """Execute one full scan cycle: fetch → signal → trade → alert."""
    logger.info("=== Scan cycle starting (dry_run=%s) ===", dry_run)

    # 1. Fetch weather forecasts
    logger.info("Fetching weather forecasts for %d cities…", len(config.CITIES))
    forecasts = get_forecasts_for_cities(config.CITIES)
    if not forecasts:
        logger.warning("No forecasts fetched — skipping scan.")
        return

    # 2. Fetch active weather markets
    logger.info("Fetching weather markets from Polymarket…")
    markets = fetch_weather_markets()
    if not markets:
        logger.info("No active weather markets found.")
        return

    # 3. Generate signals
    signals = generate_signals(forecasts, markets)
    if not signals:
        logger.info("No signals above threshold this cycle.")
        return

    # 4. Check daily limits and act on signals
    limits_ok = check_daily_limits(config.DB_PATH)
    if not limits_ok:
        logger.warning("Daily limits reached — no orders placed this cycle.")
        return

    for signal in signals:
        if _shutdown:
            break

        order_result = None

        if dry_run:
            logger.info(
                "[DRY RUN] Would %s %s | edge=%+.1f%% | size=$%.2f | price=%.1f%%",
                signal.recommended_side,
                signal.question[:55],
                signal.edge * 100,
                signal.recommended_size,
                signal.market_price * 100,
            )
        else:
            if clob_client is None:
                logger.warning("No CLOB client — skipping live order.")
            elif not signal.token_id:
                logger.warning(
                    "No token_id for market %s — skipping order.", signal.market_id
                )
            else:
                from src.trader import place_order
                try:
                    # Map signal side to CLOB side
                    clob_side = "BUY" if signal.recommended_side == "YES" else "SELL"
                    order_result = place_order(
                        clob_client,
                        token_id=signal.token_id,
                        side=clob_side,
                        size=signal.recommended_size,
                        price=signal.market_price,
                    )
                except RuntimeError as exc:
                    logger.error("Order failed: %s", exc)

        # Log to database (both dry-run and live)
        log_trade(config.DB_PATH, signal, order_result, dry_run=dry_run)

        # Send Telegram alert
        send_telegram_alert(signal, trade_result=order_result if not dry_run else None)

    logger.info("=== Scan cycle complete ===")


def main() -> None:
    args = _parse_args()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info("Bot starting — dry_run=%s once=%s", args.dry_run, args.once)

    # Ensure database is ready
    create_tables(config.DB_PATH)

    # Initialise CLOB client (only needed for live trading)
    clob_client = None if args.dry_run else _get_clob_client()

    interval_seconds = config.SCAN_INTERVAL_MINUTES * 60

    while not _shutdown:
        try:
            run_scan(dry_run=args.dry_run, clob_client=clob_client)
        except Exception as exc:
            logger.exception("Unhandled error in scan cycle: %s — continuing.", exc)

        if args.once or _shutdown:
            break

        logger.info("Sleeping %d minutes until next scan…", config.SCAN_INTERVAL_MINUTES)
        # Sleep in short increments to stay responsive to shutdown signals
        elapsed = 0
        while elapsed < interval_seconds and not _shutdown:
            time.sleep(5)
            elapsed += 5

    logger.info("Bot shut down cleanly.")


if __name__ == "__main__":
    main()
