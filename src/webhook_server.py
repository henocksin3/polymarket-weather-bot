"""Flask webhook server for Telegram bot commands."""

import logging
import os
from typing import Any

import httpx
from flask import Flask, request, jsonify

from src.telegram_handler import TelegramCommandHandler

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Create Flask app
app = Flask(__name__)

# Initialize Telegram handler
DB_PATH = os.getenv("DB_PATH", "db/trades.db")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")

telegram_handler = None
if BOT_TOKEN and CHAT_ID:
    telegram_handler = TelegramCommandHandler(BOT_TOKEN, CHAT_ID, DB_PATH)
    logger.info("Telegram handler initialized")
else:
    logger.warning("Telegram not configured - webhook server will not process commands")


@app.route("/")
def index():
    """Health check endpoint."""
    return jsonify({
        "status": "ok",
        "service": "polymarket-weather-bot-webhook",
        "telegram_configured": telegram_handler is not None,
    })


@app.route("/health")
def health():
    """Health check for Railway."""
    return jsonify({"status": "healthy"})


@app.route("/webhook", methods=["POST"])
def webhook():
    """Handle incoming Telegram webhook messages.

    Returns:
        JSON response with status.
    """
    if not telegram_handler:
        logger.warning("Telegram not configured - ignoring webhook")
        return jsonify({"status": "not_configured"}), 200

    try:
        # Get update from Telegram
        update = request.get_json()

        if not update:
            logger.warning("Received empty webhook update")
            return jsonify({"status": "empty"}), 200

        logger.info("Received webhook update: %s", update.get("update_id"))

        # Extract message
        message = update.get("message")
        if not message:
            logger.debug("No message in update")
            return jsonify({"status": "no_message"}), 200

        # Security: check authorized chat
        chat_id = str(message.get("chat", {}).get("id", ""))
        if chat_id != telegram_handler.allowed_chat_id:
            logger.warning(
                "Unauthorized webhook from chat %s (expected %s)",
                chat_id,
                telegram_handler.allowed_chat_id,
            )
            return jsonify({"status": "unauthorized"}), 200

        # Extract text
        text = message.get("text", "").strip()
        if not text:
            logger.debug("Empty message text")
            return jsonify({"status": "empty_text"}), 200

        logger.info("Processing command: %s", text)

        # Parse command
        if text.startswith("/"):
            parts = text[1:].split()
            command = parts[0].lower()
            args = parts[1:]

            # Handle command
            response = telegram_handler.handle_command(command, args)

            # Send response
            telegram_handler.send_message(response)

            logger.info("Command processed: %s", command)
            return jsonify({"status": "ok", "command": command}), 200

        else:
            logger.debug("Not a command (no /): %s", text)
            return jsonify({"status": "not_command"}), 200

    except Exception as exc:
        logger.exception("Error processing webhook: %s", exc)
        return jsonify({"status": "error", "error": str(exc)}), 500


def register_webhook(bot_token: str, webhook_url: str) -> bool:
    """Register webhook with Telegram API.

    Args:
        bot_token: Telegram bot token.
        webhook_url: Full webhook URL (https://domain/webhook).

    Returns:
        True if registered successfully, False otherwise.
    """
    try:
        url = f"https://api.telegram.org/bot{bot_token}/setWebhook"
        payload = {
            "url": webhook_url,
            "allowed_updates": ["message"],
        }

        logger.info("Registering webhook: %s", webhook_url)

        with httpx.Client(timeout=10) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

            if data.get("ok"):
                logger.info("Webhook registered successfully")
                return True
            else:
                logger.error("Failed to register webhook: %s", data)
                return False

    except Exception as exc:
        logger.exception("Error registering webhook: %s", exc)
        return False


def get_webhook_info(bot_token: str) -> dict[str, Any]:
    """Get current webhook info from Telegram.

    Args:
        bot_token: Telegram bot token.

    Returns:
        Webhook info dict.
    """
    try:
        url = f"https://api.telegram.org/bot{bot_token}/getWebhookInfo"

        with httpx.Client(timeout=10) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()

            if data.get("ok"):
                return data.get("result", {})
            else:
                return {}

    except Exception as exc:
        logger.exception("Error getting webhook info: %s", exc)
        return {}


def delete_webhook(bot_token: str) -> bool:
    """Delete webhook (useful for switching back to polling).

    Args:
        bot_token: Telegram bot token.

    Returns:
        True if deleted successfully.
    """
    try:
        url = f"https://api.telegram.org/bot{bot_token}/deleteWebhook"

        with httpx.Client(timeout=10) as client:
            response = client.post(url)
            response.raise_for_status()
            data = response.json()

            if data.get("ok"):
                logger.info("Webhook deleted successfully")
                return True
            else:
                logger.error("Failed to delete webhook: %s", data)
                return False

    except Exception as exc:
        logger.exception("Error deleting webhook: %s", exc)
        return False


def main():
    """Run the Flask webhook server."""
    # Initialize database tables
    try:
        from src.database import create_tables
        from src.experiments import create_experiment_tables
        from src.learner import create_learning_tables

        logger.info("Initializing database tables...")
        create_tables(DB_PATH)
        create_learning_tables(DB_PATH)
        create_experiment_tables(DB_PATH)
        logger.info("Database tables initialized")
    except Exception as exc:
        logger.exception("Error initializing database: %s", exc)

    # Register webhook if configured
    if BOT_TOKEN and WEBHOOK_URL:
        # First, check current webhook
        info = get_webhook_info(BOT_TOKEN)
        current_url = info.get("url", "")

        if current_url != WEBHOOK_URL:
            logger.info(
                "Current webhook URL (%s) differs from desired (%s) - registering new webhook",
                current_url,
                WEBHOOK_URL,
            )
            register_webhook(BOT_TOKEN, WEBHOOK_URL)
        else:
            logger.info("Webhook already registered: %s", current_url)

        # Log webhook info
        logger.info("Webhook info: %s", info)
    else:
        logger.warning("WEBHOOK_URL or BOT_TOKEN not set - webhook not registered")

    # Get port from environment (Railway sets PORT)
    port = int(os.getenv("PORT", 8080))

    # Run Flask server
    logger.info("Starting Flask webhook server on port %d", port)
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
