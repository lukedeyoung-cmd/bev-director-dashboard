"""Shared R365 OData payload fixtures, shaped like the live API (probed 2026-11).

OData v4 JSON: decimal-ish values arrive as JSON numbers and booleans as
JSON true/false. TransactionDetail repeats transactionDetailId across
Detail/Offset/GL-split physical rows — the composite source key exists
for exactly that. Transaction exposes the invoice date as `date` and the
type discriminator as `type` ("AP Invoice" for the purchasing surface).
"""

DICEY_LOCATION_ID = "5a4a675c-7e25-4bbe-9f97-26659f1cbca7"
PARSONS_WT_ENTITY_ID = "c1111111-1111-4111-8111-111111111111"
VENDOR_1_ID = "aaaa1111-0000-4000-8000-000000000001"
VENDOR_2_ID = "aaaa1111-0000-4000-8000-000000000002"
TXN_ID = "bbbb2222-0000-4000-8000-000000000001"
TXN2_ID = "bbbb2222-0000-4000-8000-000000000002"
ITEM_KEG_ID = "cccc3333-0000-4000-8000-000000000001"
ITEM_SPIRITS_ID = "cccc3333-0000-4000-8000-000000000002"


def location(location_id=DICEY_LOCATION_ID, name="11.2 Dicey's West Town", **overrides):
    payload = {
        "locationId": location_id,
        "name": name,
        "locationNumber": "11.2",
        "legalEntityId": PARSONS_WT_ENTITY_ID,
        "legalEntityNumber": "11",
        "legalEntityName": "Parson's West Town",
        "createdOn": "2026-11-03T22:37:42.79Z",
        "modifiedOn": "2026-11-03T22:37:42.79Z",
    }
    payload.update(overrides)
    return payload


def company(company_id=VENDOR_1_ID, name="Chicago Beverage Systems", **overrides):
    payload = {
        "companyId": company_id,
        "name": name,
        "comment": None,
        "createdOn": "2025-10-01T12:00:00.00Z",
        "modifiedOn": "2026-01-15T09:30:00.00Z",
    }
    payload.update(overrides)
    return payload


def item(item_id=ITEM_KEG_ID, name="Guinness Draught 1/2 Keg", **overrides):
    payload = {
        "itemId": item_id,
        "name": name,
        "itemNumber": "GNS-050",
        "category1": "Alcohol",
        "category2": "Beer",
        "category3": "Keg",
        "createdOn": "2025-10-01T12:00:00.00Z",
        "modifiedOn": "2026-02-20T08:00:00.00Z",
    }
    payload.update(overrides)
    return payload


def transaction(txn_id=TXN_ID, date="2026-03-12", **overrides):
    """One AP-invoice header row from the Transaction view."""
    payload = {
        "transactionId": txn_id,
        "locationId": DICEY_LOCATION_ID,
        "locationName": "11.2 Dicey's West Town",
        "date": date,
        "transactionNumber": "AP-100241",
        "name": "Chicago Beverage Systems",
        "type": "AP Invoice",
        "companyId": VENDOR_1_ID,
        "rowVersion": 142710,
        "isApproved": True,
        "isTemplate": False,
        "createdOn": "2026-03-14T05:00:00.00Z",
        "modifiedOn": "2026-03-14T05:00:00.00Z",
    }
    payload.update(overrides)
    return payload


def item_detail(
    detail_id="dddd4444-0000-4000-8000-000000000001",
    txn_id=TXN_ID,
    quantity=10.0,
    debit=167.4,
    **overrides,
):
    """Item-bearing Detail row: debit is extended cost, no unit-cost column."""
    payload = {
        "transactionDetailId": detail_id,
        "transactionId": txn_id,
        "locationId": DICEY_LOCATION_ID,
        "glAccountId": None,
        "itemId": ITEM_KEG_ID,
        "credit": 0.0,
        "debit": debit,
        "amount": None,
        "quantity": quantity,
        "unitOfMeasureName": "Case",
        "bankReconciliation": None,
        "bankDeposit": None,
        "comment": None,
        "rowType": "Detail",
        "rowVersion": 142715,
        "createdOn": "2026-03-14T05:00:00.00Z",
        "modifiedOn": "2026-03-14T05:00:00.00Z",
    }
    payload.update(overrides)
    return payload


def gl_detail(
    detail_id="dddd4444-0000-4000-8000-000000000002",
    txn_id=TXN_ID,
    debit=42.18,
    **overrides,
):
    """GL-only pairing row (no item): freight/tax/delivery charge."""
    payload = {
        "transactionDetailId": detail_id,
        "transactionId": txn_id,
        "locationId": DICEY_LOCATION_ID,
        "glAccountId": "eeee5555-0000-4000-8000-000000000009",
        "itemId": None,
        "credit": 0.0,
        "debit": debit,
        "amount": None,
        "quantity": 0.0,
        "unitOfMeasureName": None,
        "bankReconciliation": None,
        "bankDeposit": None,
        "comment": "Freight",
        "rowType": "Detail",
        "rowVersion": 142716,
        "createdOn": "2026-03-14T05:00:00.00Z",
        "modifiedOn": "2026-03-14T05:00:00.00Z",
    }
    payload.update(overrides)
    return payload
