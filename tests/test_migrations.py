"""End-to-end migration check: `alembic upgrade head` must build a usable schema."""

import sqlite3

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig

EXPECTED_TABLES = {
    "venues",
    "users",
    "pos_items",
    "ingredients",
    "daily_sales",
    "vendors",
    "invoices",
    "invoice_lines",
    "purchase_orders",
    "purchase_order_lines",
    "inventory_counts",
    "inventory_count_lines",
    "recipes",
    "recipe_ingredients",
    "pos_item_ingredient_maps",
}


@pytest.fixture()
def isolated_db_url(tmp_path, monkeypatch):
    """Point alembic at a throwaway database, independent of the test app DB."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    yield f"sqlite:///{tmp_path / 'migrations.sqlite3'}"


def test_upgrade_head_builds_all_tables(isolated_db_url, tmp_path):
    cfg = AlembicConfig("alembic.ini")
    cfg.set_main_option("script_location", "migrations")
    cfg.set_main_option("sqlalchemy.url", isolated_db_url)
    command.upgrade(cfg, "head")

    with sqlite3.connect(tmp_path / "migrations.sqlite3") as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in rows}

    missing = EXPECTED_TABLES - tables
    assert not missing, f"migration did not create: {sorted(missing)}"


def test_downgrade_then_upgrade_is_clean(isolated_db_url):
    cfg = AlembicConfig("alembic.ini")
    cfg.set_main_option("script_location", "migrations")
    cfg.set_main_option("sqlalchemy.url", isolated_db_url)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
