"""Tests for Flask webhook server."""

import json
import os
import sqlite3
import tempfile

import pytest

from src.database import create_tables
from src.experiments import create_experiment_tables
from src.learner import create_learning_tables
from src.webhook_server import app


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
def client(db, monkeypatch):
    """Create Flask test client."""
    # Set environment variables
    monkeypatch.setenv("DB_PATH", db)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456")

    # Reload module to pick up new env vars
    import importlib
    import src.webhook_server
    importlib.reload(src.webhook_server)

    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_index_endpoint(client):
    """Test index endpoint returns status."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["service"] == "polymarket-weather-bot-webhook"


def test_health_endpoint(client):
    """Test health endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "healthy"


def test_webhook_empty_update(client):
    """Test webhook with empty update."""
    response = client.post(
        "/webhook",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "empty"


def test_webhook_no_message(client):
    """Test webhook with update but no message."""
    update = {
        "update_id": 123,
    }
    response = client.post(
        "/webhook",
        data=json.dumps(update),
        content_type="application/json",
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "no_message"


def test_webhook_unauthorized_chat(client):
    """Test webhook from unauthorized chat."""
    update = {
        "update_id": 123,
        "message": {
            "chat": {"id": 999999},  # Different chat ID
            "text": "/status",
        },
    }
    response = client.post(
        "/webhook",
        data=json.dumps(update),
        content_type="application/json",
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "unauthorized"


def test_webhook_not_command(client):
    """Test webhook with non-command text."""
    update = {
        "update_id": 123,
        "message": {
            "chat": {"id": 123456},  # Authorized chat
            "text": "Hello bot",  # Not a command
        },
    }
    response = client.post(
        "/webhook",
        data=json.dumps(update),
        content_type="application/json",
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "not_command"


def test_webhook_valid_command(client, db, monkeypatch):
    """Test webhook with valid command."""
    # Mock send_message to avoid actual Telegram API call
    sent_messages = []

    def mock_send_message(self, text, parse_mode="Markdown"):
        sent_messages.append(text)
        return True

    monkeypatch.setattr(
        "src.telegram_handler.TelegramCommandHandler.send_message",
        mock_send_message,
    )

    # Add learning params so /status works
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

    update = {
        "update_id": 123,
        "message": {
            "chat": {"id": 123456},  # Authorized chat
            "text": "/hjelp",  # Valid command
        },
    }

    response = client.post(
        "/webhook",
        data=json.dumps(update),
        content_type="application/json",
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["command"] == "hjelp"

    # Verify message was sent
    assert len(sent_messages) == 1
    assert "TILGJENGELIGE KOMMANDOER" in sent_messages[0]
