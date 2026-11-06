"""Vendors, AP invoices, and purchase orders (R365 is the planned source)."""

import uuid
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TimestampMixin


class Vendor(TimestampMixin, db.Model):
    __tablename__ = "vendors"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(sa.String(200), unique=True, nullable=False)
    external_ref: Mapped[str | None] = mapped_column(sa.String(64), unique=True)


class Invoice(TimestampMixin, db.Model):
    """An accounts-payable invoice header; lines carry the purchased detail."""

    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    venue_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("venues.id", ondelete="restrict"), nullable=False, index=True
    )
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("vendors.id", ondelete="restrict"), nullable=False, index=True
    )
    invoice_number: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    invoice_date: Mapped[date] = mapped_column(sa.Date, nullable=False, index=True)
    due_date: Mapped[date | None] = mapped_column(sa.Date)
    total: Mapped[Decimal] = mapped_column(sa.Numeric(14, 2), nullable=False, default=Decimal("0"))
    external_ref: Mapped[str | None] = mapped_column(sa.String(64), unique=True)

    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceLine.line_number",
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "vendor_id", "venue_id", "invoice_number", name="uq_invoices_vendor_venue_number"
        ),
    )


class InvoiceLine(db.Model):
    __tablename__ = "invoice_lines"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("invoices.id", ondelete="cascade"), nullable=False, index=True
    )
    line_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    ingredient_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("ingredients.id", ondelete="restrict"), nullable=False
    )
    # Quantity and cost in the invoiced unit; unit_of_measure may differ from
    # the ingredient's base_unit (e.g. a case invoiced vs bottles stocked).
    quantity: Mapped[Decimal] = mapped_column(sa.Numeric(14, 4), nullable=False)
    unit_of_measure: Mapped[str | None] = mapped_column(sa.String(24))
    unit_cost: Mapped[Decimal] = mapped_column(sa.Numeric(14, 4), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(sa.Numeric(14, 2), nullable=False)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class PurchaseOrder(TimestampMixin, db.Model):
    """A purchase order header."""

    __tablename__ = "purchase_orders"

    STATUSES = ("open", "received", "closed", "cancelled")

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    venue_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("venues.id", ondelete="restrict"), nullable=False, index=True
    )
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("vendors.id", ondelete="restrict"), nullable=False, index=True
    )
    po_number: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    order_date: Mapped[date] = mapped_column(sa.Date, nullable=False, index=True)
    expected_date: Mapped[date | None] = mapped_column(sa.Date)
    status: Mapped[str] = mapped_column(
        sa.Enum(*STATUSES, name="po_status", native_enum=False), nullable=False, default="open"
    )
    external_ref: Mapped[str | None] = mapped_column(sa.String(64), unique=True)

    lines: Mapped[list["PurchaseOrderLine"]] = relationship(
        back_populates="purchase_order",
        cascade="all, delete-orphan",
        order_by="PurchaseOrderLine.line_number",
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "vendor_id", "venue_id", "po_number", name="uq_purchase_orders_vendor_venue_number"
        ),
    )


