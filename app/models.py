from datetime import date, datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from flask import current_app
from sqlalchemy.types import TypeDecorator

from app import db


class UpperString(TypeDecorator):
    impl = db.String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return value.upper() if isinstance(value, str) else value

    def process_result_value(self, value, dialect):
        return value.upper() if isinstance(value, str) else value


class UpperText(TypeDecorator):
    impl = db.Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return value.upper() if isinstance(value, str) else value

    def process_result_value(self, value, dialect):
        return value.upper() if isinstance(value, str) else value


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Transporter(db.Model):
    __tablename__ = "transporters"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(UpperString(200), nullable=False, index=True)
    pan_card = db.Column(UpperString(20), nullable=True)
    bank_account = db.Column(UpperString(50), nullable=True)
    ifsc_code = db.Column(UpperString(20), nullable=True)
    contact = db.Column(UpperString(20), nullable=True)
    tds_rate = db.Column(db.Numeric(5, 2), default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    trips = db.relationship("Trip", backref="transporter", lazy=True)
    trucks = db.relationship(
        "TransporterTruck",
        backref="transporter",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="TransporterTruck.lorry_number",
    )

    def __repr__(self):
        return f"<Transporter {self.name} (TDS: {self.tds_rate}%)>"

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "pan_card": self.pan_card,
            "bank_account": self.bank_account,
            "ifsc_code": self.ifsc_code,
            "contact": self.contact,
            "tds_rate": float(self.tds_rate),
            "trucks": [t.lorry_number for t in (self.trucks or [])],
        }


class TransporterTruck(db.Model):
    __tablename__ = "transporter_trucks"
    id = db.Column(db.Integer, primary_key=True)
    transporter_id = db.Column(db.Integer, db.ForeignKey("transporters.id"), nullable=False, index=True)
    lorry_number = db.Column(UpperString(50), nullable=False, index=True)


