"""Tests for trader, alerts, and database modules (no real API calls)."""

import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class _FakeSignal:
    market_id: str = "mkt-001"
    condition_id: str = "cond-001"
    token_id: str = "tok-001"
    question: str = "Will NYC high temp be 60–65°F on March 10?"
    city: str = "new_york"
    date: date = date(2026, 3, 10)
    forecast_prob: float = 0.85
    market_price: float = 0.20
    edge: float = 0.65
    confidence: float = 0.90
    recommended_side: str = "YES"
    recommended_size: float = 3.50
    timestamp: datetime = datetime(2026, 3, 8, 12, 0, 0)


# ---------------------------------------------------------------------------
# database tests
# ---------------------------------------------------------------------------

class TestDatabase:
    def test_create_tables_creates_db(self, tmp_path):
        from src.database import create_tables

        db = str(tmp_path / "db" / "trades.db")
        create_tables(db)
        assert os.path.exists(db)

        con = sqlite3.connect(db)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        con.close()
        assert "trades" in tables

    def test_log_trade_dry_run(self, tmp_path):
        from src.database import create_tables, log_trade

        db = str(tmp_path / "db" / "trades.db")
        create_tables(db)

        signal = _FakeSignal()
        row_id = log_trade(db, signal, order_result=None, dry_run=True)
        assert isinstance(row_id, int)
        assert row_id >= 1

        con = sqlite3.connect(db)
        row = con.execute("SELECT * FROM trades WHERE id = ?", (row_id,)).fetchone()
        con.close()
        assert row is not None
        # dry_run column is index 14 (after order_id at 13)
        assert row[14] == 1  # dry_run flag set

    def test_log_trade_with_order_result(self, tmp_path):
        from src.database import create_tables, log_trade

        db = str(tmp_path / "db" / "trades.db")
        create_tables(db)

        signal = _FakeSignal()
        order_result = {"orderID": "abc-123", "status": "matched"}
        row_id = log_trade(db, signal, order_result=order_result, dry_run=False)

        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        row = dict(con.execute("SELECT * FROM trades WHERE id = ?", (row_id,)).fetchone())
        con.close()

        assert row["order_id"] == "abc-123"
        assert row["dry_run"] == 0
        assert row["side"] == "YES"
        assert abs(row["size"] - 3.50) < 0.01

    def test_get_today_trades(self, tmp_path):
        from src.database import create_tables, get_today_trades, log_trade

        db = str(tmp_path / "db" / "trades.db")
        create_tables(db)

        signal = _FakeSignal(date=date.today())
        log_trade(db, signal, order_result=None, dry_run=True)
        log_trade(db, signal, order_result=None, dry_run=True)

        trades = get_today_trades(db)
        assert len(trades) == 2

    def test_get_today_trades_empty_db(self, tmp_path):
        from src.database import create_tables, get_today_trades

        db = str(tmp_path / "db" / "trades.db")
        create_tables(db)
        assert get_today_trades(db) == []

    def test_get_total_pnl_zero_initially(self, tmp_path):
        from src.database import create_tables, get_total_pnl

        db = str(tmp_path / "db" / "trades.db")
        create_tables(db)
        assert get_total_pnl(db) == 0.0

    def test_get_total_pnl_sums_resolved(self, tmp_path):
        from src.database import create_tables, get_total_pnl, log_trade

        db = str(tmp_path / "db" / "trades.db")
        create_tables(db)

        signal = _FakeSignal(date=date.today())
        row_id = log_trade(db, signal, order_result=None, dry_run=True)

        # Simulate settlement
        con = sqlite3.connect(db)
        con.execute("UPDATE trades SET resolved=1, pnl=2.50 WHERE id=?", (row_id,))
        con.commit()
        con.close()

        assert abs(get_total_pnl(db) - 2.50) < 0.001

    def test_get_total_pnl_no_table(self, tmp_path):
        """Should return 0.0 even if DB doesn't exist yet."""
        from src.database import get_total_pnl

        db = str(tmp_path / "nonexistent.db")
        assert get_total_pnl(db) == 0.0


# ---------------------------------------------------------------------------
# alerts tests
# ---------------------------------------------------------------------------

