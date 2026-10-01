from datetime import datetime
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required
from sqlalchemy import func
from app import db
from app.models import Trip, Transporter, Plant
from app.utils import generate_xlsx

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")


@reports_bp.route("/")
@login_required
def index():
    return render_template("reports/index.html")


@reports_bp.route("/api/payment-advice/trucks")
@login_required
def payment_advice_trucks():
    from app.models import WorkOrder, Transporter
    account = (request.args.get("account") or "").strip()
    if not account:
        return jsonify({"ok": True, "trucks": []})
    from app.routes.work_order_records import _transporter_by_truck

    def eff_name(w):
        if w.account_name and w.account_name.strip() and w.account_name.strip() != "-":
            return w.account_name.strip()
        tr = w.transporter or _transporter_by_truck(w.lorry_number)
        return tr.name.strip() if tr and tr.name and tr.name.strip() else None

    lorrys = set()
    for t in Transporter.query.all():
        if t.name and t.name.strip().lower() == account.lower():
            for trk in (t.trucks or []):
                if trk.lorry_number and trk.lorry_number.strip():
                    lorrys.add(trk.lorry_number.strip())
    for w in WorkOrder.query.options(db.joinedload(WorkOrder.transporter)).all():
        if (eff_name(w) or "").lower() == account.lower():
            if w.lorry_number and w.lorry_number.strip() and w.lorry_number.strip() != "-":
                lorrys.add(w.lorry_number.strip())
    return jsonify({"ok": True, "trucks": sorted(lorrys)})


@reports_bp.route("/api/payment-advice/dates")
@login_required
def payment_advice_dates():
    from app.models import WorkOrder
    account = (request.args.get("account") or "").strip()
    trucks = [t.strip() for t in request.args.get("trucks", "").split(",") if t.strip()]
    if not account:
        return jsonify({"ok": True, "dates": []})
    from app.routes.work_order_records import _transporter_by_truck

    def eff_name(w):
        if w.account_name and w.account_name.strip() and w.account_name.strip() != "-":
            return w.account_name.strip()
        tr = w.transporter or _transporter_by_truck(w.lorry_number)
        return tr.name.strip() if tr and tr.name and tr.name.strip() else None

    dates = set()
    for w in WorkOrder.query.options(db.joinedload(WorkOrder.transporter)).all():
        if (eff_name(w) or "").lower() != account.lower():
            continue
        if trucks and w.lorry_number.strip() not in trucks:
            continue
        if w.date:
            dates.add(w.date.isoformat())
    return jsonify({"ok": True, "dates": sorted(dates, reverse=True)})


@reports_bp.route("/payment-advice")
@login_required
def payment_advice():
    from app.models import WorkOrder, Transporter

    account = (request.args.get("account") or "").strip()
    trucks = [t.strip() for t in request.args.get("trucks", "").split(",") if t.strip()]
    dates = [d.strip() for d in request.args.get("dates", "").split(",") if d.strip()]

    accounts = set()
    for t in Transporter.query.all():
        if t.name and t.name.strip():
            accounts.add(t.name.strip())
    for (an,) in db.session.query(WorkOrder.account_name).distinct().all():
        if an and an.strip() and an.strip() != "-":
            accounts.add(an.strip())
    accounts = sorted(accounts, key=lambda s: s.lower())

    advice = None
    if account:
        from app.routes.work_order_records import _transporter_by_truck

        wos = WorkOrder.query.options(
            db.joinedload(WorkOrder.mine),
            db.joinedload(WorkOrder.transporter),
            db.joinedload(WorkOrder.petrol_stations),
        ).order_by(WorkOrder.date.asc(), WorkOrder.id).all()

        def eff_name(w):
            if w.account_name and w.account_name.strip() and w.account_name.strip() != "-":
                return w.account_name.strip()
            tr = w.transporter or _transporter_by_truck(w.lorry_number)
            return tr.name.strip() if tr and tr.name and tr.name.strip() else None

        rows = []
        for w in wos:
            if (eff_name(w) or "").lower() != account.lower():
                continue
            if trucks and w.lorry_number.strip() not in trucks:
                continue
            if dates and (w.date.isoformat() if w.date else "") not in dates:
                continue
            petrol = sum(float(ps.amount or 0) for ps in w.petrol_stations)
            rows.append({
                "id": w.id,
                "date": w.date,
                "lorry_number": w.lorry_number,
                "work_order_number": w.work_order_number or "-",
                "mine_name": w.mine.name if w.mine else "-",
                "ddtds_from": w.ddtds_from,
                "ddtds_to": w.ddtds_to,
                "rate": float(w.rate or 0),
                "mines_qty": float(w.mines_qty or 0),
                "plant_qty": float(w.plant_qty or 0),
                "shortage": float(w.shortage or 0),
                "freight": float(w.total_freight or 0),
                "petrol": petrol,
                "advance": float(w.total_advance or 0),
                "tds": float(w.tds or 0),
                "short_amt": float(w.short_amt or 0),
                "munsiyana": float(w.munsiyana or 0),
                "balance": float(w.balance or 0),
                "status": w.status,
            })

        if request.args.get("export") in ("csv", "xlsx"):
            headers = ["#", "Date", "Truck #", "Mine", "Mines Qty", "Plant Qty", "Rate",
                       "Freight", "Advance", "Short Qty", "Short Amt", "Munsiyana", "TDS", "Balance", "Status"]
            export_rows = [
                [i] + [
                    r["date"].isoformat() if r["date"] else "",
                    r["lorry_number"],
                    r["mine_name"],
                    r["mines_qty"], r["plant_qty"], r["rate"], r["freight"],
                    r["advance"], r["shortage"], r["short_amt"], r["munsiyana"], r["tds"], r["balance"],
                    r["status"],
                ]
                for i, r in enumerate(rows, start=1)
            ]
            return generate_xlsx(headers, export_rows, filename=f"payment_advice_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")

        totals = {
            "freight": round(sum(r["freight"] for r in rows), 2),
            "petrol": round(sum(r["petrol"] for r in rows), 2),
            "advance": round(sum(r["advance"] for r in rows), 2),
            "tds": round(sum(r["tds"] for r in rows), 2),
            "short_amt": round(sum(r["short_amt"] for r in rows), 2),
            "munsiyana": round(sum(r["munsiyana"] for r in rows), 2),
            "balance": round(sum(r["balance"] for r in rows), 2),
            "mines_qty": round(sum(r["mines_qty"] for r in rows), 2),
            "plant_qty": round(sum(r["plant_qty"] for r in rows), 2),
            "shortage": round(sum(r["shortage"] for r in rows), 2),
        }
        advice = {"account": account, "rows": rows, "totals": totals}

    return render_template(
        "reports/payment_advice.html",
        accounts=accounts,
        account=account,
        trucks=trucks,
        dates=dates,
        advice=advice,
        today=datetime.now().strftime("%d-%m-%Y"),
    )


