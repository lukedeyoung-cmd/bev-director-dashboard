"""CLI smoke test: create-admin provisions a working account."""


def test_create_admin_cli(app):
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=[
            "create-admin",
            "--email",
            "luke.deyoung@gmail.com",
            "--name",
            "Luke DeYoung",
            "--password",
            "s3cret-pw",
        ]
    )
    assert result.exit_code == 0, result.output

    import sqlalchemy as sa

    from app.extensions import db
    from app.models import User

    user = db.session.scalar(sa.select(User).where(User.email == "luke.deyoung@gmail.com"))
    assert user is not None
    assert user.role == "admin"
    assert user.check_password("s3cret-pw")


def test_create_admin_rejects_duplicate(app):
    runner = app.test_cli_runner()
    args = [
        "create-admin",
        "--email",
        "luke.deyoung@gmail.com",
        "--name",
        "Luke DeYoung",
        "--password",
        "s3cret-pw",
    ]
    assert runner.invoke(args=args).exit_code == 0
    duplicate = runner.invoke(args=args)
    assert duplicate.exit_code != 0
    assert "already exists" in duplicate.output
