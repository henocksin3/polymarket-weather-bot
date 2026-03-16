"""Generate trading performance reports for monitoring and analysis."""

import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    return sqlite3.connect(db_path)


def _get_report_stats(db_path: str, since_hours: int = 24) -> dict[str, Any]:
    """Get trading statistics for the reporting period.

    Args:
        db_path: Path to the SQLite database file.
        since_hours: Hours to look back for the report.

    Returns:
        Dict with statistics for the reporting period.
    """
    cutoff = (datetime.now() - timedelta(hours=since_hours)).isoformat()

    con = _connect(db_path)
    try:
        # Overall stats for the period
        cur = con.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN resolved = 1 THEN 1 ELSE 0 END) as resolved,
                SUM(CASE WHEN resolved = 1 AND hit = 1 THEN 1 ELSE 0 END) as wins,
                COALESCE(SUM(CASE WHEN resolved = 1 THEN pnl ELSE 0 END), 0.0) as pnl
            FROM trades
            WHERE created_at >= ?
            """,
            (cutoff,),
        )
        row = cur.fetchone()
        total = row[0] or 0
        resolved = row[1] or 0
        wins = row[2] or 0
        pnl = row[3] or 0.0

        # Per-city breakdown
        cur = con.execute(
            """
            SELECT
                city,
                COUNT(*) as total,
                SUM(CASE WHEN hit = 1 THEN 1 ELSE 0 END) as wins,
                COALESCE(SUM(pnl), 0.0) as pnl
            FROM trades
            WHERE created_at >= ? AND resolved = 1
            GROUP BY city
            ORDER BY city
            """,
            (cutoff,),
        )
        city_stats = [
            {
                "city": row[0],
                "total": row[1],
                "wins": row[2],
                "pnl": row[3],
                "win_rate": row[2] / row[1] if row[1] > 0 else 0.0,
            }
            for row in cur.fetchall()
        ]

        # All-time stats for comparison
        cur = con.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN hit = 1 THEN 1 ELSE 0 END) as wins,
                COALESCE(SUM(pnl), 0.0) as pnl
            FROM trades
            WHERE resolved = 1
            """
        )
        all_time = cur.fetchone()
        all_time_total = all_time[0] or 0
        all_time_wins = all_time[1] or 0
        all_time_pnl = all_time[2] or 0.0

        return {
            "period_hours": since_hours,
            "total_trades": total,
            "resolved_trades": resolved,
            "wins": wins,
            "losses": resolved - wins,
            "win_rate": wins / resolved if resolved > 0 else 0.0,
            "pnl": pnl,
            "city_stats": city_stats,
            "all_time_total": all_time_total,
            "all_time_wins": all_time_wins,
            "all_time_pnl": all_time_pnl,
            "all_time_win_rate": all_time_wins / all_time_total if all_time_total > 0 else 0.0,
        }
    finally:
        con.close()


def _get_learning_adjustments(db_path: str) -> list[dict[str, Any]]:
    """Get recent learning parameter adjustments.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        List of learning parameter records.
    """
    con = _connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        cur = con.execute(
            """
            SELECT city, side, win_rate_10, win_rate_30, win_rate_all,
                   total_trades, position_size, confidence_score, active
            FROM learning_params
            ORDER BY city, side
            """
        )
        return [dict(row) for row in cur.fetchall()]
    except sqlite3.OperationalError:
        # Table doesn't exist yet
        return []
    finally:
        con.close()


