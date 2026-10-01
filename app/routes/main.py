from flask import Blueprint, render_template
from flask_login import login_required
from sqlalchemy import func, case, extract
from datetime import datetime, timedelta
from app import db
from app.models import Trip, Plant, Mine, WorkOrder

main_bp = Blueprint("main", __name__)


def detect_anomalies():
    anomalies = []
    thirty_days_ago = datetime.today().date() - timedelta(days=30)
    recent = WorkOrder.query.filter(WorkOrder.date >= thirty_days_ago).all()

    if not recent:
        return anomalies

    truck_rates = {}
    for wo in recent:
        if wo.lorry_number and wo.rate and float(wo.rate) > 0:
            truck_rates.setdefault(wo.lorry_number, []).append(float(wo.rate))

    truck_avg_rate = {}
    for truck, rates in truck_rates.items():
        truck_avg_rate[truck] = sum(rates) / len(rates)

    seen = {}
    for wo in recent:
        key = (wo.lorry_number, str(wo.date), wo.mine_id)
        if key in seen:
            seen[key].append(wo)
        else:
            seen[key] = [wo]

    for key, group in seen.items():
        if len(group) > 1:
            anomalies.append({
                "type": "duplicate",
                "severity": "danger",
                "icon": "bi-copy",
                "message": f"Duplicate entry: {key[0]} on {key[1]} — {len(group)} identical records found",
                "wo_ids": [w.id for w in group],
            })

    for wo in recent:
        if wo.lorry_number and wo.rate and float(wo.rate) > 0 and wo.lorry_number in truck_avg_rate:
            avg = truck_avg_rate[wo.lorry_number]
            rate = float(wo.rate)
            if avg > 0 and abs(rate - avg) / avg > 0.5:
                anomalies.append({
                    "type": "rate_anomaly",
                    "severity": "warning",
                    "icon": "bi-graph-up-arrow",
                    "message": f"Rate anomaly: {wo.lorry_number} has ₹{rate:,.0f}/T vs avg ₹{avg:,.0f}/T ({((rate - avg) / avg * 100):+.0f}% deviation)",
                    "wo_ids": [wo.id],
                })

    for wo in recent:
        zero_fields = []
        if wo.rate and float(wo.rate) == 0:
            zero_fields.append("Rate")
        if wo.mines_qty and float(wo.mines_qty) == 0:
            zero_fields.append("Qty")
        if wo.total_freight and float(wo.total_freight) == 0 and float(wo.rate or 0) > 0:
            zero_fields.append("Freight")
        if zero_fields:
            anomalies.append({
                "type": "zero_data",
                "severity": "info",
                "icon": "bi-exclamation-circle",
                "message": f"Missing data: {wo.lorry_number} (#{wo.id}) — {', '.join(zero_fields)} is zero",
                "wo_ids": [wo.id],
            })

    for wo in recent:
        freight = float(wo.total_freight or 0)
        bal = float(wo.balance or 0)
        if freight > 0 and bal > freight * 0.8 and wo.status != "COMPLETED":
            anomalies.append({
                "type": "high_balance",
                "severity": "warning",
                "icon": "bi-wallet2",
                "message": f"Unpaid: {wo.lorry_number} (#{wo.id}) — ₹{bal:,.0f} balance on ₹{freight:,.0f} freight ({(bal / freight * 100):.0f}%)",
                "wo_ids": [wo.id],
            })

    severity_order = {"danger": 0, "warning": 1, "info": 2}
    anomalies.sort(key=lambda x: severity_order.get(x["severity"], 3))

    return anomalies[:20]


@main_bp.route("/")
@login_required
def dashboard():
    wo_totals = db.session.query(
        func.coalesce(func.sum(WorkOrder.total_freight), 0),
        func.coalesce(func.sum(WorkOrder.cash + WorkOrder.loading + WorkOrder.total_advance), 0),
        func.coalesce(func.sum(WorkOrder.balance), 0),
        func.count(WorkOrder.id),
        func.coalesce(func.sum(case((WorkOrder.status == "PENDING", 1), else_=0)), 0),
        func.coalesce(func.sum(case((WorkOrder.status == "COMPLETED", 1), else_=0)), 0),
        func.coalesce(func.sum(WorkOrder.tds), 0),
    ).one()
    total_freight, total_paid, total_balance, total_wos, pending_wos, completed_wos, total_tds = [
        float(x) for x in wo_totals
    ]

    mine_rows = db.session.query(
        Mine.plant_id,
        func.count(WorkOrder.id),
        func.coalesce(func.sum(WorkOrder.total_freight), 0),
        func.coalesce(func.sum(WorkOrder.tds), 0),
        func.coalesce(func.sum(WorkOrder.cash + WorkOrder.loading + WorkOrder.total_advance), 0),
        func.coalesce(func.sum(WorkOrder.balance), 0),
        func.coalesce(func.sum(case((WorkOrder.status == "PENDING", 1), else_=0)), 0),
        func.coalesce(func.sum(case((WorkOrder.status == "COMPLETED", 1), else_=0)), 0),
        func.coalesce(func.sum(func.coalesce(WorkOrder.mines_qty, 0)), 0),
    ).join(WorkOrder, WorkOrder.mine_id == Mine.id).filter(Mine.plant_id.isnot(None)).group_by(Mine.plant_id).all()

    by_plant = {}
    for pid, count, freight, tds, paid, balance, pending, completed, qty in mine_rows:
        by_plant[int(pid)] = {
            "count": int(count),
            "freight": float(freight),
            "tds": float(tds),
            "paid": float(paid),
            "balance": float(balance),
            "pending": int(pending),
            "completed": int(completed),
            "mines_qty": float(qty),
        }

    plant_data = []
    for p in Plant.query.order_by(Plant.name).all():
        d = by_plant.get(p.id, {
            "count": 0, "freight": 0.0, "tds": 0.0,
            "paid": 0.0, "balance": 0.0, "pending": 0, "completed": 0,
            "mines_qty": 0.0,
        })
        d["id"] = p.id
        d["name"] = p.name
        plant_data.append(d)

    recent_wos = WorkOrder.query.options(
        db.joinedload(WorkOrder.mine)
    ).order_by(WorkOrder.date.desc()).limit(8).all()

    anomalies = detect_anomalies()

    collection_rate = (
        round(total_freight / (total_freight + total_balance) * 100, 1)
        if (total_freight > 0 and (total_freight + total_balance) > 0)
        else 0
    )

    return render_template(
        "dashboard.html",
        total_freight=total_freight,
        total_paid=total_paid,
        total_balance=total_balance,
        total_tds=total_tds,
        pending_wos=int(pending_wos),
        completed_wos=int(completed_wos),
        total_wos=int(total_wos),
        recent_wos=recent_wos,
        plant_data=plant_data,
        now=datetime.now(),
        anomalies=anomalies,
        collection_rate=collection_rate,
    )
