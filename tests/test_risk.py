"""Tests for src/risk.py — position sizing and daily risk limits."""

import sqlite3
import tempfile
from datetime import date
from pathlib import Path

import pytest

from src.risk import check_daily_limits, get_current_exposure, kelly_size


# ---------------------------------------------------------------------------
# kelly_size
# ---------------------------------------------------------------------------

class TestKellySize:
    def test_strong_edge_capped_at_max(self):
        """With a huge edge the result must never exceed MAX_POSITION_USD."""
        from config import MAX_POSITION_USD
        size = kelly_size(forecast_prob=0.90, market_price=0.15)
        assert size == MAX_POSITION_USD

    def test_no_edge_returns_zero(self):
        """When forecast_prob equals market_price there is no edge."""
        size = kelly_size(forecast_prob=0.50, market_price=0.50)
        assert size == pytest.approx(0.0, abs=1e-6)

    def test_negative_kelly_returns_zero(self):
        """If Kelly fraction is negative (bad bet) return 0."""
        # forecast says 10% but market prices it at 80% — buying YES is terrible
        size = kelly_size(forecast_prob=0.10, market_price=0.80)
        assert size == 0.0

    def test_boundary_market_price_zero(self):
        assert kelly_size(forecast_prob=0.90, market_price=0.0) == 0.0

    def test_boundary_market_price_one(self):
        assert kelly_size(forecast_prob=0.90, market_price=1.0) == 0.0

    def test_moderate_edge_uses_kelly_formula(self):
        """Hand-verify a known result with explicit numbers."""
        # p=0.60, market=0.50  =>  b=1.0, kelly_f=(0.6*1 - 0.4)/1 = 0.20
        # size = 0.20 * 0.15 * 100 = 3.00
        size = kelly_size(
            forecast_prob=0.60,
            market_price=0.50,
            bankroll=100.0,
            kelly_fraction=0.15,
        )
        assert size == pytest.approx(3.00, rel=1e-3)

    def test_custom_bankroll_and_fraction(self):
        """Scaling bankroll doubles the result."""
        s1 = kelly_size(0.60, 0.50, bankroll=100.0, kelly_fraction=0.15)
        s2 = kelly_size(0.60, 0.50, bankroll=200.0, kelly_fraction=0.15)
        assert s2 == pytest.approx(min(s1 * 2, 5.0), rel=1e-3)


# ---------------------------------------------------------------------------
# Helpers for DB-backed tests
# ---------------------------------------------------------------------------

def _make_db(path: str, trades: list[dict]) -> None:
    """Create a minimal trades table and insert rows."""
    con = sqlite3.connect(path)
    con.execute(
        """
        CREATE TABLE trades (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            size      REAL NOT NULL DEFAULT 0,
            pnl       REAL NOT NULL DEFAULT 0,
            resolved  INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    for t in trades:
        con.execute(
            "INSERT INTO trades (trade_date, size, pnl, resolved) VALUES (?, ?, ?, ?)",
            (t["trade_date"], t["size"], t["pnl"], t["resolved"]),
        )
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# check_daily_limits
# ---------------------------------------------------------------------------

class TestCheckDailyLimits:
    def test_empty_db_allows_trading(self, tmp_path):
        db = str(tmp_path / "trades.db")
        _make_db(db, [])
        assert check_daily_limits(db) is True

    def test_missing_table_allows_trading(self, tmp_path):
        """Fresh DB with no table should not crash — treat as zero trades."""
        db = str(tmp_path / "empty.db")
        sqlite3.connect(db).close()  # create empty file
        assert check_daily_limits(db) is True

    def test_below_trade_limit_allows_trading(self, tmp_path):
        from config import MAX_DAILY_TRADES
        db = str(tmp_path / "trades.db")
        today = date.today().isoformat()
        trades = [
            {"trade_date": today, "size": 2.0, "pnl": 0.0, "resolved": 1}
            for _ in range(MAX_DAILY_TRADES - 1)
        ]
        _make_db(db, trades)
        assert check_daily_limits(db) is True

    def test_at_trade_limit_blocks_trading(self, tmp_path):
        from config import MAX_DAILY_TRADES
        db = str(tmp_path / "trades.db")
        today = date.today().isoformat()
        trades = [
            {"trade_date": today, "size": 2.0, "pnl": 0.0, "resolved": 1}
            for _ in range(MAX_DAILY_TRADES)
        ]
        _make_db(db, trades)
        assert check_daily_limits(db) is False

    def test_yesterday_trades_dont_count(self, tmp_path):
        from config import MAX_DAILY_TRADES
        db = str(tmp_path / "trades.db")
        trades = [
            {"trade_date": "2000-01-01", "size": 2.0, "pnl": 0.0, "resolved": 1}
            for _ in range(MAX_DAILY_TRADES + 5)
        ]
        _make_db(db, trades)
        assert check_daily_limits(db) is True

    def test_daily_loss_limit_blocks_trading(self, tmp_path):
        from config import BANKROLL, MAX_DAILY_LOSS_PCT
        db = str(tmp_path / "trades.db")
        today = date.today().isoformat()
        big_loss = -(MAX_DAILY_LOSS_PCT * BANKROLL + 1)
        _make_db(
            db,
            [{"trade_date": today, "size": 5.0, "pnl": big_loss, "resolved": 1}],
        )
        assert check_daily_limits(db) is False

    def test_within_loss_limit_allows_trading(self, tmp_path):
        from config import BANKROLL, MAX_DAILY_LOSS_PCT
        db = str(tmp_path / "trades.db")
        today = date.today().isoformat()
        small_loss = -(MAX_DAILY_LOSS_PCT * BANKROLL * 0.5)
        _make_db(
            db,
            [{"trade_date": today, "size": 5.0, "pnl": small_loss, "resolved": 1}],
        )
        assert check_daily_limits(db) is True


# ---------------------------------------------------------------------------
# get_current_exposure
# ---------------------------------------------------------------------------

class TestGetCurrentExposure:
    def test_no_open_positions(self, tmp_path):
        db = str(tmp_path / "trades.db")
        today = date.today().isoformat()
        _make_db(
            db,
            [{"trade_date": today, "size": 3.0, "pnl": 0.5, "resolved": 1}],
        )
        assert get_current_exposure(db) == pytest.approx(0.0)

    def test_sums_open_positions(self, tmp_path):
        db = str(tmp_path / "trades.db")
        today = date.today().isoformat()
        _make_db(
            db,
            [
                {"trade_date": today, "size": 3.0, "pnl": 0.0, "resolved": 0},
                {"trade_date": today, "size": 2.5, "pnl": 0.0, "resolved": 0},
                {"trade_date": today, "size": 4.0, "pnl": 1.0, "resolved": 1},
            ],
        )
        assert get_current_exposure(db) == pytest.approx(5.5)

    def test_missing_table_returns_zero(self, tmp_path):
        db = str(tmp_path / "empty.db")
        sqlite3.connect(db).close()
        assert get_current_exposure(db) == 0.0
