"""Polymarket Weather Trading Bot — entry point."""

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime

import config
from src.alerts import send_telegram_alert
from src.database import create_tables, get_accuracy_stats, log_trade
from src.experiments import (
    assign_experiment_variant,
    create_experiment_tables,
    run_experiment_cycle,
)
from src.learner import analyze_and_update_params, create_learning_tables
from src.markets import fetch_weather_markets
from src.reporter import save_report, send_telegram_report
from src.resolver import resolve_pending_trades
from src.risk import check_daily_limits
from src.signals import generate_signals
from src.weather import get_forecasts_for_cities

# LOG_DIR defaults to DATA_DIR/logs when a Railway Volume is mounted (DATA_DIR=/data),
# falling back to a local "logs/" directory for development.
_data_dir = os.getenv("DATA_DIR", "")
LOG_DIR = os.getenv("LOG_DIR", os.path.join(_data_dir, "logs") if _data_dir else "logs")
_LOG_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

logging.basicConfig(level=logging.INFO, format=_LOG_FMT)
logger = logging.getLogger(__name__)


def _setup_file_logging() -> None:
    """Add a daily rotating file handler so every run is persisted to disk."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        log_file = os.path.join(LOG_DIR, f"bot_{datetime.now().strftime('%Y%m%d')}.log")
        fh = logging.FileHandler(log_file)
        fh.setFormatter(logging.Formatter(_LOG_FMT))
        logging.getLogger().addHandler(fh)
        logger.debug("File logging active: %s", log_file)
    except OSError as exc:
        logger.warning("Could not set up file logging: %s", exc)


def _write_scan_summary(signals: list, dry_run: bool) -> None:
    """Print and write a structured scan summary with per-signal simulated P&L.

    Simulated P&L per signal = edge × size (expected value of the trade).
    This is logged to stdout (visible in Railway dashboard) and to a timestamped
    file in LOG_DIR for local / volume-backed inspection.
    """
    mode = "DRY-RUN" if dry_run else "LIVE"
    now = datetime.now()
    sep = "=" * 62

    lines = [
        sep,
        f"  SCAN SUMMARY  {now.strftime('%Y-%m-%d %H:%M')}  [{mode}]",
        sep,
        f"  Signals found: {len(signals)}",
        "",
        f"  {'Side':<4}  {'Edge':>7}  {'Size':>6}  {'Exp P&L':>8}  Question",
        f"  {'-'*4}  {'-'*7}  {'-'*6}  {'-'*8}  {'-'*40}",
    ]

    total_expected_pnl = 0.0
    for sig in signals:
        if sig.recommended_side == "YES":
            exp_pnl = sig.edge * sig.recommended_size
        else:
            exp_pnl = (1 - sig.market_price) * sig.recommended_size
        total_expected_pnl += exp_pnl
        lines.append(
            f"  {sig.recommended_side:<4}  {sig.edge:>+7.1%}  "
            f"${sig.recommended_size:>5.2f}  ${exp_pnl:>+7.2f}  "
            f"{sig.question[:48]}"
        )

    stats = get_accuracy_stats(config.DB_PATH)
    if stats["total_resolved"] > 0:
        accuracy_line = (
            f"  Accuracy: {stats['hits']}/{stats['total_resolved']} "
            f"({stats['hit_rate']:.0%}) | "
            f"Realised P&L: ${stats['total_pnl']:+.2f}"
        )
    else:
        accuracy_line = "  Accuracy: no resolved trades yet"

    lines += [
        f"  {'-'*58}",
        f"  Expected P&L this run: ${total_expected_pnl:+.2f}",
        accuracy_line,
        sep,
    ]

    summary = "\n".join(lines)
    logger.info("\n%s", summary)

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        fname = os.path.join(LOG_DIR, f"scan_{now.strftime('%Y%m%d_%H%M')}.log")
        with open(fname, "w") as f:
            f.write(summary + "\n")
    except OSError as exc:
        logger.debug("Could not write summary file: %s", exc)

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
    """Execute one full scan cycle: resolve → learn → report → fetch → signal → trade → alert."""
    logger.info("=== Scan cycle starting (dry_run=%s) ===", dry_run)

    # 0. Resolve any previously logged trades that have now settled
    try:
        resolved_count = resolve_pending_trades(config.DB_PATH)

        # If trades were resolved, run learning and generate report
        if resolved_count > 0:
            logger.info("Running adaptive learning analysis...")
            try:
                summary = analyze_and_update_params(config.DB_PATH)
                logger.info(
                    "Learning complete: analyzed %d combinations, deactivated %d",
                    summary["analyzed"],
                    len(summary["deactivated"]),
                )

                # Run experiment cycle
                logger.info("Running experiment cycle...")
                try:
                    exp_summary = run_experiment_cycle(config.DB_PATH)
                    if exp_summary["started"]:
                        logger.info(
                            "Started %d new experiment(s): %s",
                            len(exp_summary["started"]),
                            [e["hypothesis"] for e in exp_summary["started"]],
                        )
                    if exp_summary["active"]:
                        logger.info("%d active experiment(s)", len(exp_summary["active"]))
                except Exception as exc:
                    logger.warning("Experiment cycle error (non-fatal): %s", exc)

                # Generate and save report
                report_path = save_report(config.DB_PATH, since_hours=24)
                logger.info("Report saved: %s", report_path)

                # Send report to Telegram
                send_telegram_report(config.DB_PATH, since_hours=24)
            except Exception as exc:
                logger.warning("Learning/reporting error (non-fatal): %s", exc)
    except Exception as exc:
        logger.warning("Resolver error (non-fatal): %s", exc)

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
    _write_scan_summary(signals, dry_run)
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

        # Assign experiment variant if there's an active experiment for this city
        experiment_variant = assign_experiment_variant(config.DB_PATH, signal.city)

        # Log to database (both dry-run and live)
        log_trade(
            config.DB_PATH,
            signal,
            order_result,
            dry_run=dry_run,
            experiment_variant=experiment_variant,
        )

        # Send Telegram alert
        send_telegram_alert(signal, trade_result=order_result if not dry_run else None)

    logger.info("=== Scan cycle complete ===")


def main() -> None:
    args = _parse_args()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    _setup_file_logging()
    logger.info("Bot starting — dry_run=%s once=%s", args.dry_run, args.once)

    # Ensure database is ready
    create_tables(config.DB_PATH)
    create_learning_tables(config.DB_PATH)
    create_experiment_tables(config.DB_PATH)

    # Note: Telegram commands now handled by webhook server (src/webhook_server.py)
    # This script only runs trading logic

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
