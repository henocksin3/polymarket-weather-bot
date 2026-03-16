"""Two-way Telegram communication handler with command support."""

import logging
import os
import time
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class TelegramCommandHandler:
    """Handler for Telegram bot commands."""

    def __init__(self, bot_token: str, allowed_chat_id: str, db_path: str):
        """Initialize Telegram command handler.

        Args:
            bot_token: Telegram bot token.
            allowed_chat_id: Chat ID that is allowed to send commands.
            db_path: Path to SQLite database.
        """
        self.bot_token = bot_token
        self.allowed_chat_id = allowed_chat_id
        self.db_path = db_path
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.last_update_id = 0

    def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        """Send a message to the authorized chat.

        Args:
            text: Message text to send.
            parse_mode: Telegram parse mode (Markdown or HTML).

        Returns:
            True if sent successfully, False otherwise.
        """
        try:
            url = f"{self.base_url}/sendMessage"
            payload = {
                "chat_id": self.allowed_chat_id,
                "text": text,
                "parse_mode": parse_mode,
            }

            with httpx.Client(timeout=10) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                return True

        except Exception as exc:
            logger.warning("Failed to send Telegram message: %s", exc)
            return False

    def get_updates(self) -> list[dict[str, Any]]:
        """Poll for new messages from Telegram.

        Returns:
            List of update objects.
        """
        try:
            url = f"{self.base_url}/getUpdates"
            params = {
                "offset": self.last_update_id + 1,
                "timeout": 5,
            }

            with httpx.Client(timeout=10) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                data = response.json()

                if not data.get("ok"):
                    return []

                updates = data.get("result", [])

                # Update offset to mark messages as read
                if updates:
                    self.last_update_id = max(u["update_id"] for u in updates)

                return updates

        except Exception as exc:
            logger.debug("Failed to get Telegram updates: %s", exc)
            return []

    def handle_command(self, command: str, args: list[str]) -> str:
        """Handle a bot command.

        Args:
            command: Command name (without /).
            args: List of command arguments.

        Returns:
            Response text to send back.
        """
        if command == "status":
            return self._handle_status()
        elif command == "rapport":
            return self._handle_rapport()
        elif command == "eksperimenter":
            return self._handle_eksperimenter()
        elif command == "kalibrering":
            return self._handle_kalibrering()
        elif command == "stopp":
            return self._handle_stopp(args)
        elif command == "start":
            return self._handle_start(args)
        elif command == "hjelp":
            return self._handle_hjelp()
        else:
            return f"Ukjent kommando: /{command}\n\nBruk /hjelp for å se tilgjengelige kommandoer."

    def _handle_status(self) -> str:
        """Handle /status command.

        Returns:
            Quick status summary.
        """
        try:
            from database import get_accuracy_stats
            from experiments import get_active_experiments
            from learner import get_learning_params

            import sqlite3

            # Get overall stats
            stats = get_accuracy_stats(self.db_path)

            # Get active experiments
            active_experiments = get_active_experiments(self.db_path)

            # Get learning params
            con = sqlite3.connect(self.db_path)
            try:
                con.row_factory = sqlite3.Row
                cur = con.execute(
                    """
                    SELECT city, side, win_rate_all, total_trades, active
                    FROM learning_params
                    ORDER BY city, side
                    """
                )
                params = [dict(row) for row in cur.fetchall()]
            finally:
                con.close()

            lines = [
                "🤖 *BOT STATUS*",
                "─" * 30,
                "",
                f"*All-Time Performance:*",
                f"Total resolved: {stats['total_resolved']}",
                f"Wins: {stats['hits']} ({stats['hit_rate']:.1%})",
                f"P&L: ${stats['total_pnl']:+.2f}",
                "",
            ]

            if params:
                lines.append("*Strategies:*")
                for p in params[:5]:  # Show first 5
                    status = "✓" if p["active"] == 1 else "✗"
                    city_name = p["city"].replace("_", " ").title()
                    lines.append(
                        f"{status} {city_name} {p['side']}: {p['win_rate_all']:.0%} "
                        f"({p['total_trades']} trades)"
                    )
                if len(params) > 5:
                    lines.append(f"... and {len(params) - 5} more")
                lines.append("")

            if active_experiments:
                lines.append(f"*Active Experiments:* {len(active_experiments)}")
            else:
                lines.append("*Active Experiments:* None")

            lines.extend([
                "",
                "_Use /rapport for full report_",
                "_Use /eksperimenter for experiment details_",
            ])

            return "\n".join(lines)

        except Exception as exc:
            logger.exception("Error handling /status: %s", exc)
            return f"❌ Error getting status: {exc}"

    def _handle_rapport(self) -> str:
        """Handle /rapport command.

        Returns:
            Full trading report.
        """
        try:
            from reporter import generate_text_report

            report = generate_text_report(self.db_path, since_hours=24)

            # Telegram has 4096 char limit
            if len(report) > 4000:
                return "```\n" + report[:3900] + "\n\n... (truncated)\n```"
            else:
                return "```\n" + report + "\n```"

        except Exception as exc:
            logger.exception("Error handling /rapport: %s", exc)
            return f"❌ Error generating report: {exc}"

    def _handle_eksperimenter(self) -> str:
        """Handle /eksperimenter command.

        Returns:
            Active experiments and recent results.
        """
        try:
            from experiments import get_active_experiments, get_recent_experiments

            active = get_active_experiments(self.db_path)
            recent = get_recent_experiments(self.db_path, days=7)

            lines = [
                "🧪 *EXPERIMENTS*",
                "─" * 30,
                "",
            ]

            if active:
                lines.append("*Active A/B Tests:*")
                for exp in active:
                    city_name = exp["city"].replace("_", " ").title()
                    baseline_wr = (
                        exp["baseline_wins"] / exp["baseline_trades"]
                        if exp["baseline_trades"] > 0
                        else 0.0
                    )
                    experiment_wr = (
                        exp["experiment_wins"] / exp["experiment_trades"]
                        if exp["experiment_trades"] > 0
                        else 0.0
                    )

                    lines.append(f"\n📍 *{city_name}*")
                    lines.append(f"_{exp['hypothesis']}_")
                    lines.append(
                        f"Baseline: {exp['baseline_trades']} trades, "
                        f"WR: {baseline_wr:.1%}"
                    )
                    lines.append(
                        f"Experiment: {exp['experiment_trades']} trades, "
                        f"WR: {experiment_wr:.1%}"
                    )

                    # Show who's winning
                    if exp["baseline_trades"] >= 5 and exp["experiment_trades"] >= 5:
                        if experiment_wr > baseline_wr:
                            lines.append("→ Experiment leading! ✓")
                        else:
                            lines.append("→ Baseline leading")

                lines.append("")
            else:
                lines.append("*Active A/B Tests:* None")
                lines.append("")

            # Recent results
            completed = [e for e in recent if e["status"] == "completed"]
            aborted = [e for e in recent if e["status"] == "aborted"]

            if completed:
                lines.append("*Recent Completions (7 days):*")
                for exp in completed[:3]:  # Show first 3
                    city_name = exp["city"].replace("_", " ").title()
                    winner_symbol = "✓" if exp["winner"] == "experiment" else "✗"
                    lines.append(f"{winner_symbol} {city_name}: {exp['winner']}")

            if aborted:
                lines.append("")
                lines.append("*Aborted (Safety):*")
                for exp in aborted[:3]:
                    city_name = exp["city"].replace("_", " ").title()
                    lines.append(f"⚠️ {city_name}: {exp['reason'][:50]}")

            return "\n".join(lines)

        except Exception as exc:
            logger.exception("Error handling /eksperimenter: %s", exc)
            return f"❌ Error getting experiments: {exc}"

    def _handle_kalibrering(self) -> str:
        """Handle /kalibrering command.

        Returns:
            Calibration stats for all cities.
        """
        try:
            from experiments import get_calibration_stats
            import config

            lines = [
                "🎯 *FORECAST CALIBRATION*",
                "─" * 30,
                "",
            ]

            for city_key in config.CITIES.keys():
                stats = get_calibration_stats(self.db_path, city_key, days=30)

                city_name = city_key.replace("_", " ").title()
                lines.append(f"*{city_name}*")

                if stats["sample_size"] >= 5:
                    lines.append(f"Bias: {stats['bias']:+.2f}°F")
                    lines.append(f"Std Dev: {stats['std_dev']:.2f}°F")
                    lines.append(f"Samples: {stats['sample_size']}")
                    lines.append(f"Confidence: {stats['confidence']:.0%}")

                    # Interpretation
                    if abs(stats["bias"]) > 1.0:
                        direction = "overpredicts" if stats["bias"] > 0 else "underpredicts"
                        lines.append(f"→ GFS {direction} by {abs(stats['bias']):.1f}°F")
                    else:
                        lines.append("→ GFS bias minimal")
                else:
                    lines.append(f"Insufficient data ({stats['sample_size']} samples)")
                    lines.append("_Need 5+ samples for calibration_")

                lines.append("")

            return "\n".join(lines)

        except Exception as exc:
            logger.exception("Error handling /kalibrering: %s", exc)
            return f"❌ Error getting calibration: {exc}"

    def _handle_stopp(self, args: list[str]) -> str:
        """Handle /stopp command to deactivate a strategy.

        Args:
            args: [city, side]

        Returns:
            Confirmation message.
        """
        if len(args) < 2:
            return "❌ Usage: /stopp [city] [side]\n\nExample: `/stopp chicago YES`"

        try:
            import sqlite3

            city = args[0].lower().replace(" ", "_")
            side = args[1].upper()

            if side not in ["YES", "NO"]:
                return "❌ Side must be YES or NO"

            con = sqlite3.connect(self.db_path)
            try:
                # Check if params exist
                cur = con.execute(
                    "SELECT * FROM learning_params WHERE city = ? AND side = ?",
                    (city, side),
                )
                existing = cur.fetchone()

                if not existing:
                    return f"❌ No learning params found for {city} {side}"

                # Deactivate
                con.execute(
                    "UPDATE learning_params SET active = 0 WHERE city = ? AND side = ?",
                    (city, side),
                )
                con.commit()

                city_name = city.replace("_", " ").title()
                return f"✓ Deactivated {city_name} {side}\n\nStrategy will not place new trades."

            finally:
                con.close()

        except Exception as exc:
            logger.exception("Error handling /stopp: %s", exc)
            return f"❌ Error deactivating strategy: {exc}"

    def _handle_start(self, args: list[str]) -> str:
        """Handle /start command to reactivate a strategy.

        Args:
            args: [city, side]

        Returns:
            Confirmation message.
        """
        if len(args) < 2:
            return "❌ Usage: /start [city] [side]\n\nExample: `/start chicago YES`"

        try:
            import sqlite3

            city = args[0].lower().replace(" ", "_")
            side = args[1].upper()

            if side not in ["YES", "NO"]:
                return "❌ Side must be YES or NO"

            con = sqlite3.connect(self.db_path)
            try:
                # Check if params exist
                cur = con.execute(
                    "SELECT * FROM learning_params WHERE city = ? AND side = ?",
                    (city, side),
                )
                existing = cur.fetchone()

                if not existing:
                    return f"❌ No learning params found for {city} {side}"

                # Reactivate
                con.execute(
                    "UPDATE learning_params SET active = 1 WHERE city = ? AND side = ?",
                    (city, side),
                )
                con.commit()

                city_name = city.replace("_", " ").title()
                return f"✓ Reactivated {city_name} {side}\n\nStrategy will resume placing trades."

            finally:
                con.close()

        except Exception as exc:
            logger.exception("Error handling /start: %s", exc)
            return f"❌ Error reactivating strategy: {exc}"

    def _handle_hjelp(self) -> str:
        """Handle /hjelp command.

        Returns:
            Help text with all available commands.
        """
        return """
🤖 *TILGJENGELIGE KOMMANDOER*
─────────────────────────────────

*Status & Rapporter:*
/status - Nåværende win rate, P&L, aktive eksperimenter
/rapport - Full trading-rapport (siste 24 timer)

*Eksperimenter & Kalibrering:*
/eksperimenter - Vis alle aktive A/B-tester
/kalibrering - Vis GFS bias per by

*Strategi-kontroll:*
/stopp [by] [side] - Deaktiver strategi
  _Eksempel: /stopp chicago YES_
/start [by] [side] - Reaktiver strategi
  _Eksempel: /start miami NO_

*Hjelp:*
/hjelp - Vis denne meldingen

─────────────────────────────────
_Polymarket Weather Bot v2.0_
_Self-experimenting AI trader_ 🧪
"""

    def process_updates(self, updates: list[dict[str, Any]]) -> None:
        """Process incoming Telegram updates.

        Args:
            updates: List of update objects from getUpdates.
        """
        for update in updates:
            message = update.get("message")
            if not message:
                continue

            # Security: only process messages from allowed chat
            chat_id = str(message.get("chat", {}).get("id", ""))
            if chat_id != self.allowed_chat_id:
                logger.warning(
                    "Ignoring message from unauthorized chat: %s", chat_id
                )
                continue

            # Extract text
            text = message.get("text", "").strip()
            if not text:
                continue

            logger.info("Received Telegram message: %s", text)

            # Parse command
            if text.startswith("/"):
                parts = text[1:].split()
                command = parts[0].lower()
                args = parts[1:]

                # Handle command
                response = self.handle_command(command, args)
                self.send_message(response)

    def run_polling_loop(self, stop_flag: callable) -> None:
        """Run continuous polling loop.

        Args:
            stop_flag: Callable that returns True when bot should stop.
        """
        logger.info("Starting Telegram polling loop...")

        while not stop_flag():
            try:
                # Get updates
                updates = self.get_updates()

                # Process each update
                if updates:
                    self.process_updates(updates)

                # Sleep before next poll
                time.sleep(10)

            except Exception as exc:
                logger.exception("Error in Telegram polling loop: %s", exc)
                time.sleep(30)  # Longer sleep on error

        logger.info("Telegram polling loop stopped")


def create_handler(db_path: str) -> TelegramCommandHandler | None:
    """Create a Telegram command handler if credentials are configured.

    Args:
        db_path: Path to SQLite database.

    Returns:
        TelegramCommandHandler instance or None if not configured.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        logger.info("Telegram not configured - command handler disabled")
        return None

    return TelegramCommandHandler(token, chat_id, db_path)
