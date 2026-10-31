"""Authentication routes: session login/logout."""

import sqlalchemy as sa
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.auth.forms import LoginForm
from app.extensions import db
from app.models.user import User

auth_bp = Blueprint("auth", __name__)


def _safe_next(target: str | None) -> str:
    """Only follow same-origin relative redirect targets."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("web.dashboard")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("web.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.scalar(
            sa.select(User).where(User.email == form.email.data.lower().strip())
        )
        # One message for all failures so responses don't reveal which emails exist.
        if user is None or not user.is_active or not user.check_password(form.password.data):
            flash("Invalid email or password.", "error")
            return render_template("login.html", form=form), 401
        login_user(user)
        user.touch_last_login()
        db.session.commit()
        return redirect(_safe_next(request.values.get("next")))

    return render_template("login.html", form=form)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You are signed out.", "ok")
    return redirect(url_for("auth.login"))