class PurchaseOrderLine(db.Model):
    __tablename__ = "purchase_order_lines"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("purchase_orders.id", ondelete="cascade"), nullable=False, index=True
    )
    line_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    ingredient_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("ingredients.id", ondelete="restrict"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(sa.Numeric(14, 4), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(sa.Numeric(14, 4), nullable=False)

    purchase_order: Mapped[PurchaseOrder] = relationship(back_populates="lines")



# ----------------------------------------------------------------------
# R365 raw mirrors
#
# Verbatim mirrors of the R365 OData views (Basic auth over
# https://odata.restaurant365.net/api/v2/views, probed 2026-11). Columns
# map 1:1 to the views' EDM properties; the sync upserts these rows and
# nothing else. Normalization into the Vendor/Invoice tables above happens
# later, together with the Toast↔R365 item mapping (separate task). Rows
# reference each other by R365 GUID columns without FK constraints so
# ingestion order never matters and partial syncs stay loadable.
# ----------------------------------------------------------------------


class R365Location(TimestampMixin, db.Model):
    """R365 OData Location view — venue reference (R365 names are source of
    truth; mapping to dashboard Venue rows is a separate task)."""

    __tablename__ = "r365_locations"

    location_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True)
    name: Mapped[str | None] = mapped_column(sa.String(255))
    location_number: Mapped[str | None] = mapped_column(sa.String(64))
    legal_entity_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    legal_entity_number: Mapped[str | None] = mapped_column(sa.String(64))
    legal_entity_name: Mapped[str | None] = mapped_column(sa.String(255))
    attribute1_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    attribute1_number: Mapped[str | None] = mapped_column(sa.String(64))
    attribute1_name: Mapped[str | None] = mapped_column(sa.String(255))
    attribute2_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    attribute2_number: Mapped[str | None] = mapped_column(sa.String(64))
    attribute2_name: Mapped[str | None] = mapped_column(sa.String(255))
    api_created_on: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    api_modified_on: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class R365Vendor(TimestampMixin, db.Model):
    """R365 OData Company view — the vendor surface (companies payables are
    booked against; Transaction.companyId references it)."""

    __tablename__ = "r365_vendors"

    company_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True)
    name: Mapped[str | None] = mapped_column(sa.String(255))
    company_number: Mapped[str | None] = mapped_column(sa.String(64))
    comment: Mapped[str | None] = mapped_column(sa.String(500))
    api_created_on: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    api_modified_on: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class R365Item(TimestampMixin, db.Model):
    """R365 OData Item view — the R365 item catalog (cost items, not POS items)."""

    __tablename__ = "r365_items"

    item_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True)
    name: Mapped[str | None] = mapped_column(sa.String(255))
    item_number: Mapped[str | None] = mapped_column(sa.String(64))
    category1: Mapped[str | None] = mapped_column(sa.String(100), index=True)
    category2: Mapped[str | None] = mapped_column(sa.String(100))
    category3: Mapped[str | None] = mapped_column(sa.String(100))
    api_created_on: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    api_modified_on: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class R365Transaction(TimestampMixin, db.Model):
    """R365 OData Transaction view — AP-invoice headers (type = 'AP Invoice').

    The purchasing sync filters to AP Invoice rows; other transaction types
    (budget templates, journal entries) stay out of the cost pipeline.
    """

    __tablename__ = "r365_transactions"

    transaction_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True)
    location_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, index=True)
    location_name: Mapped[str | None] = mapped_column(sa.String(255))
    transaction_date: Mapped[date | None] = mapped_column(sa.Date, index=True)
    transaction_number: Mapped[str | None] = mapped_column(sa.String(120))
    name: Mapped[str | None] = mapped_column(sa.String(500))
    transaction_type: Mapped[str | None] = mapped_column(sa.String(50), index=True)
    company_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, index=True)
    row_version: Mapped[int | None] = mapped_column(sa.BigInteger, index=True)
    is_approved: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    is_template: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    api_created_on: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    api_modified_on: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class R365TransactionDetail(TimestampMixin, db.Model):
    """R365 OData TransactionDetail view — invoice/transaction line items.

    Item-bearing Detail rows are the purchase lines: debit holds the
    extended cost, quantity the invoiced units of unit_of_measure_name.
    The view has no unit-cost or transaction-date column; unit cost is
    derived downstream (debit / quantity) and the invoice date comes from
    the R365Transaction header. transactionDetailId repeats across a
    line's physical rows (Detail/Offset/GL splits), so identity is
    source_row_key (detail id | rowType | GL account).
    """

    __tablename__ = "r365_transaction_details"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    source_row_key: Mapped[str] = mapped_column(sa.String(120), unique=True, nullable=False)
    transaction_detail_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, index=True)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, index=True)
    location_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, index=True)
    gl_account_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, index=True)
    item_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, index=True)
    credit: Mapped[Decimal | None] = mapped_column(sa.Numeric(14, 4))
    debit: Mapped[Decimal | None] = mapped_column(sa.Numeric(14, 4))
    amount: Mapped[Decimal | None] = mapped_column(sa.Numeric(14, 4))
    quantity: Mapped[Decimal | None] = mapped_column(sa.Numeric(14, 4))
    previous_count_total: Mapped[Decimal | None] = mapped_column(sa.Numeric(14, 4))
    adjustment: Mapped[Decimal | None] = mapped_column(sa.Numeric(14, 4))
    unit_of_measure_name: Mapped[str | None] = mapped_column(sa.String(120))
    bank_reconciliation: Mapped[str | None] = mapped_column(sa.String(255))
    bank_deposit: Mapped[str | None] = mapped_column(sa.String(255))
    comment: Mapped[str | None] = mapped_column(sa.String(500))
    row_type: Mapped[str | None] = mapped_column(sa.String(50), index=True)
    row_version: Mapped[int | None] = mapped_column(sa.BigInteger, index=True)
    api_created_on: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    api_modified_on: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class R365SyncState(TimestampMixin, db.Model):
    """Per-entity ingestion checkpoints.

    last_history_period marks the last completed month end of the initial
    history load (so completed months are never re-pulled); max_row_version
    is the incremental-sync checkpoint (rows strictly above it are new or
    edited). One row per entity name, e.g. 'Transaction', 'TransactionDetail'.
    """

    __tablename__ = "r365_sync_state"

    entity: Mapped[str] = mapped_column(sa.String(50), primary_key=True)
    last_history_period: Mapped[date | None] = mapped_column(sa.Date)
    max_row_version: Mapped[int | None] = mapped_column(sa.BigInteger)
    last_sync_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
