#!/usr/bin/env python3
"""Live smoke test for the R365 OData Connector (read-only probes).

Verifies Location, Item, Company, and TransactionDetail against the real
API using the injected credentials:

    export R365_ODATA_USERNAME=...   # SECRET_R365_ODATA_USERNAME
    export R365_ODATA_PASSWORD=...   # SECRET_R365_ODATA_PASSWORD
    .venv/bin/python scripts/smoke_r365.py

Probes are bounded (one small sample per view; TransactionDetail limited
to the last 31 days — the server rejects larger windows) to keep API usage
minimal. Prints row counts and sample records — never credentials. Exits
non-zero on any failure.
"""

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta

# Make the app package importable when run from a checkout.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.r365.client import DEFAULT_BASE_URL, R365AuthError, R365ODataClient  # noqa: E402


def sample(client: R365ODataClient, entity: str, params: dict, limit: int) -> list[dict]:
    """Collect at most `limit` rows (breaking early stops further pages)."""
    rows: list[dict] = []
    for row in client.iter_rows(entity, params=params):
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def probe(entity: str, rows: list[dict]) -> None:
    print(f"\n=== {entity}: {len(rows)} row(s) sampled ===")
    for raw in rows:
        if entity == "Location":
            print(
                f"  locationId={raw.get('locationId')} name={raw.get('name')!r} "
                f"legalEntity={raw.get('legalEntityName')!r}"
            )
        elif entity == "Item":
            print(
                f"  itemId={raw.get('itemId')} name={raw.get('name')!r} "
                f"cat1={raw.get('category1')!r}"
            )
        elif entity == "Company":
            print(f"  companyId={raw.get('companyId')} name={raw.get('name')!r}")
        else:
            print(
                f"  detailId={raw.get('transactionDetailId')} rowType={raw.get('rowType')!r} "
                f"debit={raw.get('debit')} qty={raw.get('quantity')}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page-size", type=int, default=5, help="Rows to sample per view.")
    args = parser.parse_args()

    username = os.environ.get("R365_ODATA_USERNAME")
    password = os.environ.get("R365_ODATA_PASSWORD")
    if not username or not password:
        print("ERROR: set R365_ODATA_USERNAME / R365_ODATA_PASSWORD first.", file=sys.stderr)
        return 2

    client = R365ODataClient(username=username, password=password, page_size=args.page_size)
    print(f"Probing {DEFAULT_BASE_URL} with page size {args.page_size} (read-only)...")

    try:
        probe("Location", sample(client, "Location", {}, args.page_size))
        probe("Company", sample(client, "Company", {}, args.page_size))
        probe("Item", sample(client, "Item", {}, args.page_size))

        # TransactionDetail requires a <=31-day window; sample the recent one.
        since = datetime.now(UTC) - timedelta(days=30)
        until = datetime.now(UTC)
        probe(
            "TransactionDetail",
            sample(
                client,
                "TransactionDetail",
                {"$filter": f"createdOn ge {_iso(since)} and createdOn lt {_iso(until)}"},
                args.page_size,
            ),
        )
    except R365AuthError:
        print("ERROR: authentication failed — check the R365 OData credentials.", file=sys.stderr)
        return 1
    except Exception as exc:  # surface any probe failure with a non-zero exit
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("\nSmoke test OK: Location, Company, Item, TransactionDetail all reachable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
