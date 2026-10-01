from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required
from app import db
from app.models import Trip, Transporter, Plant, Mine
from app.utils import validate_positive, log_audit

trips_bp = Blueprint("trips", __name__, url_prefix="/trips")
PER_PAGE = 20


@trips_bp.route("/")
@login_required
def list():
    page = request.args.get("page", 1, type=int)
    query = Trip.query
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    lorry = request.args.get("lorry", "")
    plant_id = request.args.get("plant_id", "")
    status = request.args.get("status", "")
    if date_from:
        query = query.filter(Trip.date >= datetime.strptime(date_from, "%Y-%m-%d").date())
    if date_to:
        query = query.filter(Trip.date <= datetime.strptime(date_to, "%Y-%m-%d").date())
    if lorry:
        query = query.filter(Trip.lorry_number.ilike(f"%{lorry}%"))
    if plant_id:
        query = query.filter(Trip.plant_id == int(plant_id))
    if status:
        query = query.filter(Trip.status == status.upper())
    query = query.options(
        db.joinedload(Trip.transporter),
        db.joinedload(Trip.plant),
        db.joinedload(Trip.mine),
    )
    pagination = query.order_by(Trip.date.desc()).paginate(page=page, per_page=PER_PAGE, error_out=False)
    return render_template(
        "trips/list.html",
        trips=pagination.items,
        pagination=pagination,
        date_from=date_from,
        date_to=date_to,
        lorry=lorry,
        plant_id=plant_id,
        status=status,
        plants=Plant.query.order_by(Plant.name).all(),
    )


@trips_bp.route("/add", methods=["GET", "POST"])
@login_required
def add():
    transporters = Transporter.query.order_by(Transporter.name).all()
    plants = Plant.query.order_by(Plant.name).all()
    mines = Mine.query.order_by(Mine.name).all()
    if request.method == "POST":
        try:
            date_str = request.form.get("date", "").strip()
            lorry_number = request.form.get("lorry_number", "").strip()
            transporter_id = request.form.get("transporter_id")
            plant_id = request.form.get("plant_id")
            total_freight = 0
            tds_percent = 1

            if not date_str or not lorry_number or not transporter_id:
                flash("Date, Lorry Number, and Transporter are required", "danger")
                return render_template("trips/form.html", trip=None, transporters=transporters, plants=plants, mines=mines)

            trip_date = datetime.strptime(date_str, "%Y-%m-%d").date()

            existing = Trip.query.filter_by(date=trip_date, lorry_number=lorry_number).first()
            if existing:
                flash("A trip already exists for this lorry on this date", "danger")
                return render_template("trips/form.html", trip=None, transporters=transporters, plants=plants, mines=mines)

            mines_qty = request.form.get("mines_qty", "").strip()
            mine_id = request.form.get("mine_id")
            trip = Trip(
                date=trip_date,
                lorry_number=lorry_number,
                transporter_id=int(transporter_id),
                plant_id=int(plant_id) if plant_id else None,
                mine_id=int(mine_id) if mine_id else None,
                mines_qty=float(mines_qty) if mines_qty else None,
                total_freight=total_freight,
                tds_percent=tds_percent,
                remarks=request.form.get("remarks", "").strip(),
            )
            trip.recalculate()
            db.session.add(trip)
            db.session.flush()
            log_audit("create", "trip", trip.id, f"Created trip: {trip.lorry_number} on {trip.date}")
            db.session.commit()
            flash("Trip added successfully", "success")
            return redirect(url_for("trips.list"))
        except ValueError as e:
            flash(str(e), "danger")
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")
    return render_template("trips/form.html", trip=None, transporters=transporters, plants=plants, mines=mines)


@trips_bp.route("/edit/<int:id>", methods=["GET", "POST"])
@login_required
def edit(id):
    trip = Trip.query.get_or_404(id)
    transporters = Transporter.query.order_by(Transporter.name).all()
    plants = Plant.query.order_by(Plant.name).all()
    mines = Mine.query.order_by(Mine.name).all()
    if request.method == "POST":
        try:
            date_str = request.form.get("date", "").strip()
            lorry_number = request.form.get("lorry_number", "").strip()
            transporter_id = request.form.get("transporter_id")
            plant_id = request.form.get("plant_id")
            if not date_str or not lorry_number or not transporter_id:
                flash("Date, Lorry Number, and Transporter are required", "danger")
                return render_template("trips/form.html", trip=trip, transporters=transporters, plants=plants, mines=mines)

            trip_date = datetime.strptime(date_str, "%Y-%m-%d").date()

            existing = Trip.query.filter(
                Trip.date == trip_date, Trip.lorry_number == lorry_number, Trip.id != id
            ).first()
            if existing:
                flash("A trip already exists for this lorry on this date", "danger")
                return render_template("trips/form.html", trip=trip, transporters=transporters, plants=plants, mines=mines)

            mines_qty = request.form.get("mines_qty", "").strip()
            mine_id = request.form.get("mine_id")
            trip.date = trip_date
            trip.lorry_number = lorry_number
            trip.transporter_id = int(transporter_id)
            trip.plant_id = int(plant_id) if plant_id else None
            trip.mine_id = int(mine_id) if mine_id else None
            trip.mines_qty = float(mines_qty) if mines_qty else None
            trip.remarks = request.form.get("remarks", "").strip()
            trip.recalculate()
            log_audit("update", "trip", trip.id, f"Updated trip: {trip.lorry_number}")
            db.session.commit()
            flash("Trip updated successfully", "success")
            return redirect(url_for("trips.list"))
        except ValueError as e:
            flash(str(e), "danger")
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")
    return render_template("trips/form.html", trip=trip, transporters=transporters, plants=plants, mines=mines)


@trips_bp.route("/delete/<int:id>", methods=["POST"])
@login_required
def delete(id):
    trip = Trip.query.get_or_404(id)
    try:
        db.session.delete(trip)
        log_audit("delete", "trip", id, f"Deleted trip: {trip.lorry_number}")
        db.session.commit()
        flash("Trip deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {str(e)}", "danger")
    return redirect(url_for("trips.list"))


@trips_bp.route("/view/<int:id>")
@login_required
def view(id):
    trip = Trip.query.get_or_404(id)
    return render_template("trips/view.html", trip=trip)


@trips_bp.route("/api")
@login_required
def api():
    trips = Trip.query.options(
        db.joinedload(Trip.transporter),
        db.joinedload(Trip.plant),
        db.joinedload(Trip.mine),
    ).order_by(Trip.date.desc()).all()
    return jsonify([t.to_dict() for t in trips])