class TestAlerts:
    def test_send_telegram_alert_no_config(self):
        """Should return False (not raise) when tokens are missing."""
        from src.alerts import send_telegram_alert

        signal = _FakeSignal()
        with patch("src.alerts.TELEGRAM_BOT_TOKEN", ""), \
             patch("src.alerts.TELEGRAM_CHAT_ID", ""):
            result = send_telegram_alert(signal, trade_result=None)
        assert result is False

    def test_send_telegram_alert_dry_run_message(self):
        """Message should contain DRY RUN when trade_result is None."""
        from src.alerts import send_telegram_alert

        signal = _FakeSignal()
        captured = {}

        def fake_send(message):
            captured["msg"] = message
            return True

        with patch("src.alerts._send", side_effect=fake_send), \
             patch("src.alerts.TELEGRAM_BOT_TOKEN", "token"), \
             patch("src.alerts.TELEGRAM_CHAT_ID", "123"):
            result = send_telegram_alert(signal, trade_result=None)

        assert result is True
        assert "DRY RUN" in captured["msg"]
        assert "YES" in captured["msg"]
        assert "3.50" in captured["msg"]

    def test_send_telegram_alert_live_message(self):
        """Message should say LIVE when trade_result is provided."""
        from src.alerts import send_telegram_alert

        signal = _FakeSignal()
        captured = {}

        def fake_send(message):
            captured["msg"] = message
            return True

        with patch("src.alerts._send", side_effect=fake_send), \
             patch("src.alerts.TELEGRAM_BOT_TOKEN", "token"), \
             patch("src.alerts.TELEGRAM_CHAT_ID", "123"):
            result = send_telegram_alert(signal, trade_result={"orderID": "xyz"})

        assert result is True
        assert "LIVE" in captured["msg"]
        assert "xyz" in captured["msg"]

    def test_send_daily_summary_positive_pnl(self):
        """Summary message should include green emoji for positive P&L."""
        from src.alerts import send_daily_summary

        captured = {}

        def fake_send(message):
            captured["msg"] = message
            return True

        stats = {
            "trades_today": 5,
            "pnl_today": 3.20,
            "open_positions": 2,
            "total_exposure": 10.0,
            "wins": 3,
            "losses": 2,
        }

        with patch("src.alerts._send", side_effect=fake_send), \
             patch("src.alerts.TELEGRAM_BOT_TOKEN", "token"), \
             patch("src.alerts.TELEGRAM_CHAT_ID", "123"):
            result = send_daily_summary(stats)

        assert result is True
        assert "🟢" in captured["msg"]
        assert "3W" in captured["msg"] or "3W / 2L" in captured["msg"]

    def test_send_daily_summary_negative_pnl(self):
        """Summary message should include red emoji for negative P&L."""
        from src.alerts import send_daily_summary

        captured = {}

        def fake_send(message):
            captured["msg"] = message
            return True

        stats = {"trades_today": 2, "pnl_today": -1.50, "open_positions": 0,
                 "total_exposure": 0.0, "wins": 0, "losses": 2}

        with patch("src.alerts._send", side_effect=fake_send), \
             patch("src.alerts.TELEGRAM_BOT_TOKEN", "token"), \
             patch("src.alerts.TELEGRAM_CHAT_ID", "123"):
            result = send_daily_summary(stats)

        assert result is True
        assert "🔴" in captured["msg"]


# ---------------------------------------------------------------------------
# trader tests
# ---------------------------------------------------------------------------

class TestTrader:
    def test_initialize_client_with_mock(self):
        """initialize_client should instantiate without raising."""
        mock_client = MagicMock()
        mock_clob_cls = MagicMock(return_value=mock_client)
        mock_creds_cls = MagicMock()

        with patch.dict("sys.modules", {
            "py_clob_client": MagicMock(),
            "py_clob_client.client": MagicMock(ClobClient=mock_clob_cls),
            "py_clob_client.clob_types": MagicMock(ApiCreds=mock_creds_cls),
        }):
            from importlib import reload
            import src.trader as trader_mod
            reload(trader_mod)

            client = trader_mod.initialize_client(
                api_key="key",
                api_secret="secret",
                api_passphrase="pass",
            )
        assert client is not None

    def test_get_open_positions_returns_list(self):
        """get_open_positions should return a list even on empty response."""
        from src.trader import get_open_positions

        mock_client = MagicMock()
        mock_client.get_positions.return_value = []

        result = get_open_positions(mock_client)
        assert isinstance(result, list)

    def test_get_open_positions_handles_error(self):
        """get_open_positions should return [] on exception."""
        from src.trader import get_open_positions

        mock_client = MagicMock()
        mock_client.get_positions.side_effect = Exception("network error")

        result = get_open_positions(mock_client)
        assert result == []

    def test_get_open_positions_unwraps_data_key(self):
        """get_open_positions should unwrap {'data': [...]} responses."""
        from src.trader import get_open_positions

        mock_client = MagicMock()
        mock_client.get_positions.return_value = {"data": [{"id": "pos-1"}]}

        result = get_open_positions(mock_client)
        assert result == [{"id": "pos-1"}]
