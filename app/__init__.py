"""Application factory for the Land & Sea Dept Beverage Director Dashboard."""

from flask import Flask

from app.config import Config


def create_app(config_class: type = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    if not app.config["SECRET_KEY"]:
        raise RuntimeError("SECRET_KEY is required (see .env.example)")

    from app.extensions import csrf, db, login_manager

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from app import models  # noqa: F401  (registers models + the login user loader)
    from app.api.routes import api_bp
    from app.auth.routes import auth_bp
    from app.cli import register_cli
    from app.web.routes import web_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(web_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    register_cli(app)

    return app
