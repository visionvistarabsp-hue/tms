from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required
from app import db
from app.models import Mine, Plant, WorkOrder
from app.utils import log_audit

mines_bp = Blueprint("mines", __name__, url_prefix="/mines")


@mines_bp.route("/")
@login_required
def list():
    plant_id = request.args.get("plant_id", "", type=str)
    query = Mine.query
    if plant_id:
        query = query.filter_by(plant_id=int(plant_id))
    mines = query.options(
        db.joinedload(Mine.work_orders)
    ).order_by(Mine.name).all()

    plant = None
    if plant_id:
        plant = Plant.query.get(int(plant_id))

    plants = Plant.query.order_by(Plant.name).all()
    return render_template("mines/list.html", mines=mines, plant=plant, plant_id=plant_id, plants=plants)


@mines_bp.route("/add", methods=["GET", "POST"])
@login_required
def add():
    plants = Plant.query.order_by(Plant.name).all()
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            plant_id = request.form.get("plant_id", "").strip()
            if not name:
                flash("Mine name is required", "danger")
                return render_template("mines/form.html", mine=None, plants=plants)
            if Mine.query.filter_by(name=name).first():
                flash("Mine with this name already exists", "danger")
                return render_template("mines/form.html", mine=None, plants=plants)
            m = Mine(name=name)
            if plant_id:
                m.plant_id = int(plant_id)
            db.session.add(m)
            db.session.flush()
            log_audit("create", "mine", m.id, f"Created mine: {m.name}")
            db.session.commit()
            flash("Mine added successfully", "success")
            return redirect(url_for("mines.view", id=m.id))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")
    return render_template("mines/form.html", mine=None, plants=plants)


@mines_bp.route("/edit/<int:id>", methods=["GET", "POST"])
@login_required
def edit(id):
    m = Mine.query.get_or_404(id)
    plants = Plant.query.order_by(Plant.name).all()
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            plant_id = request.form.get("plant_id", "").strip()
            if not name:
                flash("Mine name is required", "danger")
                return render_template("mines/form.html", mine=m, plants=plants)
            existing = Mine.query.filter(Mine.name == name, Mine.id != id).first()
            if existing:
                flash("Mine with this name already exists", "danger")
                return render_template("mines/form.html", mine=m, plants=plants)
            m.name = name
            m.plant_id = int(plant_id) if plant_id else None
            log_audit("update", "mine", m.id, f"Updated mine: {m.name}")
            db.session.commit()
            flash("Mine updated successfully", "success")
            return redirect(url_for("mines.list"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")
    return render_template("mines/form.html", mine=m, plants=plants)


@mines_bp.route("/delete/<int:id>", methods=["POST"])
@login_required
def delete(id):
    m = Mine.query.get_or_404(id)
    try:
        db.session.delete(m)
        log_audit("delete", "mine", id, f"Deleted mine: {m.name}")
        db.session.commit()
        flash("Mine deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Cannot delete: {str(e)}", "danger")
    return redirect(url_for("mines.list"))


@mines_bp.route("/<int:id>")
@login_required
def view(id):
    m = Mine.query.get_or_404(id)
    work_orders = (
        WorkOrder.query.filter_by(mine_id=m.id)
        .order_by(WorkOrder.date.desc())
        .all()
    )
    return render_template("mines/view.html", mine=m, work_orders=work_orders)


@mines_bp.route("/api")
@login_required
def api():
    mines = Mine.query.order_by(Mine.name).all()
    return jsonify([m.to_dict() for m in mines])
