"""Vendors, AP invoices, and purchase orders (R365 is the planned source)."""

import uuid
from datetime import date
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