@reports_bp.route("/trip-wise")
@login_required
def trip_wise():
    query = Trip.query
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    if date_from:
        query = query.filter(Trip.date >= datetime.strptime(date_from, "%Y-%m-%d").date())
    if date_to:
        query = query.filter(Trip.date <= datetime.strptime(date_to, "%Y-%m-%d").date())
    trips = query.options(
        db.joinedload(Trip.transporter),
        db.joinedload(Trip.plant),
        db.joinedload(Trip.mine),
    ).order_by(Trip.date.desc()).all()

    if request.args.get("export") in ("csv", "xlsx"):
        headers = ["Date", "Truck", "Work Order", "Mines Name", "Mines Qty", "Plant", "Transporter", "Freight", "TDS%", "TDS Amt", "Expense", "Paid", "Balance", "Status"]
        rows = [
            [
                t.date,
                t.lorry_number,
                t.mine.name if t.mine else "",
                t.mine.name if t.mine else "",
                float(t.mines_qty) if t.mines_qty else "",
                t.plant.name if t.plant else "",
                t.transporter.name if t.transporter else "",
                float(t.total_freight),
                float(t.tds_percent),
                float(t.tds_amount),
                float(t.total_expense),
                float(t.total_paid),
                float(t.balance),
                t.status,
            ]
            for t in trips
        ]
        return generate_xlsx(headers, rows)

    return render_template("reports/trip_wise.html", trips=trips, date_from=date_from, date_to=date_to)


@reports_bp.route("/transporter-wise")
@login_required
def transporter_wise():
    rows = (
        db.session.query(
            Transporter.id,
            Transporter.name,
            func.count(Trip.id),
            func.coalesce(func.sum(Trip.total_freight), 0),
            func.coalesce(func.sum(Trip.total_paid), 0),
            func.coalesce(func.sum(Trip.total_expense), 0),
            func.coalesce(func.sum(Trip.tds_amount), 0),
            func.coalesce(func.sum(Trip.balance), 0),
        )
        .join(Trip, Trip.transporter_id == Transporter.id)
        .group_by(Transporter.id, Transporter.name)
        .order_by(Transporter.name)
        .all()
    )
    data = [
        {
            "transporter": name,
            "trip_count": int(count),
            "total_freight": float(freight),
            "total_paid": float(paid),
            "total_expense": float(expense),
            "total_tds": float(tds),
            "total_balance": float(balance),
        }
        for _tid, name, count, freight, paid, expense, tds, balance in rows
    ]

    if request.args.get("export") in ("csv", "xlsx"):
        headers = ["Transporter", "Trips", "Freight", "Paid", "Expense", "TDS", "Balance"]
        rows_out = [
            [d["transporter"], d["trip_count"], d["total_freight"], d["total_paid"],
             d["total_expense"], d["total_tds"], d["total_balance"]]
            for d in data
        ]
        return generate_xlsx(headers, rows_out)

    return render_template("reports/transporter_wise.html", data=data)