def generate_text_report(db_path: str, since_hours: int = 24) -> str:
    """Generate a text-based trading report.

    Args:
        db_path: Path to the SQLite database file.
        since_hours: Hours to look back for the report.

    Returns:
        Formatted text report as a string.
    """
    stats = _get_report_stats(db_path, since_hours)
    adjustments = _get_learning_adjustments(db_path)

    # Get weekly history
    try:
        from src.learner import get_weekly_history
        weekly_history = get_weekly_history(db_path, weeks=2)
    except Exception:
        weekly_history = []

    # Get experiment data
    active_experiments = []
    recent_experiments = []
    try:
        from src.experiments import get_active_experiments, get_recent_experiments
        active_experiments = get_active_experiments(db_path)
        recent_experiments = get_recent_experiments(db_path, days=7)
    except Exception:
        pass

    now = datetime.now()
    lines = [
        "=" * 70,
        f"  POLYMARKET WEATHER BOT - TRADING REPORT",
        f"  {now.strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 70,
        "",
        f"📊 PERIOD SUMMARY (last {stats['period_hours']} hours)",
        "-" * 70,
        f"  Total trades:     {stats['total_trades']}",
        f"  Resolved trades:  {stats['resolved_trades']}",
        f"  Wins:             {stats['wins']}",
        f"  Losses:           {stats['losses']}",
        f"  Win rate:         {stats['win_rate']:.1%}",
        f"  P&L:              ${stats['pnl']:+.2f}",
        "",
    ]

    if stats["city_stats"]:
        lines.extend([
            "🏙️  PER-CITY BREAKDOWN (resolved trades)",
            "-" * 70,
        ])
        for cs in stats["city_stats"]:
            city_name = cs["city"].replace("_", " ").title()
            lines.append(
                f"  {city_name:12}  {cs['total']:2} trades | "
                f"{cs['wins']:2}W {cs['total'] - cs['wins']:2}L | "
                f"WR: {cs['win_rate']:5.1%} | P&L: ${cs['pnl']:+7.2f}"
            )
        lines.append("")

    lines.extend([
        "📈 ALL-TIME PERFORMANCE",
        "-" * 70,
        f"  Total resolved:   {stats['all_time_total']}",
        f"  Wins:             {stats['all_time_wins']}",
        f"  Losses:           {stats['all_time_total'] - stats['all_time_wins']}",
        f"  Win rate:         {stats['all_time_win_rate']:.1%}",
        f"  Total P&L:        ${stats['all_time_pnl']:+.2f}",
        "",
    ])

    if adjustments:
        lines.extend([
            "🔧 CURRENT LEARNING PARAMETERS",
            "-" * 70,
        ])
        for adj in adjustments:
            status = "✓ ACTIVE" if adj["active"] == 1 else "✗ DISABLED"
            city_name = adj["city"].replace("_", " ").title()

            # Show multi-period win rates
            wr_10 = adj.get("win_rate_10", adj.get("win_rate", 0.0))
            wr_30 = adj.get("win_rate_30", adj.get("win_rate", 0.0))
            wr_all = adj.get("win_rate_all", adj.get("win_rate", 0.0))
            confidence = adj.get("confidence_score", 0)
            trades = adj.get("total_trades", 0)

            lines.append(
                f"  {city_name:12} {adj['side']:>3}  |  "
                f"WR(10/30/all): {wr_10:4.0%}/{wr_30:4.0%}/{wr_all:4.0%}  |  "
                f"Size: ${adj['position_size']:5.2f}  |  "
                f"Conf: {confidence:2d}%  |  "
                f"{status}"
            )
        lines.append(
            f"  {'':12} {'':>3}  |  "
            f"(Win rates: last 10 / last 30 / all {trades} trades)"
        )
        lines.append("")

    # Calculate expected P&L trend
    if stats["resolved_trades"] > 0:
        avg_pnl_per_trade = stats["pnl"] / stats["resolved_trades"]
        daily_trades_estimate = 20  # Based on MAX_DAILY_TRADES
        expected_daily_pnl = avg_pnl_per_trade * daily_trades_estimate

        lines.extend([
            "💡 EXPECTED P&L TREND",
            "-" * 70,
            f"  Avg P&L per trade:      ${avg_pnl_per_trade:+.2f}",
            f"  Estimated daily P&L:    ${expected_daily_pnl:+.2f} (at {daily_trades_estimate} trades/day)",
            "",
        ])

    # Weekly history
    if weekly_history:
        lines.extend([
            "📅 WEEKLY PERFORMANCE HISTORY",
            "-" * 70,
        ])

        # Group by week
        weeks_data = {}
        for record in weekly_history:
            week = record["week_start"]
            if week not in weeks_data:
                weeks_data[week] = []
            weeks_data[week].append(record)

        for week in sorted(weeks_data.keys(), reverse=True)[:2]:
            lines.append(f"  Week of {week}:")
            for rec in sorted(weeks_data[week], key=lambda x: (x["city"], x["side"])):
                city_name = rec["city"].replace("_", " ").title()
                lines.append(
                    f"    {city_name:12} {rec['side']:>3}  |  "
                    f"{rec['trades_count']:2} trades  |  "
                    f"WR: {rec['win_rate']:5.1%}  |  "
                    f"Avg P&L: ${rec['avg_pnl']:+6.2f}"
                )
            lines.append("")

    # Active experiments
    if active_experiments:
        lines.extend([
            "🧪 ACTIVE EXPERIMENTS",
            "-" * 70,
        ])
        for exp in active_experiments:
            city_name = exp["city"].replace("_", " ").title()
            baseline_wr = exp["baseline_wins"] / exp["baseline_trades"] if exp["baseline_trades"] > 0 else 0.0
            experiment_wr = exp["experiment_wins"] / exp["experiment_trades"] if exp["experiment_trades"] > 0 else 0.0

            lines.append(f"  {city_name} — {exp['hypothesis']}")
            lines.append(
                f"    Baseline:   {exp['baseline_trades']:2} trades | WR: {baseline_wr:5.1%} | "
                f"Value: {exp['baseline_value']:.2f}"
            )
            lines.append(
                f"    Experiment: {exp['experiment_trades']:2} trades | WR: {experiment_wr:5.1%} | "
                f"Value: {exp['experiment_value']:.2f}"
            )
            lines.append("")

    # Recent experiment results
    if recent_experiments:
        completed = [e for e in recent_experiments if e["status"] == "completed"]
        aborted = [e for e in recent_experiments if e["status"] == "aborted"]

        if completed or aborted:
            lines.extend([
                "📊 EXPERIMENT RESULTS (last 7 days)",
                "-" * 70,
            ])

        if completed:
            lines.append("  Completed:")
            for exp in completed:
                city_name = exp["city"].replace("_", " ").title()
                winner_symbol = "✓" if exp["winner"] == "experiment" else "✗"
                lines.append(
                    f"    {winner_symbol} {city_name} — {exp['hypothesis'][:50]}"
                )
                lines.append(f"      Winner: {exp['winner']} | {exp['reason']}")
            lines.append("")

        if aborted:
            lines.append("  Aborted (safety):")
            for exp in aborted:
                city_name = exp["city"].replace("_", " ").title()
                lines.append(f"    ✗ {city_name} — {exp['hypothesis'][:50]}")
                lines.append(f"      Reason: {exp['reason']}")
            lines.append("")

    lines.append("=" * 70)

    return "\n".join(lines)


