"""Shared Flask extension singletons."""

import sqlalchemy as sa
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect

# Alembic-friendly constraint naming (stable names for future migrations).
naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

db = SQLAlchemy(metadata=sa.MetaData(naming_convention=naming_convention))
login_manager = LoginManager()
login_manager.login_view = "auth.login"
csrf = CSRFProtect()
