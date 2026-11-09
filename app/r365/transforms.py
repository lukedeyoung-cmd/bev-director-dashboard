"""Pure R365 OData row parsers: API JSON → model-attribute dicts.

Every function maps one raw view row (the shapes probed live, 2026-11) to
a dict whose keys match the corresponding SQLAlchemy model attributes in
app/models/purchasing.py. No DB access, no client access — trivially
testable against recorded fixtures.

Decimal handling: the API serializes Edm.Decimal as JSON numbers, which
Python parses as float. Floats are converted via str() to avoid binary
float artifacts (0.1-style drift) before landing in Numeric columns.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

ItemRow = dict[str, Any]

# The AP-invoice type — the purchasing surface's transaction type. Header
# ingestion mirrors every type (a detail line must always join to a header);
# queries that want AP-only spend filter on R365Transaction.transaction_type
# with this constant.
PURCHASE_TRANSACTION_TYPE = "AP Invoice"

# Detail rows that carry purchased goods vs GL-only pairings.
DETAIL_ROW_TYPE = "Detail"


def _to_uuid(value: Any) -> uuid.UUID | None:
    """GUID string → UUID; None/blank stays None."""
    if value is None or value == "":
        return None
    return uuid.UUID(str(value))


def _to_decimal(value: Any) -> Decimal | None:
    """JSON number (float) or string → Decimal; None/blank stays None."""
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _to_datetime(value: Any) -> datetime | None:
    """OData DateTimeOffset string (…Z) → aware datetime; None stays None."""
    if value is None or value == "":
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _to_date(value: Any) -> date | None:
    """OData DateTimeOffset string → calendar date (UTC midnight convention)."""
    if value is None or value == "":
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _to_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def source_row_key(
    transaction_detail_id: Any, row_type: Any, gl_account_id: Any
) -> str:
    """Stable identity for a TransactionDetail row.

    The API declares transactionDetailId as the key, but the live view
    returns multiple physical rows sharing one id (a Detail line and its
    Offset pairing; GL splits), distinguished by rowType and glAccountId.
    Zero collisions on the triple in a 1000-row live sample (2026-11).
    """
    return f"{transaction_detail_id}|{row_type}|{gl_account_id or ''}"


def parse_location(row: ItemRow) -> ItemRow:
    """Location view row → R365Location attributes (verbatim, no venue mapping)."""
    return {
        "location_id": _to_uuid(row.get("locationId")),
        "name": _to_str(row.get("name")),
        "location_number": _to_str(row.get("locationNumber")),
        "legal_entity_id": _to_uuid(row.get("legalEntityId")),
        "legal_entity_number": _to_str(row.get("legalEntityNumber")),
        "legal_entity_name": _to_str(row.get("legalEntityName")),
        "attribute1_id": _to_uuid(row.get("attribute1Id")),
        "attribute1_number": _to_str(row.get("attribute1Number")),
        "attribute1_name": _to_str(row.get("attribute1Name")),
        "attribute2_id": _to_uuid(row.get("attribute2Id")),
        "attribute2_number": _to_str(row.get("attribute2Number")),
        "attribute2_name": _to_str(row.get("attribute2Name")),
        "api_created_on": _to_datetime(row.get("createdOn")),
        "api_modified_on": _to_datetime(row.get("modifiedOn")),
    }


def parse_vendor(row: ItemRow) -> ItemRow:
    """Company view row (R365's vendor surface) → R365Vendor attributes."""
    return {
        "company_id": _to_uuid(row.get("companyId")),
        "name": _to_str(row.get("name")),
        "company_number": _to_str(row.get("companyNumber")),
        "comment": _to_str(row.get("comment")),
        "api_created_on": _to_datetime(row.get("createdOn")),
        "api_modified_on": _to_datetime(row.get("modifiedOn")),
    }


def parse_item(row: ItemRow) -> ItemRow:
    """Item view row (R365 item catalog) → R365Item attributes."""
    return {
        "item_id": _to_uuid(row.get("itemId")),
        "name": _to_str(row.get("name")),
        "item_number": _to_str(row.get("itemNumber")),
        "category1": _to_str(row.get("category1")),
        "category2": _to_str(row.get("category2")),
        "category3": _to_str(row.get("category3")),
        "api_created_on": _to_datetime(row.get("createdOn")),
        "api_modified_on": _to_datetime(row.get("modifiedOn")),
    }


def parse_transaction(row: ItemRow) -> ItemRow:
    """Transaction view row (any type) → R365Transaction attributes.

    Non-AP types (Tips Payable, Credit Card Combined, …) leave vendor and
    invoice-number fields null or absent — every non-key field parses to
    None without error. Only identity is enforced: a row without a
    transactionId or type has no mirror identity and would upsert garbage.
    """
    transaction_id = _to_uuid(row.get("transactionId"))
    transaction_type = _to_str(row.get("type"))
    if transaction_id is None:
        raise ValueError(f"Transaction row without transactionId: {row!r}")
    if transaction_type is None:
        raise ValueError(f"Transaction row {transaction_id} without type")
    return {
        "transaction_id": transaction_id,
        "location_id": _to_uuid(row.get("locationId")),
        "location_name": _to_str(row.get("locationName")),
        "transaction_date": _to_date(row.get("date")),
        "transaction_number": _to_str(row.get("transactionNumber")),
        "name": _to_str(row.get("name")),
        "transaction_type": transaction_type,
        "company_id": _to_uuid(row.get("companyId")),
        "row_version": _to_int(row.get("rowVersion")),
        "is_approved": bool(row.get("isApproved")),
        "is_template": bool(row.get("isTemplate")),
        "api_created_on": _to_datetime(row.get("createdOn")),
        "api_modified_on": _to_datetime(row.get("modifiedOn")),
    }


def parse_transaction_detail(row: ItemRow) -> ItemRow:
    """TransactionDetail view row (invoice line) → R365TransactionDetail attrs.

    Cost arrives as debit/credit (extended, in invoice currency) — the view
    has no unit-cost column; unit cost is derived downstream as
    debit / quantity when quantity > 0. Item-bearing Detail rows are the
    purchase lines; Offset rows carry the AP credit pairing.
    """
    detail_id = row.get("transactionDetailId")
    row_type = _to_str(row.get("rowType"))
    gl_account_id = row.get("glAccountId")
    return {
        "source_row_key": source_row_key(detail_id, row_type, gl_account_id),
        "transaction_detail_id": _to_uuid(detail_id),
        "transaction_id": _to_uuid(row.get("transactionId")),
        "location_id": _to_uuid(row.get("locationId")),
        "gl_account_id": _to_uuid(gl_account_id),
        "item_id": _to_uuid(row.get("itemId")),
        "credit": _to_decimal(row.get("credit")),
        "debit": _to_decimal(row.get("debit")),
        "amount": _to_decimal(row.get("amount")),
        "quantity": _to_decimal(row.get("quantity")),
        "previous_count_total": _to_decimal(row.get("previousCountTotal")),
        "adjustment": _to_decimal(row.get("adjustment")),
        "unit_of_measure_name": _to_str(row.get("unitOfMeasureName")),
        "bank_reconciliation": _to_str(row.get("bankReconciliation")),
        "bank_deposit": _to_str(row.get("bankDeposit")),
        "comment": _to_str(row.get("comment")),
        "row_type": row_type,
        "row_version": _to_int(row.get("rowVersion")),
        "api_created_on": _to_datetime(row.get("createdOn")),
        "api_modified_on": _to_datetime(row.get("modifiedOn")),
    }