def save_report(
    db_path: str,
    output_dir: str = "reports",
    since_hours: int = 24,
) -> str:
    """Generate and save a trading report to disk.

    Args:
        db_path: Path to the SQLite database file.
        output_dir: Directory to save reports (default: "reports").
        since_hours: Hours to look back for the report.

    Returns:
        Path to the saved report file.
    """
    # Use /data/reports on Railway, local reports/ directory otherwise
    data_dir = os.getenv("DATA_DIR", "")
    if data_dir:
        output_dir = os.path.join(data_dir, "reports")

    os.makedirs(output_dir, exist_ok=True)

    report_text = generate_text_report(db_path, since_hours)
    now = datetime.now()
    filename = f"report_{now.strftime('%Y-%m-%d_%H')}.txt"
    filepath = os.path.join(output_dir, filename)

    with open(filepath, "w") as f:
        f.write(report_text)

    logger.info("Report saved: %s", filepath)
    return filepath


def print_report(db_path: str, since_hours: int = 24) -> None:
    """Generate and print a trading report to stdout.

    Args:
        db_path: Path to the SQLite database file.
        since_hours: Hours to look back for the report.
    """
    report_text = generate_text_report(db_path, since_hours)
    print(report_text)


def send_telegram_report(
    db_path: str,
    bot_token: str | None = None,
    chat_id: str | None = None,
    since_hours: int = 24,
) -> bool:
    """Send trading report to Telegram.

    Args:
        db_path: Path to the SQLite database file.
        bot_token: Telegram bot token (or None to read from env).
        chat_id: Telegram chat ID (or None to read from env).
        since_hours: Hours to look back for the report.

    Returns:
        True if sent successfully, False otherwise.
    """
    token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")

    if not token or not chat:
        logger.debug("Telegram not configured — skipping report")
        return False

    report_text = generate_text_report(db_path, since_hours)

    # Telegram message limit is 4096 chars, truncate if needed
    if len(report_text) > 4000:
        report_text = report_text[:3900] + "\n\n... (truncated)"

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat,
        "text": f"```\n{report_text}\n```",
        "parse_mode": "Markdown",
    }

    try:
        response = httpx.post(url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info("Trading report sent to Telegram")
        return True
    except httpx.HTTPError as exc:
        logger.warning("Failed to send Telegram report: %s", exc)
        return False
