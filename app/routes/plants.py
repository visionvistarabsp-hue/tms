from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required
from app import db
from app.models import Plant
from app.utils import log_audit

plants_bp = Blueprint("plants", __name__, url_prefix="/plants")


@plants_bp.route("/")
@login_required
def list():
    plants = Plant.query.order_by(Plant.name).all()
    return render_template("plants/list.html", plants=plants)


@plants_bp.route("/add", methods=["GET", "POST"])
@login_required
def add():
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            if not name:
                flash("Plant name is required", "danger")
                return render_template("plants/form.html", plant=None)
            if Plant.query.filter_by(name=name).first():
                flash("Plant with this name already exists", "danger")
                return render_template("plants/form.html", plant=None)
            p = Plant(
                name=name,
                location=request.form.get("location", "").strip(),
            )
            db.session.add(p)
            db.session.flush()
            log_audit("create", "plant", p.id, f"Created plant: {p.name}")
            db.session.commit()
            flash("Plant added successfully", "success")
            return redirect(url_for("plants.list"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")
    return render_template("plants/form.html", plant=None)


@plants_bp.route("/edit/<int:id>", methods=["GET", "POST"])
@login_required
def edit(id):
    p = Plant.query.get_or_404(id)
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            if not name:
                flash("Plant name is required", "danger")
                return render_template("plants/form.html", plant=p)
            existing = Plant.query.filter(Plant.name == name, Plant.id != id).first()
            if existing:
                flash("Plant with this name already exists", "danger")
                return render_template("plants/form.html", plant=p)
            p.name = name
            p.location = request.form.get("location", "").strip()
            log_audit("update", "plant", p.id, f"Updated plant: {p.name}")
            db.session.commit()
            flash("Plant updated successfully", "success")
            return redirect(url_for("plants.list"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")
    return render_template("plants/form.html", plant=p)


@plants_bp.route("/delete/<int:id>", methods=["POST"])
@login_required
def delete(id):
    p = Plant.query.get_or_404(id)
    try:
        db.session.delete(p)
        log_audit("delete", "plant", id, f"Deleted plant: {p.name}")
        db.session.commit()
        flash("Plant deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Cannot delete: {str(e)}", "danger")
    return redirect(url_for("plants.list"))


@plants_bp.route("/api")
@login_required
def api():
    plants = Plant.query.order_by(Plant.name).all()
    return jsonify([p.to_dict() for p in plants])
