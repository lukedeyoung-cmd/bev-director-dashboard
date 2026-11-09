"""Tests for R365 OData response parsing / normalization (pure functions)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.r365 import transforms as tx
from tests.r365_payloads import (
    DICEY_LOCATION_ID,
    ITEM_KEG_ID,
    TXN_ID,
    VENDOR_1_ID,
    company,
    gl_detail,
    item,
    item_detail,
    location,
    non_ap_transaction,
    transaction,
)


def test_location_preserves_source_fields_verbatim():
    row = tx.parse_location(location())
    assert row["location_id"] == UUID(DICEY_LOCATION_ID)
    assert row["name"] == "11.2 Dicey's West Town"
    assert row["location_number"] == "11.2"
    assert row["legal_entity_id"] == UUID("c1111111-1111-4111-8111-111111111111")
    assert row["legal_entity_name"] == "Parson's West Town"
    assert row["legal_entity_number"] == "11"


def test_location_with_missing_optional_fields():
    row = tx.parse_location({"locationId": DICEY_LOCATION_ID, "name": "X"})
    assert row["legal_entity_id"] is None
    assert row["legal_entity_name"] is None
    assert row["attribute1_id"] is None
    assert row["api_created_on"] is None


def test_location_timestamps_parse_aware():
    row = tx.parse_location(location())
    # Fractional seconds from the API stamp are preserved.
    assert row["api_created_on"] == datetime(2026, 11, 3, 22, 37, 42, 790000, tzinfo=UTC)


def test_vendor_row_defaults():
    row = tx.parse_vendor(company())
    assert row["company_id"] == UUID(VENDOR_1_ID)
    assert row["name"] == "Chicago Beverage Systems"
    assert row["comment"] is None


def test_item_row_categories_default_empty():
    row = tx.parse_item({"itemId": ITEM_KEG_ID, "name": "Guinness"})
    assert row["item_id"] == UUID(ITEM_KEG_ID)
    assert row["category1"] is None
    assert row["category2"] is None
    assert row["category3"] is None

    full = tx.parse_item(item())
    assert full["category1"] == "Alcohol"
    assert full["category2"] == "Beer"


def test_transaction_row_from_probed_shape():
    row = tx.parse_transaction(transaction())
    assert row["transaction_id"] == UUID(TXN_ID)
    assert row["transaction_date"] == date(2026, 3, 12)
    assert row["transaction_type"] == "AP Invoice"
    assert row["is_approved"] is True
    assert row["is_template"] is False
    assert row["company_id"] == UUID(VENDOR_1_ID)
    assert row["row_version"] == 142710


def test_transaction_row_defaults_on_sparse_row():
    row = tx.parse_transaction({"transactionId": TXN_ID, "type": "AP Invoice"})
    assert row["transaction_date"] is None
    assert row["is_approved"] is False  # bool(None)
    assert row["is_template"] is False
    assert row["row_version"] is None
    assert row["company_id"] is None


def test_transaction_row_non_ap_shape_parses_to_nulls():
    """Non-AP rows: absent vendor/number fields parse to None, no exception."""
    row = tx.parse_transaction(non_ap_transaction())
    assert row["transaction_id"] == UUID("bbbb2222-0000-4000-8000-000000000003")
    assert row["transaction_type"] == "Tips Payable"
    assert row["transaction_number"] is None
    assert row["company_id"] is None
    assert row["name"] is None
    assert row["transaction_date"] == date(2026, 3, 15)


def test_transaction_row_requires_identity_fields():
    # transactionId and type are the mirror identity — a row without either
    # cannot be upserted and must fail loudly, not silently.
    with pytest.raises(ValueError, match="transactionId"):
        tx.parse_transaction({"type": "AP Invoice"})
    with pytest.raises(ValueError, match="without type"):
        tx.parse_transaction({"transactionId": TXN_ID})


def test_detail_row_item_kind_vs_gl_kind():
    item_row = tx.parse_transaction_detail(item_detail())
    assert item_row["item_id"] == UUID(ITEM_KEG_ID)
    assert item_row["gl_account_id"] is None
    assert item_row["debit"] == Decimal("167.4")
    assert item_row["quantity"] == Decimal("10")

    gl_row = tx.parse_transaction_detail(gl_detail())
    assert gl_row["gl_account_id"] is not None
    assert gl_row["item_id"] is None
    assert gl_row["comment"] == "Freight"
    assert gl_row["debit"] == Decimal("42.18")


# ----------------------------------------------------------------------
# Scalar coercions
# ----------------------------------------------------------------------
def test_to_decimal_from_number_and_string():
    assert tx._to_decimal(167.4) == Decimal("167.4")
    assert tx._to_decimal("167.4000") == Decimal("167.4000")
    assert tx._to_decimal(10) == Decimal(10)
    assert tx._to_decimal(Decimal("1.5")) == Decimal("1.5")
    assert tx._to_decimal(None) is None
    assert tx._to_decimal("") is None


def test_to_datetime_variants():
    assert tx._to_datetime("2026-03-14T05:00:00Z") == datetime(2026, 3, 14, 5, 0, 0, tzinfo=UTC)
    assert tx._to_datetime(None) is None
    assert tx._to_datetime("") is None


def test_to_date_and_int_and_str():
    assert tx._to_date("2026-03-12T00:00:00Z") == date(2026, 3, 12)
    assert tx._to_date(None) is None
    assert tx._to_int("142710") == 142710
    assert tx._to_int(None) is None
    assert tx._to_str("x") == "x"
    assert tx._to_str("") is None
    assert tx._to_str(None) is None
    assert tx._to_uuid(None) is None
    assert tx._to_uuid(DICEY_LOCATION_ID) == UUID(DICEY_LOCATION_ID)


# ----------------------------------------------------------------------
# Composite source key
# ----------------------------------------------------------------------
def test_source_key_distinguishes_sub_rows():
    # The live view repeats one transactionDetailId across physical rows
    # (Detail line vs GL pairing), so the key includes rowType/glAccountId.
    item_raw = item_detail()
    gl_raw = gl_detail(detail_id=item_raw["transactionDetailId"])
    key1 = tx.source_row_key(
        item_raw["transactionDetailId"], item_raw["rowType"], item_raw["glAccountId"]
    )
    key2 = tx.source_row_key(
        gl_raw["transactionDetailId"], gl_raw["rowType"], gl_raw["glAccountId"]
    )
    assert key1 != key2
    assert len(key1) <= 120  # fits the source_row_key column
    # Deterministic.
    again = tx.source_row_key(
        item_raw["transactionDetailId"], item_raw["rowType"], item_raw["glAccountId"]
    )
    assert again == key1
