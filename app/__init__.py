import os
from urllib.parse import urlencode
from flask import Flask, request
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()


def create_app():
    app = Flask(__name__)
    app.config.from_object("app.config.Config")

    from app.config import _is_postgres, make_pg_creator, postgres_reachable

    if _is_postgres(app.config["SQLALCHEMY_DATABASE_URI"]):
        opts = dict(app.config.get("SQLALCHEMY_ENGINE_OPTIONS") or {})
        opts["creator"] = make_pg_creator()
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = opts

        with app.app_context():
            if not postgres_reachable(app.config["SQLALCHEMY_DATABASE_URI"]):
                app.logger.warning(
                    "Neon Postgres unreachable. Falling back to local SQLite (instance/tms.db)."
                )
                app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "..", "instance", "tms.db"
                ).replace("\\", "/")
                app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {}

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)
    login_manager.login_view = "auth.login"

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from app.routes.auth import auth_bp
    from app.routes.transporters import transporters_bp
    from app.routes.trips import trips_bp
    from app.routes.expenses import expenses_bp
    from app.routes.payments import payments_bp
    from app.routes.reports import reports_bp
    from app.routes.plants import plants_bp
    from app.routes.work_order_records import work_order_records_bp
    from app.routes.mines import mines_bp
    from app.routes.main import main_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(transporters_bp)
    app.register_blueprint(trips_bp)
    app.register_blueprint(expenses_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(plants_bp)
    app.register_blueprint(work_order_records_bp)
    app.register_blueprint(mines_bp)
    app.register_blueprint(main_bp)

    @app.template_filter("dt")
    def dt(value):
        if value is None or value == "":
            return "-"
        if hasattr(value, "strftime"):
            return value.strftime("%d/%m/%Y")
        from datetime import datetime
        try:
            return datetime.strptime(str(value), "%Y-%m-%d").strftime("%d/%m/%Y")
        except (ValueError, TypeError):
            return str(value)

    @app.template_filter("qty")
    def qty(value):
        """Show a quantity with at least 3 decimal places and no rounding of the
        stored value (stored precision is 4 decimals)."""
        if value is None or value == "":
            return "-"
        try:
            v = float(value)
        except (TypeError, ValueError):
            return value
        s = f"{v:.4f}".rstrip("0")
        if s.endswith("."):
            s = s[:-1]
        if "." in s:
            decimals = len(s.split(".")[1])
            if decimals < 3:
                s += "0" * (3 - decimals)
        else:
            s += ".000"
        return s

    @app.context_processor
    def inject_helpers():
        def page_url(page):
            args = request.args.copy()
            args["page"] = page
            return urlencode(args)
        return dict(page_url=page_url)

    from app.config import Config
    if not Config.SKIP_INIT_SEED:
        with app.app_context():
            try:
                db.create_all()
            except Exception:
                pass

            if not User.query.filter_by(username=Config.ADMIN_USERNAME).first():
                user = User(username=Config.ADMIN_USERNAME, is_admin=True)
                user.set_password(Config.ADMIN_PASSWORD)
                db.session.add(user)
                db.session.commit()

    return app
