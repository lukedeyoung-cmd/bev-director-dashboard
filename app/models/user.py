"""Application users and authentication."""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from flask_login import UserMixin
from sqlalchemy.orm import Mapped, mapped_column
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, login_manager
from app.models.base import TimestampMixin


class User(TimestampMixin, db.Model, UserMixin):
    __tablename__ = "users"

    ROLES = ("admin", "leadership", "viewer")

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(sa.String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    role: Mapped[str] = mapped_column(
        sa.Enum(*ROLES, name="user_role", native_enum=False),
        nullable=False,
        default="viewer",
    )
    active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<User {self.email}>"

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return self.password_hash is not None and check_password_hash(self.password_hash, password)

    def touch_last_login(self) -> None:
        self.last_login_at = datetime.now(UTC)

    # Flask-Login reads is_active; UserMixin's constant True would admit disabled accounts.
    @property
    def is_active(self) -> bool:  # type: ignore[override]
        return self.active


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    try:
        pk = uuid.UUID(str(user_id))
    except ValueError:
        return None
    return db.session.get(User, pk)
