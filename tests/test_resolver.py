"""Tests for src/resolver.py — outcome resolution logic."""

import json
import sqlite3
import tempfile
import os
from unittest.mock import MagicMock, patch

import pytest

from src.database import create_tables, get_accuracy_stats, get_unresolved_trades, log_trade
from src.resolver import _calculate_pnl, _fetch_resolution, resolve_pending_trades
from src.signals import Signal
from datetime import date, datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db() -> str:
    """Create a temp DB with the trades table and return its path."""
    tmp = tempfile.mktemp(suffix=".db")
    create_tables(tmp)
    return tmp


def _insert_trade(db_path: str, condition_id: str, side: str, price: float, size: float = 5.0) -> int:
    """Insert a minimal unresolved trade and return its row ID."""
    sig = Signal(
        market_id="m1",
        condition_id=condition_id,
        token_id="tok1",
        question="Will the temp be above 50°F on March 10, 2026?",
        city="new_york",
        date=date(2026, 3, 10),
        forecast_prob=0.9 if side == "YES" else 0.1,
        market_price=price,
        edge=0.75,
        confidence=0.9,
        recommended_side=side,
        recommended_size=size,
        timestamp=datetime.now(),
    )
    return log_trade(db_path, sig, order_result=None, dry_run=True)


# ---------------------------------------------------------------------------
# _calculate_pnl
# ---------------------------------------------------------------------------

class TestCalculatePnl:

    def test_yes_hit(self):
        hit, pnl = _calculate_pnl("YES", market_price=0.10, size=5.0, yes_won=True)
        assert hit is True
        assert pytest.approx(pnl, abs=0.01) == 5.0 * (1 / 0.10 - 1)  # $45

    def test_yes_miss(self):
        hit, pnl = _calculate_pnl("YES", market_price=0.10, size=5.0, yes_won=False)
        assert hit is False
        assert pnl == -5.0

    def test_no_hit(self):
        # bought NO at market_price=0.85 (YES price) → NO price = 0.15
        hit, pnl = _calculate_pnl("NO", market_price=0.85, size=5.0, yes_won=False)
        assert hit is True
        assert pytest.approx(pnl, abs=0.01) == 5.0 * (1 / 0.15 - 1)  # ~$28.33

    def test_no_miss(self):
        hit, pnl = _calculate_pnl("NO", market_price=0.85, size=5.0, yes_won=True)
        assert hit is False
        assert pnl == -5.0


# ---------------------------------------------------------------------------
# _fetch_resolution
# ---------------------------------------------------------------------------

class TestFetchResolution:

    @patch("src.resolver.httpx.Client")
    def test_yes_won(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"resolved": True, "outcomePrices": json.dumps(["1", "0"])}
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_resp

        resolved, yes_won = _fetch_resolution("0xabc")
        assert resolved is True
        assert yes_won is True

    @patch("src.resolver.httpx.Client")
    def test_no_won(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"resolved": True, "outcomePrices": json.dumps(["0", "1"])}
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_resp

        resolved, yes_won = _fetch_resolution("0xabc")
        assert resolved is True
        assert yes_won is False

    @patch("src.resolver.httpx.Client")
    def test_not_resolved(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"resolved": False, "outcomePrices": json.dumps(["0.7", "0.3"])}
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_resp

        resolved, yes_won = _fetch_resolution("0xabc")
        assert resolved is False
        assert yes_won is None

    @patch("src.resolver.httpx.Client")
    def test_market_not_found_returns_false(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()
        mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_resp

        resolved, yes_won = _fetch_resolution("0xnonexistent")
        assert resolved is False
        assert yes_won is None


# ---------------------------------------------------------------------------
# resolve_pending_trades (integration with DB)
# ---------------------------------------------------------------------------

class TestResolvePendingTrades:

    @patch("src.resolver._fetch_resolution")
    def test_resolves_yes_hit(self, mock_fetch):
        db = _make_db()
        _insert_trade(db, "0xcond1", side="YES", price=0.10)
        mock_fetch.return_value = (True, True)  # YES won

        count = resolve_pending_trades(db)

        assert count == 1
        stats = get_accuracy_stats(db)
        assert stats["total_resolved"] == 1
        assert stats["hits"] == 1
        assert stats["misses"] == 0
        assert stats["hit_rate"] == 1.0
        assert stats["total_pnl"] == pytest.approx(5.0 * (1 / 0.10 - 1), abs=0.01)

    @patch("src.resolver._fetch_resolution")
    def test_resolves_no_miss(self, mock_fetch):
        db = _make_db()
        _insert_trade(db, "0xcond2", side="NO", price=0.85)
        mock_fetch.return_value = (True, True)  # YES won → NO missed

        count = resolve_pending_trades(db)

        assert count == 1
        stats = get_accuracy_stats(db)
        assert stats["hits"] == 0
        assert stats["misses"] == 1
        assert stats["total_pnl"] == -5.0

    @patch("src.resolver._fetch_resolution")
    def test_unresolved_market_skipped(self, mock_fetch):
        db = _make_db()
        _insert_trade(db, "0xcond3", side="YES", price=0.10)
        mock_fetch.return_value = (False, None)

        count = resolve_pending_trades(db)

        assert count == 0
        assert get_unresolved_trades(db) != []

    @patch("src.resolver._fetch_resolution")
    def test_empty_db_returns_zero(self, mock_fetch):
        db = _make_db()
        count = resolve_pending_trades(db)
        assert count == 0
        mock_fetch.assert_not_called()

    @patch("src.resolver._fetch_resolution")
    def test_accuracy_across_multiple_trades(self, mock_fetch):
        db = _make_db()
        _insert_trade(db, "0xa", side="YES", price=0.10)
        _insert_trade(db, "0xb", side="NO",  price=0.80)
        _insert_trade(db, "0xc", side="YES", price=0.20)

        # Trade a: YES hits, Trade b: NO hits (YES lost), Trade c: YES misses
        mock_fetch.side_effect = [
            (True, True),   # 0xa YES won → YES hit
            (True, False),  # 0xb YES lost → NO hit
            (True, True),   # 0xc YES won → YES hit
        ]

        resolve_pending_trades(db)

        stats = get_accuracy_stats(db)
        assert stats["total_resolved"] == 3
        assert stats["hits"] == 3
        assert stats["hit_rate"] == 1.0
