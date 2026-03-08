"""Send trade alerts and daily summaries via Telegram."""

import logging
from typing import Any

import httpx

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _send(message: str) -> bool:
    """Send a message to the configured Telegram chat.

    Returns True on success, False on failure.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured — skipping alert")
        return False

    url = _TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }
    try:
        resp = httpx.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        logger.error("Telegram send failed: %s", exc)
        return False


def send_telegram_alert(signal: Any, trade_result: dict[str, Any] | None) -> bool:
    """Send a Telegram alert for a trade signal.

    Args:
        signal:       Signal dataclass (from src/signals.py).
        trade_result: Dict returned by place_order, or None for dry-run.

    Returns:
        True if the message was sent successfully.
    """
    mode = "DRY RUN" if trade_result is None else "LIVE"
    order_id = (trade_result or {}).get("orderID", trade_result or {}.get("id", "—"))

    lines = [
        f"<b>🤖 Weather Bot [{mode}]</b>",
        "",
        f"<b>Market:</b> {signal.question[:80]}",
        f"<b>City:</b> {signal.city}",
        f"<b>Date:</b> {signal.date}",
        "",
        f"<b>Side:</b> {signal.recommended_side}",
        f"<b>Size:</b> ${signal.recommended_size:.2f} USDC",
        f"<b>Price:</b> {signal.market_price:.1%}",
        "",
        f"<b>Forecast prob:</b> {signal.forecast_prob:.1%}",
        f"<b>Edge:</b> {signal.edge:+.1%}",
        f"<b>Confidence:</b> {signal.confidence:.1%}",
    ]

    if trade_result is not None and order_id != "—":
        lines.append(f"<b>Order ID:</b> {order_id}")

    message = "\n".join(lines)
    logger.info("Sending Telegram alert for signal: %s", signal.question[:60])
    return _send(message)


def send_daily_summary(stats: dict[str, Any]) -> bool:
    """Send a daily performance summary to Telegram.

    Args:
        stats: Dict with keys:
            - trades_today (int)
            - pnl_today (float)
            - open_positions (int)
            - total_exposure (float)
            - wins (int)
            - losses (int)

    Returns:
        True if the message was sent successfully.
    """
    trades = stats.get("trades_today", 0)
    pnl = stats.get("pnl_today", 0.0)
    open_pos = stats.get("open_positions", 0)
    exposure = stats.get("total_exposure", 0.0)
    wins = stats.get("wins", 0)
    losses = stats.get("losses", 0)

    pnl_emoji = "🟢" if pnl >= 0 else "🔴"

    lines = [
        "<b>📊 Daily Summary</b>",
        "",
        f"<b>Trades today:</b> {trades} ({wins}W / {losses}L)",
        f"<b>P&amp;L today:</b> {pnl_emoji} ${pnl:+.2f}",
        f"<b>Open positions:</b> {open_pos}",
        f"<b>Total exposure:</b> ${exposure:.2f} USDC",
    ]

    message = "\n".join(lines)
    logger.info("Sending daily summary: trades=%d pnl=%.2f", trades, pnl)
    return _send(message)
