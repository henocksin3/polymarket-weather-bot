"""Execute trades on Polymarket via the CLOB API using py-clob-client."""

import logging
from typing import Any

from config import CLOB_API_BASE

logger = logging.getLogger(__name__)


def initialize_client(
    api_key: str,
    api_secret: str,
    api_passphrase: str,
    private_key: str = "",
    chain_id: int = 137,
) -> Any:
    """Create and return a ClobClient instance.

    Args:
        api_key:        Polymarket API key.
        api_secret:     Polymarket API secret.
        api_passphrase: Polymarket API passphrase.
        private_key:    Ethereum private key (used for L1/L2 auth).
        chain_id:       Chain ID (137 = Polygon mainnet).

    Returns:
        Authenticated ClobClient instance.
    """
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds

    creds = ApiCreds(
        api_key=api_key,
        api_secret=api_secret,
        api_passphrase=api_passphrase,
    )
    client = ClobClient(
        host=CLOB_API_BASE,
        chain_id=chain_id,
        key=private_key if private_key else None,
        creds=creds,
    )
    logger.info("ClobClient initialised (host=%s, chain=%s)", CLOB_API_BASE, chain_id)
    return client


def place_order(
    client: Any,
    token_id: str,
    side: str,
    size: float,
    price: float,
) -> dict[str, Any]:
    """Place a limit order on the CLOB.

    Args:
        client:   Authenticated ClobClient.
        token_id: Polymarket token ID (outcome token).
        side:     "BUY" or "SELL".
        size:     Number of shares (USDC notional ≈ size * price).
        price:    Limit price (0–1).

    Returns:
        Order response dict from the CLOB API.

    Raises:
        RuntimeError: If the API call fails.
    """
    from py_clob_client.clob_types import OrderArgs
    from py_clob_client.order_builder.constants import BUY

    clob_side = BUY if side.upper() == "BUY" else "SELL"

    order_args = OrderArgs(
        token_id=token_id,
        price=round(price, 4),
        size=round(size, 2),
        side=clob_side,
    )

    logger.info(
        "Placing order: token=%s side=%s size=%.2f price=%.4f",
        token_id,
        side,
        size,
        price,
    )

    try:
        signed_order = client.create_order(order_args)
        response = client.post_order(signed_order)
        logger.info("Order placed: %s", response)
        return response if isinstance(response, dict) else {"raw": response}
    except Exception as exc:
        logger.error("Order failed: %s", exc)
        raise RuntimeError(f"place_order failed: {exc}") from exc


def get_open_positions(client: Any) -> list[dict[str, Any]]:
    """Retrieve open positions from the CLOB.

    Args:
        client: Authenticated ClobClient.

    Returns:
        List of open position dicts.
    """
    try:
        positions = client.get_positions()
        if isinstance(positions, list):
            return positions
        # Some SDK versions wrap the result
        if isinstance(positions, dict) and "data" in positions:
            return positions["data"]
        return []
    except Exception as exc:
        logger.error("Failed to fetch positions: %s", exc)
        return []
