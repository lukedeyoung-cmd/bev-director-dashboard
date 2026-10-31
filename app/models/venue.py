"""Venues in the Land & Sea Dept portfolio."""

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import TimestampMixin


class Venue(TimestampMixin, db.Model):
    __tablename__ = "venues"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    code: Mapped[str] = mapped_column(sa.String(20), unique=True, nullable=False)
    timezone: Mapped[str] = mapped_column(sa.String(64), nullable=False, default="America/Chicago")
    # POS system identifier for this location (e.g. Toast restaurant GUID),
    # set when the venue's data source is connected.
    pos_venue_ref: Mapped[str | None] = mapped_column(sa.String(120), unique=True)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<Venue {self.code}>"