@reports_bp.route("/date-wise")
@login_required
def date_wise():
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    q = db.session.query(
        Trip.date,
        func.count(Trip.id),
        func.coalesce(func.sum(Trip.total_freight), 0),
        func.coalesce(func.sum(Trip.total_paid), 0),
        func.coalesce(func.sum(Trip.total_expense), 0),
        func.coalesce(func.sum(Trip.tds_amount), 0),
        func.coalesce(func.sum(Trip.balance), 0),
    )
    if date_from:
        q = q.filter(Trip.date >= datetime.strptime(date_from, "%Y-%m-%d").date())
    if date_to:
        q = q.filter(Trip.date <= datetime.strptime(date_to, "%Y-%m-%d").date())
    rows = q.group_by(Trip.date).order_by(Trip.date.desc()).all()

    summary = [
        {
            "date": d.isoformat(),
            "trip_count": int(count),
            "total_freight": float(freight),
            "total_paid": float(paid),
            "total_expense": float(expense),
            "total_tds": float(tds),
            "total_balance": float(balance),
        }
        for d, count, freight, paid, expense, tds, balance in rows
    ]

    if request.args.get("export") in ("csv", "xlsx"):
        headers = ["Date", "Trips", "Freight", "Paid", "Expense", "TDS", "Balance"]
        rows_out = [
            [s["date"], s["trip_count"], s["total_freight"], s["total_paid"],
             s["total_expense"], s["total_tds"], s["total_balance"]]
            for s in summary
        ]
        return generate_xlsx(headers, rows_out)

    return render_template("reports/date_wise.html", summary=summary, date_from=date_from, date_to=date_to)


@reports_bp.route("/plant-wise")
@login_required
def plant_wise():
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")

    plants = Plant.query.order_by(Plant.name).all()
    plant_ids = {p.id for p in plants}

    q = Trip.query.filter(Trip.plant_id.isnot(None)).options(
        db.joinedload(Trip.mine),
        db.joinedload(Trip.transporter),
    )
    if date_from:
        q = q.filter(Trip.date >= datetime.strptime(date_from, "%Y-%m-%d").date())
    if date_to:
        q = q.filter(Trip.date <= datetime.strptime(date_to, "%Y-%m-%d").date())
    trips = q.order_by(Trip.date.desc()).all()

    by_plant = {}
    for t in trips:
        if t.plant_id not in plant_ids:
            continue
        by_plant.setdefault(t.plant_id, []).append(t)

    hierarchy = []
    for p in plants:
        ptrips = by_plant.get(p.id, [])
        if not ptrips:
            continue
        lorry_groups = {}
        for t in ptrips:
            lorry_groups.setdefault(t.lorry_number, []).append(t)

        lorry_data = []
        plant_freight = plant_paid = plant_expense = plant_tds = plant_balance = 0
        for lorry, lt in sorted(lorry_groups.items()):
            lorry_freight = sum(float(x.total_freight) for x in lt)
            lorry_paid = sum(float(x.total_paid) for x in lt)
            lorry_expense = sum(float(x.total_expense) for x in lt)
            lorry_tds = sum(float(x.tds_amount) for x in lt)
            lorry_balance = sum(float(x.balance) for x in lt)
            lorry_data.append({
                "lorry": lorry,
                "trips": lt,
                "trip_count": len(lt),
                "freight": lorry_freight,
                "paid": lorry_paid,
                "expense": lorry_expense,
                "tds": lorry_tds,
                "balance": lorry_balance,
            })
            plant_freight += lorry_freight
            plant_paid += lorry_paid
            plant_expense += lorry_expense
            plant_tds += lorry_tds
            plant_balance += lorry_balance

        hierarchy.append({
            "plant": p,
            "trip_count": len(ptrips),
            "freight": plant_freight,
            "paid": plant_paid,
            "expense": plant_expense,
            "tds": plant_tds,
            "balance": plant_balance,
            "lorries": lorry_data,
        })

    if request.args.get("export") in ("csv", "xlsx"):
        headers = ["Plant", "Truck", "Work Order", "Mines Name", "Mines Qty", "Date", "Transporter", "Freight", "TDS%", "TDS Amt", "Expense", "Paid", "Balance", "Status"]
        rows = []
        for h in hierarchy:
            for l in h["lorries"]:
                for t in l["trips"]:
                    rows.append([
                        h["plant"].name,
                        l["lorry"],
                        t.mine.name if t.mine else "",
                        t.mine.name if t.mine else "",
                        float(t.mines_qty) if t.mines_qty else "",
                        t.date,
                        t.transporter.name if t.transporter else "",
                        float(t.total_freight),
                        float(t.tds_percent),
                        float(t.tds_amount),
                        float(t.total_expense),
                        float(t.total_paid),
                        float(t.balance),
                        t.status,
                    ])
        return generate_xlsx(headers, rows)

    return render_template("reports/plant_wise.html", hierarchy=hierarchy, date_from=date_from, date_to=date_to)
