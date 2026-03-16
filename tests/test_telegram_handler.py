"""Tests for Telegram command handler."""

import os
import sqlite3
import tempfile

import pytest

from src.database import create_tables
from src.experiments import create_experiment_tables
from src.learner import create_learning_tables
from src.telegram_handler import TelegramCommandHandler


@pytest.fixture
def db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    # Initialize database
    create_tables(path)
    create_learning_tables(path)
    create_experiment_tables(path)

    yield path

    # Cleanup
    try:
        os.unlink(path)
    except Exception:
        pass


@pytest.fixture
def handler(db):
    """Create a Telegram handler for testing."""
    return TelegramCommandHandler(
        bot_token="test_token",
        allowed_chat_id="123456",
        db_path=db,
    )


def test_create_handler(handler):
    """Test that handler is created successfully."""
    assert handler is not None
    assert handler.bot_token == "test_token"
    assert handler.allowed_chat_id == "123456"


def test_handle_hjelp_command(handler):
    """Test /hjelp command."""
    response = handler.handle_command("hjelp", [])
    assert "TILGJENGELIGE KOMMANDOER" in response
    assert "/status" in response
    assert "/rapport" in response
    assert "/eksperimenter" in response


def test_handle_unknown_command(handler):
    """Test unknown command."""
    response = handler.handle_command("unknown", [])
    assert "Ukjent kommando" in response
    assert "/hjelp" in response


def test_handle_status_command(handler):
    """Test /status command with empty database."""
    response = handler.handle_command("status", [])
    assert "BOT STATUS" in response
    assert "All-Time Performance" in response


def test_handle_stopp_command_no_args(handler):
    """Test /stopp command without arguments."""
    response = handler.handle_command("stopp", [])
    assert "Usage:" in response
    assert "/stopp [city] [side]" in response


def test_handle_stopp_command_invalid_side(handler):
    """Test /stopp command with invalid side."""
    response = handler.handle_command("stopp", ["chicago", "MAYBE"])
    assert "Side must be YES or NO" in response


def test_handle_stopp_command_no_params(handler):
    """Test /stopp command when no learning params exist."""
    response = handler.handle_command("stopp", ["chicago", "YES"])
    assert "No learning params found" in response


def test_handle_stopp_command_success(handler, db):
    """Test /stopp command successfully deactivating a strategy."""
    # Create learning params
    con = sqlite3.connect(db)
    try:
        con.execute(
            """
            INSERT INTO learning_params
              (city, side, win_rate_10, win_rate_30, win_rate_all,
               total_trades, position_size, active, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("chicago", "YES", 0.5, 0.5, 0.5, 10, 5.0, 1, "2026-03-16"),
        )
        con.commit()
    finally:
        con.close()

    response = handler.handle_command("stopp", ["chicago", "YES"])
    assert "Deactivated" in response
    assert "Chicago YES" in response

    # Verify it was deactivated
    con = sqlite3.connect(db)
    try:
        cur = con.execute(
            "SELECT active FROM learning_params WHERE city = ? AND side = ?",
            ("chicago", "YES"),
        )
        row = cur.fetchone()
        assert row[0] == 0
    finally:
        con.close()


def test_handle_start_command_success(handler, db):
    """Test /start command successfully reactivating a strategy."""
    # Create deactivated learning params
    con = sqlite3.connect(db)
    try:
        con.execute(
            """
            INSERT INTO learning_params
              (city, side, win_rate_10, win_rate_30, win_rate_all,
               total_trades, position_size, active, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("miami", "NO", 0.6, 0.6, 0.6, 20, 8.0, 0, "2026-03-16"),
        )
        con.commit()
    finally:
        con.close()

    response = handler.handle_command("start", ["miami", "NO"])
    assert "Reactivated" in response
    assert "Miami NO" in response

    # Verify it was reactivated
    con = sqlite3.connect(db)
    try:
        cur = con.execute(
            "SELECT active FROM learning_params WHERE city = ? AND side = ?",
            ("miami", "NO"),
        )
        row = cur.fetchone()
        assert row[0] == 1
    finally:
        con.close()


def test_process_updates_unauthorized_chat(handler):
    """Test that updates from unauthorized chats are ignored."""
    updates = [
        {
            "update_id": 1,
            "message": {
                "chat": {"id": 999999},  # Different chat ID
                "text": "/status",
            },
        }
    ]

    # Should not raise exception, just ignore
    handler.process_updates(updates)


def test_process_updates_authorized_chat(handler, db):
    """Test that updates from authorized chat are processed."""
    # Add a learning param so /status works
    con = sqlite3.connect(db)
    try:
        con.execute(
            """
            INSERT INTO learning_params
              (city, side, win_rate_10, win_rate_30, win_rate_all,
               total_trades, position_size, active, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("chicago", "YES", 0.5, 0.5, 0.5, 10, 5.0, 1, "2026-03-16"),
        )
        con.commit()
    finally:
        con.close()

    updates = [
        {
            "update_id": 1,
            "message": {
                "chat": {"id": 123456},  # Authorized chat ID
                "text": "/status",
            },
        }
    ]

    # Should process without error
    # (Note: won't actually send message in test, but will call handle_command)
    handler.process_updates(updates)


def test_handle_kalibrering_command_no_data(handler):
    """Test /kalibrering command with no calibration data."""
    response = handler.handle_command("kalibrering", [])
    assert "FORECAST CALIBRATION" in response
    assert "Insufficient data" in response


def test_handle_eksperimenter_command_no_experiments(handler):
    """Test /eksperimenter command with no active experiments."""
    response = handler.handle_command("eksperimenter", [])
    assert "EXPERIMENTS" in response
    assert "None" in response or "no active" in response.lower()
