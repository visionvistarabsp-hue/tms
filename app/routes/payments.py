from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required
from app import db
from app.models import Payment, Trip
from app.utils import validate_positive, log_audit

payments_bp = Blueprint("payments", __name__, url_prefix="/payments")


@payments_bp.route("/add/<int:trip_id>", methods=["GET", "POST"])
@login_required
def add(trip_id):
    trip = Trip.query.get_or_404(trip_id)
    if request.method == "POST":
        try:
            payment_method = request.form.get("payment_method", "").strip()
            amount = validate_positive(request.form.get("amount", 0), "Payment amount")
            execution_date_str = request.form.get("execution_date", "").strip()

            if payment_method not in ("NEFT", "RTGS", "CASH"):
                flash("Invalid payment method", "danger")
                return render_template("payments/form.html", trip=trip, payment=None)

            execution_date = datetime.strptime(execution_date_str, "%Y-%m-%d").date() if execution_date_str else datetime.utcnow().date()

            payment = Payment(
                trip_id=trip.id,
                payment_method=payment_method,
                amount=amount,
                execution_date=execution_date,
                beneficiary_name=request.form.get("beneficiary_name", "").strip(),
                account_number=request.form.get("account_number", "").strip(),
                ifsc_code=request.form.get("ifsc_code", "").strip(),
                reference_number=request.form.get("reference_number", "").strip(),
            )
            db.session.add(payment)
            trip.recalculate()
            db.session.flush()
            log_audit("create", "payment", payment.id, f"Payment of {amount} to trip {trip.id}")
            db.session.commit()
            flash("Payment added successfully", "success")
            return redirect(url_for("trips.view", id=trip.id))
        except ValueError as e:
            flash(str(e), "danger")
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")
    return render_template("payments/form.html", trip=trip, payment=None)


@payments_bp.route("/edit/<int:id>", methods=["GET", "POST"])
@login_required
def edit(id):
    payment = Payment.query.get_or_404(id)
    trip = payment.trip
    if request.method == "POST":
        try:
            payment_method = request.form.get("payment_method", "").strip()
            amount = validate_positive(request.form.get("amount", 0), "Payment amount")
            execution_date_str = request.form.get("execution_date", "").strip()

            if payment_method not in ("NEFT", "RTGS", "CASH"):
                flash("Invalid payment method", "danger")
                return render_template("payments/form.html", trip=trip, payment=payment)

            execution_date = datetime.strptime(execution_date_str, "%Y-%m-%d").date() if execution_date_str else payment.execution_date

            payment.payment_method = payment_method
            payment.amount = amount
            payment.execution_date = execution_date
            payment.beneficiary_name = request.form.get("beneficiary_name", "").strip()
            payment.account_number = request.form.get("account_number", "").strip()
            payment.ifsc_code = request.form.get("ifsc_code", "").strip()
            payment.reference_number = request.form.get("reference_number", "").strip()
            trip.recalculate()
            log_audit("update", "payment", payment.id, f"Updated payment for trip {trip.id}")
            db.session.commit()
            flash("Payment updated successfully", "success")
            return redirect(url_for("trips.view", id=trip.id))
        except ValueError as e:
            flash(str(e), "danger")
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")
    return render_template("payments/form.html", trip=trip, payment=payment)


@payments_bp.route("/delete/<int:id>", methods=["POST"])
@login_required
def delete(id):
    payment = Payment.query.get_or_404(id)
    trip = payment.trip
    try:
        db.session.delete(payment)
        trip.recalculate()
        log_audit("delete", "payment", id, f"Deleted payment from trip {trip.id}")
        db.session.commit()
        flash("Payment deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {str(e)}", "danger")
    return redirect(url_for("trips.view", id=trip.id))


@payments_bp.route("/api/<int:trip_id>")
@login_required
def api(trip_id):
    payments = Payment.query.filter_by(trip_id=trip_id).all()
    return jsonify([p.to_dict() for p in payments])
