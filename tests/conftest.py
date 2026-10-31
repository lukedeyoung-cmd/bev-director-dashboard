"""Test fixtures: isolated app + database per test."""

import os
import tempfile

# Set before importing app.config: Config reads the environment at import time.
_TEST_DIR = tempfile.mkdtemp(prefix="bev-tests-")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEST_DIR}/test.sqlite3")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

import pytest  # noqa: E402

from app import create_app  # noqa: E402
from app.config import Config  # noqa: E402
from app.extensions import db as _db  # noqa: E402
from app.models import User  # noqa: E402


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    with app.app_context():
        _db.create_all()
        yield app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def user(app):
    user = User(email="luke.deyoung@gmail.com", name="Luke DeYoung", role="admin")
    user.set_password("test-password-123")
    _db.session.add(user)
    _db.session.commit()
    return user