class Trip(db.Model):
    __tablename__ = "trips"
    __table_args__ = (
        db.UniqueConstraint("date", "lorry_number", name="uq_trip_date_lorry"),
        db.Index("ix_trip_date", "date"),
        db.Index("ix_trip_transporter", "transporter_id"),
        db.Index("ix_trip_plant", "plant_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)
    lorry_number = db.Column(UpperString(50), nullable=False)
    transporter_id = db.Column(db.Integer, db.ForeignKey("transporters.id"), nullable=False, index=True)
    plant_id = db.Column(db.Integer, db.ForeignKey("plants.id"), nullable=True, index=True)
    total_freight = db.Column(db.Numeric(12, 2), nullable=False, default=0.00)
    tds_percent = db.Column(db.Numeric(5, 2), nullable=False, default=1.00)
    tds_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0.00)
    total_expense = db.Column(db.Numeric(12, 2), nullable=False, default=0.00)
    total_paid = db.Column(db.Numeric(12, 2), nullable=False, default=0.00)
    balance = db.Column(db.Numeric(12, 2), nullable=False, default=0.00)
    mines_name = db.Column(UpperString(200), nullable=True)
    mine_id = db.Column(db.Integer, db.ForeignKey("mines.id"), nullable=True, index=True)
    mines_qty = db.Column(db.Numeric(12, 2), nullable=True, default=0.00)
    status = db.Column(UpperString(20), nullable=False, default="Pending")
    remarks = db.Column(UpperText, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    expenses = db.relationship("Expense", backref="trip", lazy=True, cascade="all, delete-orphan")
    payments = db.relationship("Payment", backref="trip", lazy=True, cascade="all, delete-orphan")

    def recalculate(self):
        self.tds_amount = round(float(self.total_freight) * float(self.tds_percent) / 100, 2)
        self.total_expense = sum(float(e.amount) for e in self.expenses) if self.expenses else 0
        self.total_paid = sum(float(p.amount) for p in self.payments) if self.payments else 0
        self.balance = round(
            float(self.total_freight) - float(self.total_paid) - float(self.total_expense) - float(self.tds_amount),
            2,
        )
        self.status = "COMPLETED" if self.balance <= 0 else "PENDING"

    def to_dict(self):
        return {
            "id": self.id,
            "date": self.date.isoformat() if self.date else None,
            "lorry_number": self.lorry_number,
            "transporter_id": self.transporter_id,
            "transporter_name": self.transporter.name if self.transporter else "",
            "plant_id": self.plant_id,
            "plant_name": self.plant.name if self.plant else "",
            "total_freight": float(self.total_freight),
            "tds_percent": float(self.tds_percent),
            "tds_amount": float(self.tds_amount),
            "total_expense": float(self.total_expense),
            "total_paid": float(self.total_paid),
            "balance": float(self.balance),
            "mine_id": self.mine_id,
            "mine_name": self.mine.name if self.mine else "",
            "mines_qty": float(self.mines_qty) if self.mines_qty else 0,
            "status": self.status,
            "remarks": self.remarks,
        }


class Expense(db.Model):
    __tablename__ = "expenses"
    id = db.Column(db.Integer, primary_key=True)
    trip_id = db.Column(db.Integer, db.ForeignKey("trips.id"), nullable=False, index=True)
    description = db.Column(UpperString(300), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "trip_id": self.trip_id,
            "description": self.description,
            "amount": float(self.amount),
        }


class Payment(db.Model):
    __tablename__ = "payments"
    id = db.Column(db.Integer, primary_key=True)
    trip_id = db.Column(db.Integer, db.ForeignKey("trips.id"), nullable=False, index=True)
    payment_method = db.Column(UpperString(10), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    execution_date = db.Column(db.Date, nullable=False)
    beneficiary_name = db.Column(UpperString(200), nullable=True)
    account_number = db.Column(UpperString(50), nullable=True)
    ifsc_code = db.Column(UpperString(20), nullable=True)
    reference_number = db.Column(UpperString(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "trip_id": self.trip_id,
            "payment_method": self.payment_method,
            "amount": float(self.amount),
            "execution_date": self.execution_date.isoformat() if self.execution_date else None,
            "beneficiary_name": self.beneficiary_name,
            "account_number": self.account_number,
            "ifsc_code": self.ifsc_code,
            "reference_number": self.reference_number,
        }


class Plant(db.Model):
    __tablename__ = "plants"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(UpperString(200), nullable=False, unique=True, index=True)
    location = db.Column(UpperString(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    trips = db.relationship("Trip", backref="plant", lazy=True)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "location": self.location,
        }


class Mine(db.Model):
    __tablename__ = "mines"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(UpperString(200), nullable=False, unique=True, index=True)
    plant_id = db.Column(db.Integer, db.ForeignKey("plants.id"), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    plant = db.relationship("Plant", backref="mines")
    trips = db.relationship("Trip", backref="mine", lazy=True)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "plant_id": self.plant_id,
            "plant_name": self.plant.name if self.plant else "",
        }


def calculate_tds(work_order, transporter=None):
    """
    Calculate TDS for a work order.

    Rules (based on the DD period start date, ddtds_from):
    - DD period starts in the current financial year (April 1 onward, e.g.
      FY 2026-27) → TDS = 0
    - DD period starts before the current financial year (e.g. Jan-Mar 2026,
      all of 2025, 2023-2025) → TDS = Freight × tds_percent%
    """
    from datetime import date

    dd_from = work_order.ddtds_from
    dd_to = work_order.ddtds_to
    tds_percent = float(work_order.tds_percent or 0)
    freight = float(work_order.total_freight or 0)

    if not dd_from or not dd_to or tds_percent <= 0:
        return 0

    today = date.today()
    current_year = today.year if today.month >= 4 else today.year - 1
    fy_start = date(current_year, 4, 1)

    if dd_from >= fy_start:
        return 0

    return round(freight * (tds_percent / 100), 2)


class WorkOrder(db.Model):
    __tablename__ = "work_orders"
    id = db.Column(db.Integer, primary_key=True)
    work_order_number = db.Column(UpperString(50), nullable=True, index=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("work_orders.id"), nullable=True, index=True)
    date = db.Column(db.Date, nullable=False, index=True)
    lorry_number = db.Column(UpperString(50), nullable=False, index=True)
    mine_id = db.Column(db.Integer, db.ForeignKey("mines.id"), nullable=True, index=True)
    transporter_id = db.Column(db.Integer, db.ForeignKey("transporters.id"), nullable=True, index=True)
    tds = db.Column(db.Numeric(12, 2), nullable=False, default=0.00)
    tds_auto = db.Column(db.Boolean, default=True, nullable=False)
    tds_percent = db.Column(db.Numeric(5, 2), nullable=False, default=1.00)
    ddtds = db.Column(db.Date, nullable=True)
    ddtds_from = db.Column(db.Date, nullable=True)
    ddtds_to = db.Column(db.Date, nullable=True)
    account_advance = db.Column(db.Numeric(12, 2), nullable=False, default=0.00)
    mines_qty = db.Column(db.Numeric(14, 4), nullable=True, default=0.00)
    plant_qty = db.Column(db.Numeric(14, 4), nullable=True, default=0.00)
    rate = db.Column(db.Numeric(12, 2), nullable=False, default=0.00)
    total_freight = db.Column(db.Numeric(12, 2), nullable=False, default=0.00)
    cash = db.Column(db.Numeric(12, 2), nullable=False, default=0.00)
    loading = db.Column(db.Numeric(12, 2), nullable=False, default=0.00)
    total_advance = db.Column(db.Numeric(12, 2), nullable=False, default=0.00)
    shortage = db.Column(db.Numeric(14, 4), nullable=False, default=0.00)
    short_amt = db.Column(db.Numeric(12, 2), nullable=False, default=0.00)
    short_rate = db.Column(db.Numeric(12, 2), nullable=False, default=10000.00)
    munsiyana = db.Column(db.Numeric(12, 2), nullable=False, default=300.00)
    balance = db.Column(db.Numeric(12, 2), nullable=False, default=0.00)
    account_name = db.Column(UpperString(200), nullable=True, index=True)
    payable_date = db.Column(db.Date, nullable=True)
    remark = db.Column(UpperText, nullable=True)
    status = db.Column(UpperString(20), nullable=False, default="Pending", index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    mine = db.relationship("Mine", backref="work_orders")
    transporter = db.relationship("Transporter", backref="work_orders")
    petrol_stations = db.relationship("PetrolStation", backref="work_order", lazy=True, cascade="all, delete-orphan")
    parent = db.relationship("WorkOrder", remote_side=[id], backref="children")

    def recalculate(self):
        from app.models import calculate_tds

        self.total_freight = round(float(self.plant_qty or 0) * float(self.rate or 0), 2)
        self.shortage = round(max(0.0, float(self.mines_qty or 0) - float(self.plant_qty or 0)), 4)
        self.short_amt = round(float(self.shortage or 0) * float(self.short_rate or 10000), 2)
        petrol_total = sum(float(ps.amount or 0) for ps in self.petrol_stations)
        self.total_advance = round(float(self.cash or 0) + petrol_total + float(self.loading or 0), 2)

        if self.tds_auto:
            self.tds = calculate_tds(self)

        temp = (
            float(self.total_advance or 0)
            + float(self.short_amt or 0)
            + float(self.munsiyana or 0)
            + float(self.tds or 0)
            + float(self.account_advance or 0)
        )
        self.balance = round(float(self.total_freight) - temp, 2)
        self.status = "COMPLETED" if (self.payable_date or self.balance <= 0) else "PENDING"


class PetrolStation(db.Model):
    __tablename__ = "petrol_stations"
    id = db.Column(db.Integer, primary_key=True)
    work_order_id = db.Column(db.Integer, db.ForeignKey("work_orders.id"), nullable=False, index=True)
    slot = db.Column(db.Integer, nullable=False, default=0, index=True)
    name = db.Column(UpperString(200), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False, default=0.00)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    action = db.Column(UpperString(100), nullable=False)
    entity_type = db.Column(UpperString(50), nullable=False)
    entity_id = db.Column(db.Integer, nullable=True)
    details = db.Column(UpperText, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
