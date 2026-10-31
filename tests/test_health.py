"""Tests for the health endpoint and basic app boot."""

import pytest

from app import create_app
from app.config import Config


def test_healthz_ok(client):
    response = client.get("/api/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "database": "ok"}


def test_app_requires_secret_key():
    class NoSecretConfig(Config):
        TESTING = True
        SECRET_KEY = None

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(NoSecretConfig)
