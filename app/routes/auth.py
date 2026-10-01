from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required
from app import db
from app.models import User

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for("main.dashboard"))
        flash("Invalid credentials", "danger")
    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/setup")
def setup():
    from app.config import Config
    user = User.query.filter_by(username=Config.ADMIN_USERNAME).first()
    if not user:
        user = User(username=Config.ADMIN_USERNAME, is_admin=True)
        user.set_password(Config.ADMIN_PASSWORD)
        db.session.add(user)
        db.session.commit()
    return redirect(url_for("auth.login"))
