from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required
from app import db
from app.models import Expense, Trip
from app.utils import validate_positive, log_audit

expenses_bp = Blueprint("expenses", __name__, url_prefix="/expenses")


@expenses_bp.route("/add/<int:trip_id>", methods=["GET", "POST"])
@login_required
def add(trip_id):
    trip = Trip.query.get_or_404(trip_id)
    if request.method == "POST":
        try:
            description = request.form.get("description", "").strip()
            amount = validate_positive(request.form.get("amount", 0), "Expense amount")
            if not description:
                flash("Description is required", "danger")
                return render_template("expenses/form.html", trip=trip, expense=None)

            total_freight = request.form.get("total_freight", "").strip()
            tds_percent = request.form.get("tds_percent", "").strip()
            if total_freight:
                trip.total_freight = float(total_freight)
            if tds_percent:
                trip.tds_percent = float(tds_percent)

            expense = Expense(trip_id=trip.id, description=description, amount=amount)
            db.session.add(expense)
            trip.recalculate()
            db.session.flush()
            log_audit("create", "expense", expense.id, f"Added expense to trip {trip.id}: {amount}")
            db.session.commit()
            flash("Expense added successfully", "success")
            return redirect(url_for("trips.view", id=trip.id))
        except ValueError as e:
            flash(str(e), "danger")
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")
    return render_template("expenses/form.html", trip=trip, expense=None)


@expenses_bp.route("/edit/<int:id>", methods=["GET", "POST"])
@login_required
def edit(id):
    expense = Expense.query.get_or_404(id)
    trip = expense.trip
    if request.method == "POST":
        try:
            description = request.form.get("description", "").strip()
            amount = validate_positive(request.form.get("amount", 0), "Expense amount")
            if not description:
                flash("Description is required", "danger")
                return render_template("expenses/form.html", trip=trip, expense=expense)

            total_freight = request.form.get("total_freight", "").strip()
            tds_percent = request.form.get("tds_percent", "").strip()
            if total_freight:
                trip.total_freight = float(total_freight)
            if tds_percent:
                trip.tds_percent = float(tds_percent)

            expense.description = description
            expense.amount = amount
            trip.recalculate()
            log_audit("update", "expense", expense.id, f"Updated expense for trip {trip.id}")
            db.session.commit()
            flash("Expense updated successfully", "success")
            return redirect(url_for("trips.view", id=trip.id))
        except ValueError as e:
            flash(str(e), "danger")
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")
    return render_template("expenses/form.html", trip=trip, expense=expense)


@expenses_bp.route("/delete/<int:id>", methods=["POST"])
@login_required
def delete(id):
    expense = Expense.query.get_or_404(id)
    trip = expense.trip
    try:
        db.session.delete(expense)
        trip.recalculate()
        log_audit("delete", "expense", id, f"Deleted expense from trip {trip.id}")
        db.session.commit()
        flash("Expense deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {str(e)}", "danger")
    return redirect(url_for("trips.view", id=trip.id))


@expenses_bp.route("/api/<int:trip_id>")
@login_required
def api(trip_id):
    expenses = Expense.query.filter_by(trip_id=trip_id).all()
    return jsonify([e.to_dict() for e in expenses])
