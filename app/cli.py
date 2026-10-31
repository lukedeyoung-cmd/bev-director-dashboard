"""Management CLI commands (flask <command>)."""

import click
import sqlalchemy as sa
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models.user import User


def register_cli(app) -> None:
    from app.fixtures import register_fixture_commands

    register_fixture_commands(app)

    @app.cli.command("create-admin")
    @click.option("--email", required=True, help="Login email for the new admin.")
    @click.option("--name", required=True, help="Display name.")
    @click.password_option(help="Password (prompted interactively if omitted).")
    def create_admin(email: str, name: str, password: str) -> None:
        """Create an administrator account (run once per deployment)."""
        normalized = email.strip().lower()
        existing = db.session.scalar(sa.select(User).where(User.email == normalized))
        if existing is not None:
            raise click.ClickException(f"A user with email {normalized} already exists.")
        user = User(email=normalized, name=name.strip(), role="admin")
        user.password_hash = generate_password_hash(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Created admin user {normalized}")
