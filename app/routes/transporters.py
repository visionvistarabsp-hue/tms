from urllib.parse import urlsplit
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required
from app import db
from app.models import Transporter, TransporterTruck
from app.utils import log_audit

transporters_bp = Blueprint("transporters", __name__, url_prefix="/transporters")
PER_PAGE = 20


def _safe_redirect(target):
    """Accept only same-app absolute paths (e.g. /work-orders/view/240)."""
    if not target:
        return ""
    parts = urlsplit(target)
    if not parts.scheme and not parts.netloc and target.startswith("/"):
        return target
    return ""


@transporters_bp.route("/")
@login_required
def list():
    page = request.args.get("page", 1, type=int)
    query = Transporter.query
    search = request.args.get("search", "")
    if search:
        subq = (
            db.session.query(TransporterTruck.transporter_id)
            .filter(TransporterTruck.lorry_number.ilike(f"%{search}%"))
        )
        query = query.filter(
            db.or_(
                Transporter.name.ilike(f"%{search}%"),
                Transporter.id.in_(subq),
            )
        )
    pagination = query.order_by(Transporter.name).paginate(page=page, per_page=PER_PAGE, error_out=False)
    return render_template(
        "transporters/list.html",
        transporters=pagination.items,
        pagination=pagination,
        search=search,
    )


def _trucks_from_form():
    trucks = []
    seen = set()
    for key in request.form:
        if key.startswith("truck_"):
            val = request.form.get(key, "").strip().upper()
            if val and val not in seen:
                seen.add(val)
                trucks.append(val)
    return trucks


@transporters_bp.route("/add", methods=["GET", "POST"])
@login_required
def add():
    return_to = _safe_redirect(request.args.get("next") or request.form.get("next") or "")
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            if not name:
                flash("Transporter name is required", "danger")
                return render_template("transporters/form.html", transporter=None, prefill_name="", prefill_truck="", return_to=return_to)
            if Transporter.query.filter_by(name=name).first():
                flash("Transporter with this name already exists", "danger")
                return render_template("transporters/form.html", transporter=None, prefill_name="", prefill_truck="", return_to=return_to)
            tds_rate = float(request.form.get("tds_rate", 0) or 0)
            t = Transporter(
                name=name,
                pan_card=request.form.get("pan_card", "").strip().upper(),
                bank_account=request.form.get("bank_account", "").strip(),
                ifsc_code=request.form.get("ifsc_code", "").strip(),
                contact=request.form.get("contact", "").strip(),
                tds_rate=tds_rate,
            )
            for lorry in _trucks_from_form():
                t.trucks.append(TransporterTruck(lorry_number=lorry))
            db.session.add(t)
            db.session.flush()
            log_audit("create", "transporter", t.id, f"Created transporter: {t.name}")
            db.session.commit()
            flash("Transporter added successfully", "success")
            if return_to:
                return redirect(return_to)
            return redirect(url_for("transporters.list"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")
    return render_template("transporters/form.html", transporter=None, prefill_name=request.args.get("name", "").strip(), prefill_truck=request.args.get("truck", "").strip(), return_to=return_to)


@transporters_bp.route("/edit/<int:id>", methods=["GET", "POST"])
@login_required
def edit(id):
    t = Transporter.query.get_or_404(id)
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            if not name:
                flash("Transporter name is required", "danger")
                return render_template("transporters/form.html", transporter=t)
            existing = Transporter.query.filter(Transporter.name == name, Transporter.id != id).first()
            if existing:
                flash("Transporter with this name already exists", "danger")
                return render_template("transporters/form.html", transporter=t)
            t.name = name
            t.pan_card = request.form.get("pan_card", "").strip().upper()
            t.bank_account = request.form.get("bank_account", "").strip()
            t.ifsc_code = request.form.get("ifsc_code", "").strip()
            t.contact = request.form.get("contact", "").strip()
            t.tds_rate = float(request.form.get("tds_rate", 0) or 0)
            t.trucks = [TransporterTruck(lorry_number=lorry) for lorry in _trucks_from_form()]
            log_audit("update", "transporter", t.id, f"Updated transporter: {t.name}")
            db.session.commit()
            flash("Transporter updated successfully", "success")
            return redirect(url_for("transporters.list"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")
    return render_template("transporters/form.html", transporter=t)


@transporters_bp.route("/delete/<int:id>", methods=["POST"])
@login_required
def delete(id):
    t = Transporter.query.get_or_404(id)
    try:
        db.session.delete(t)
        log_audit("delete", "transporter", id, f"Deleted transporter: {t.name}")
        db.session.commit()
        flash("Transporter deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Cannot delete: {str(e)}", "danger")
    return redirect(url_for("transporters.list"))


@transporters_bp.route("/api")
@login_required
def api():
    transporters = Transporter.query.order_by(Transporter.name).all()
    return jsonify([t.to_dict() for t in transporters])
