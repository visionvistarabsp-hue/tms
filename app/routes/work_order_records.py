import csv, io, os, json, logging
import requests
from datetime import datetime, date as _date
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, Response
from flask_login import login_required
from app import db
from app.models import WorkOrder, Mine, PetrolStation, Plant, Transporter, TransporterTruck
from app.utils import log_audit, generate_xlsx


def _parse_any_date(value):
    """Parse a date from ANY common input shape into a datetime.date().

    Never raises on format mismatch (returns None), so imports never die with
    "time data ... does not match format %Y-%m-%d". Handles:
      - datetime/date objects         (2026-08-01, 2026-08-01 00:00:00)
      - ISO strings                   (2026-08-01, 2026-08-01T00:00:00)
      - slash / dash / Belgian formats(DD-MM-YYYY, DD/MM/YYYY, YYYY/MM/DD)
      - Excel serial numbers          (float/int days since 1899-12-30)
      - full datetimes with timezone text
      - none/empty
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, _date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        serial = float(value)
        if 1 <= serial <= 80000:
            try:
                return (_datetime(1899, 12, 30) + timedelta(days=serial)).date()
            except Exception:
                return None
        return None
    s = str(value).strip().replace("Z", "").replace("z", "")
    if not s:
        return None

    formats = [
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M",
        "%Y/%m/%d",
        "%Y/%m/%d %H:%M:%S",
        "%d-%m-%Y",
        "%d-%m-%Y %H:%M:%S",
        "%d/%m/%Y",
        "%d/%m/%Y %H:%M:%S",
        "%m/%d/%Y",
        "%d.%m.%Y",
        "%d %b %Y",
        "%d %B %Y",
        "%d-%b-%Y",
        "%d-%B-%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue

    t = s.split()
    if len(t) >= 1:
        tok = t[0]
        for sep in ("-", "/", "."):
            parts = tok.split(sep)
            if len(parts) == 3 and all(p.isdigit() for p in parts):
                a, b, c = (int(p) for p in parts)
                try:
                    if parts[0].isdigit() and len(parts[0]) == 4:  # YYYY-first
                        return _date(a, b, c)
                    # DD-first (common for the AUG.AMADAD-style xlsx)
                    if a <= 99:
                        a += 2000
                    return _date(c, b, a)
                except Exception:
                    continue
    return None


def _groq_api_key():
    """Resolve the Groq API key regardless of env-var spelling (GROQ/GORQ, case, dashes/underscores).

    Groq key values always start with 'gsk_', so if a normal os.environ lookup fails we scan
    all loaded env vars (case-insensitive) for one whose value begins with 'gsk_'.
    """
    for name in ("GROQ_API_KEY", "GROQ_API_key", "GORQ_API_KEY", "GORQ_API_key", "GORQ-API", "GROQ_KEY", "GROQ"):
        v = os.environ.get(name)
        if v:
            return v
    lowered = {k.lower(): k for k in os.environ}
    for name in lowered:
        if "gsk_" in lowered[name]:
            return os.environ[lowered[name]]
    matched = {k: os.environ[k] for k in os.environ if os.environ[k].strip().startswith("gsk_")}
    if matched:
        return next(iter(matched.values()))
    return None




work_order_fields = [
    ("date", "Date", "date"),
    ("lorry_number", "Truck #", "text"),
    ("work_order_number", "WO Number", "text"),
    ("mine_name", "Mine Name", "text"),
    ("tds", "TDS", "number"),
    ("ddtds_from", "DD TDS From", "date"),
    ("ddtds_to", "DD TDS To", "date"),
    ("account_advance", "Acc Adv", "number"),
    ("mines_qty", "Mines Qty", "number"),
    ("plant_qty", "Plant Qty", "number"),
    ("rate", "Rate", "number"),
    ("total_freight", "Freight", "computed"),
    ("cash", "Cash", "number"),
    ("loading", "Loading", "number"),
    ("petrol", "Petrol", "text"),
    ("total_advance", "Advance", "computed"),
    ("shortage", "Short", "computed"),
    ("short_amt", "Short Amt", "number"),
    ("munsiyana", "Munsiyana", "number"),
    ("balance", "Balance", "computed"),
    ("status", "Status", "text"),
    ("remark", "Remark", "text"),
    ("account_name", "Account Name", "text"),
]

work_order_records_bp = Blueprint("work_order_records", __name__, url_prefix="/work-orders")

PER_PAGE = 20


def _work_order_family(wo):
    """Return the whole work order group: a flat list containing the work order
    itself, plus (if it is a parent) all its child entry rows."""
    family = [wo]
    children = WorkOrder.query.filter(WorkOrder.parent_id == wo.id).all()
    family.extend(children)
    return family


def _transporter_by_truck(lorry):
    """Find the transporter whose truck list contains the given lorry number
    (case- and space-insensitive). Returns the Transporter or None."""
    if not lorry:
        return None
    from app.models import TransporterTruck

    key = "".join(str(lorry).strip().upper().split())
    for tt in TransporterTruck.query.options(db.joinedload(TransporterTruck.transporter)).all():
        stored = "".join((tt.lorry_number or "").strip().upper().split())
        if stored == key:
            return tt.transporter
    return None


@work_order_records_bp.route("/quick-add", methods=["POST"])
@login_required
def quick_add():
    data = request.get_json()
    mine_id = data.get("mine_id")
    work_order_number = data.get("work_order_number", "").strip()
    if not mine_id:
        return jsonify({"ok": False, "error": "Mine is required"}), 400
    try:
        wo = WorkOrder(
            work_order_number=work_order_number or None,
            mine_id=int(mine_id),
            date=datetime.utcnow().date(),
            lorry_number="-",
            tds_percent=1.0,
        )
        wo.recalculate()
        db.session.add(wo)
        db.session.flush()
        log_audit("create", "work_order", wo.id, f"Quick created work order: {wo.work_order_number}")
        db.session.commit()
        return jsonify({"ok": True, "id": wo.id, "work_order_number": wo.work_order_number})
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


@work_order_records_bp.route("/add-entry/<int:parent_id>", methods=["POST"])
@login_required
def add_entry(parent_id):
    parent = WorkOrder.query.get_or_404(parent_id)
    try:
        entry = WorkOrder(
            parent_id=parent.id,
            work_order_number=parent.work_order_number,
            mine_id=parent.mine_id,
            date=datetime.utcnow().date(),
            lorry_number="-",
            munsiyana=parent.munsiyana if parent.munsiyana else 300,
            tds_percent=parent.tds_percent if parent.tds_percent else 1.0,
        )
        entry.recalculate()
        db.session.add(entry)
        db.session.flush()
        db.session.commit()
        return jsonify({"ok": True, "id": entry.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


@work_order_records_bp.route("/")
@login_required
def work_orders():
    page = request.args.get("page", 1, type=int)
    mine_id = request.args.get("mine_id", "", type=str)
    query = WorkOrder.query.filter(WorkOrder.parent_id.is_(None))
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    lorry = request.args.get("lorry", "")
    status = request.args.get("status", "")
    account = request.args.get("account", "")
    if mine_id:
        query = query.filter_by(mine_id=int(mine_id))
    if date_from:
        query = query.filter(WorkOrder.date >= datetime.strptime(date_from, "%Y-%m-%d").date())
    if date_to:
        query = query.filter(WorkOrder.date <= datetime.strptime(date_to, "%Y-%m-%d").date())
    if lorry:
        query = query.filter(WorkOrder.lorry_number.ilike(f"%{lorry}%"))
    if status:
        query = query.filter(WorkOrder.status == status.upper())
    if account:
        query = query.filter(WorkOrder.account_name.ilike(f"%{account}%"))

    if request.args.get("export") in ("csv", "xlsx"):
        all_work_orders = query.order_by(WorkOrder.date.desc()).all()
        headers = ["WO #", "Date", "Truck", "Mine", "TDS", "DD TDS From", "DD TDS To", "Acc Adv", "Mines Qty", "Plant Qty", "Rate", "Freight", "Cash", "Loading", "Petrol", "Advance", "Short", "Short Amt", "Munsiyana", "Balance", "Status", "Remark", "Account"]
        rows = [
            [
                wo.work_order_number or "",
                wo.date,
                wo.lorry_number,
                wo.mine.name if wo.mine else "",
                float(wo.tds or 0), wo.ddtds_from.isoformat() if wo.ddtds_from else "", wo.ddtds_to.isoformat() if wo.ddtds_to else "", float(wo.account_advance or 0),
                float(wo.mines_qty) if wo.mines_qty else "",
                float(wo.plant_qty) if wo.plant_qty else "",
                float(wo.rate or 0), float(wo.total_freight or 0),
                float(wo.cash or 0), float(wo.loading or 0),
                sum(float(ps.amount or 0) for ps in wo.petrol_stations),
                float(wo.total_advance or 0),
                float(wo.shortage or 0),
                float(wo.short_amt or 0), float(wo.munsiyana or 0),
                float(wo.balance or 0), wo.status, wo.remark or "", wo.account_name or "",
            ]
            for wo in all_work_orders
        ]
        return generate_xlsx(headers, rows)

    query = query.options(
        db.joinedload(WorkOrder.mine).joinedload(Mine.plant),
        db.selectinload(WorkOrder.petrol_stations),
    )
    all_work_orders = query.order_by(WorkOrder.mine_id.asc(), WorkOrder.date.desc(), WorkOrder.id.desc()).all()

    seen_wo_numbers = {}
    deduped = []
    for wo in all_work_orders:
        key = (wo.mine_id, wo.work_order_number or str(wo.id))
        if key in seen_wo_numbers:
            continue
        seen_wo_numbers[key] = True
        deduped.append(wo)
    all_work_orders = deduped

    mine_groups = {}
    for wo in all_work_orders:
        key = wo.mine_id or 0
        if key not in mine_groups:
            mine = wo.mine
            plant = mine.plant if mine else None
            mine_groups[key] = {
                "mine": mine,
                "mine_name": mine.name if mine else "Unassigned",
                "plant_name": plant.name if plant else "",
                "work_orders": [],
            }
        mine_groups[key]["work_orders"].append(wo)

    grouped = list(mine_groups.values())
    total_count = len(all_work_orders)

    mine = None
    if mine_id:
        mine = Mine.query.get(int(mine_id))

    return render_template(
        "work_order_records/list.html",
        grouped=grouped,
        total_count=total_count,
        date_from=date_from,
        date_to=date_to,
        lorry=lorry,
        status=status,
        account=account,
        mine_id=mine_id,
        mine=mine,
    )


@work_order_records_bp.route("/add", methods=["GET", "POST"])
@login_required
def add():
    mines = Mine.query.order_by(Mine.name).all()
    default_mine_id = request.args.get("mine_id", type=int)
    if request.method == "POST":
        try:
            work_order_number = request.form.get("work_order_number", "").strip()
            mine_id = request.form.get("mine_id")
            if not work_order_number or not mine_id:
                flash("Work Order Number and Mine are required", "danger")
                return render_template("work_order_records/form.html", work_order=None, mines=mines, default_mine_id=default_mine_id)

            default_mine_id = int(mine_id)
            duplicate = WorkOrder.query.filter(
                WorkOrder.mine_id == default_mine_id,
                WorkOrder.work_order_number == work_order_number,
                WorkOrder.parent_id.is_(None),
            ).first()
            if duplicate:
                flash(
                    f"Work Order number '{work_order_number}' already exists for this mine. Please enter a different number.",
                    "danger",
                )
                return render_template("work_order_records/form.html", work_order=None, mines=mines, default_mine_id=default_mine_id)

            wo = WorkOrder(
                work_order_number=work_order_number,
                mine_id=int(mine_id),
                date=datetime.utcnow().date(),
                lorry_number="-",
                tds=0,
                tds_percent=1.0,
                account_advance=0,
                mines_qty=0,
                plant_qty=0,
                rate=0,
                total_freight=0,
                cash=0,
                loading=0,
                total_advance=0,
                shortage=0,
                short_amt=0,
                munsiyana=300,
                balance=0,
                account_name="",
                remark="",
            )
            wo.recalculate()
            db.session.add(wo)
            db.session.flush()
            log_audit("create", "work_order", wo.id, f"Created work order: {wo.work_order_number}")
            db.session.commit()
            flash("Work order added successfully", "success")
            return redirect(url_for("work_order_records.work_orders"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")
    return render_template("work_order_records/form.html", work_order=None, mines=mines, default_mine_id=default_mine_id)


@work_order_records_bp.route("/edit/<int:id>", methods=["GET", "POST"])
@login_required
def edit(id):
    wo = WorkOrder.query.get_or_404(id)
    mines = Mine.query.order_by(Mine.name).all()
    if request.method == "POST":
        try:
            date_str = request.form.get("date", "").strip()
            lorry_number = request.form.get("lorry_number", "").strip()
            if not date_str or not lorry_number:
                flash("Date and Lorry Number are required", "danger")
                return render_template("work_order_records/form.html", work_order=wo, mines=mines)

            wo.date = datetime.strptime(date_str, "%Y-%m-%d").date()
            wo.lorry_number = lorry_number
            wo.work_order_number = request.form.get("work_order_number", "").strip() or None
            wo.mine_id = int(request.form.get("mine_id")) if request.form.get("mine_id") else None
            wo.tds = float(request.form.get("tds", 0) or 0)
            wo.ddtds_from = datetime.strptime(request.form.get("ddtds_from", "").strip(), "%Y-%m-%d").date() if request.form.get("ddtds_from", "").strip() else None
            wo.ddtds_to = datetime.strptime(request.form.get("ddtds_to", "").strip(), "%Y-%m-%d").date() if request.form.get("ddtds_to", "").strip() else None
            wo.account_advance = float(request.form.get("account_advance", 0) or 0)
            wo.mines_qty = float(request.form.get("mines_qty", 0) or 0)
            wo.plant_qty = float(request.form.get("plant_qty", 0) or 0)
            wo.rate = float(request.form.get("rate", 0) or 0)
            wo.cash = float(request.form.get("cash", 0) or 0)
            wo.loading = float(request.form.get("loading", 0) or 0)
            wo.total_advance = float(request.form.get("total_advance", 0) or 0)
            wo.shortage = float(request.form.get("shortage", 0) or 0)
            wo.short_amt = float(request.form.get("short_amt", 0) or 0)
            wo.munsiyana = float(request.form.get("munsiyana", 300) or 300)
            wo.account_name = request.form.get("account_name", "").strip()
            wo.remark = request.form.get("remark", "").strip()
            wo.recalculate()

            PetrolStation.query.filter_by(work_order_id=wo.id).delete()
            petrol_names = request.form.getlist("petrol_name[]")
            petrol_amounts = request.form.getlist("petrol_amount[]")
            for i, pname in enumerate(petrol_names):
                pname = pname.strip()
                if pname:
                    amt = float(petrol_amounts[i]) if i < len(petrol_amounts) and petrol_amounts[i] else 0
                    db.session.add(PetrolStation(work_order_id=wo.id, name=pname, amount=amt))

            log_audit("update", "work_order", wo.id, f"Updated work order: {wo.lorry_number}")
            db.session.commit()
            flash("Work order updated successfully", "success")
            return redirect(url_for("work_order_records.work_orders"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")
    return render_template("work_order_records/form.html", work_order=wo, mines=mines)


@work_order_records_bp.route("/delete/<int:id>", methods=["POST"])
@login_required
def delete(id):
    wo = WorkOrder.query.get_or_404(id)
    try:
        WorkOrder.query.filter_by(parent_id=wo.id).delete()
        PetrolStation.query.filter_by(work_order_id=wo.id).delete()
        db.session.delete(wo)
        log_audit("delete", "work_order", id, f"Deleted work order: {wo.lorry_number}")
        db.session.commit()
        flash("Work order deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Cannot delete: {str(e)}", "danger")
    redirect_target = request.form.get("redirect", "")
    if redirect_target == "daily-payments":
        return redirect(url_for("work_order_records.daily_payments"))
    return redirect(url_for("work_order_records.work_orders"))


EDITABLE_FIELDS = {
    "work_order_number": "WO Number",
    "tds": "TDS",
    "ddtds_from": "DD TDS From",
    "ddtds_to": "DD TDS To",
    "account_advance": "Account Advance",
    "mines_qty": "Mines Qty",
    "plant_qty": "Plant Qty",
    "rate": "Rate",
    "cash": "Cash",
    "loading": "Loading",
    "short_amt": "Short Amt",
    "munsiyana": "Munsiyana",
    "account_name": "Account Name",
    "remark": "Remark",
    "status": "Status",
}


@work_order_records_bp.route("/bulk-edit", methods=["GET", "POST"])
@login_required
def bulk_edit():
    mines = Mine.query.order_by(Mine.name).all()
    if request.method == "POST":
        wo_ids = request.form.getlist("wo_ids[]")
        field = request.form.get("field", "")
        value = request.form.get("value", "").strip()
        if not wo_ids or field not in EDITABLE_FIELDS:
            flash("Select work orders and a field", "danger")
            return redirect(url_for("work_order_records.bulk_edit"))
        try:
            for wid in wo_ids:
                wo = WorkOrder.query.get(int(wid))
                if not wo:
                    continue
                if field == "ddtds_from":
                    wo.ddtds_from = datetime.strptime(value, "%Y-%m-%d").date() if value else None
                    wo.recalculate()
                elif field == "ddtds_to":
                    wo.ddtds_to = datetime.strptime(value, "%Y-%m-%d").date() if value else None
                    wo.recalculate()
                elif field in ("tds", "account_advance", "mines_qty", "plant_qty", "rate", "cash", "loading", "short_amt", "munsiyana"):
                    setattr(wo, field, float(value or 0))
                    wo.recalculate()
                elif field in ("work_order_number", "account_name", "remark", "status"):
                    setattr(wo, field, value)
            db.session.commit()
            flash(f"Updated {len(wo_ids)} work order(s)", "success")
            return redirect(url_for("work_order_records.bulk_edit"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")
            return redirect(url_for("work_order_records.bulk_edit"))

    query = WorkOrder.query
    mine_id = request.args.get("mine_id", "", type=str)
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    status = request.args.get("status", "")
    if mine_id:
        query = query.filter_by(mine_id=int(mine_id))
    if date_from:
        query = query.filter(WorkOrder.date >= datetime.strptime(date_from, "%Y-%m-%d").date())
    if date_to:
        query = query.filter(WorkOrder.date <= datetime.strptime(date_to, "%Y-%m-%d").date())
    if status:
        query = query.filter(WorkOrder.status == status.upper())
    work_orders = query.order_by(WorkOrder.date.desc()).all()
    return render_template(
        "work_order_records/bulk_edit.html",
        work_orders=work_orders,
        mines=mines,
        editable_fields=EDITABLE_FIELDS,
        mine_id=mine_id,
        date_from=date_from,
        date_to=date_to,
        status=status,
    )


@work_order_records_bp.route("/view/<int:id>")
@login_required
def view(id):
    current_wo = WorkOrder.query.get_or_404(id)
    work_orders = WorkOrder.query.filter(
        db.or_(WorkOrder.id == id, WorkOrder.parent_id == id)
    ).options(db.selectinload(WorkOrder.petrol_stations)).order_by(WorkOrder.id.asc()).all()

    if request.args.get("export") in ("csv", "xlsx"):
        headers = ["WO #", "Date", "Truck", "Mine", "TDS", "DD TDS From", "DD TDS To", "Acc Adv", "Mines Qty", "Plant Qty", "Rate", "Freight", "Cash", "Loading", "Petrol", "Advance", "Short", "Short Amt", "Munsiyana", "Balance", "Status", "Remark", "Account"]
        rows = [
            [
                wo.work_order_number or "",
                wo.date,
                wo.lorry_number,
                wo.mine.name if wo.mine else "",
                float(wo.tds or 0), wo.ddtds_from.isoformat() if wo.ddtds_from else "", wo.ddtds_to.isoformat() if wo.ddtds_to else "", float(wo.account_advance or 0),
                float(wo.mines_qty) if wo.mines_qty else "",
                float(wo.plant_qty) if wo.plant_qty else "",
                float(wo.rate or 0), float(wo.total_freight or 0),
                float(wo.cash or 0), float(wo.loading or 0),
                sum(float(ps.amount or 0) for ps in wo.petrol_stations),
                float(wo.total_advance or 0),
                float(wo.shortage or 0),
                float(wo.short_amt or 0), float(wo.munsiyana or 0),
                float(wo.balance or 0), wo.status, wo.remark or "", wo.account_name or "",
            ]
            for wo in work_orders
        ]
        return generate_xlsx(headers, rows)

    mines = Mine.query.order_by(Mine.name).all()
    mines_json = [{"id": m.id, "name": m.name} for m in mines]
    petrol_headers = [f"Petrol {i+1}" for i in range(4)]
    for ps in current_wo.petrol_stations:
        if 0 <= ps.slot < 4:
            petrol_headers[ps.slot] = ps.name or f"Petrol {ps.slot+1}"
    petrol_amounts = {}
    for wo in work_orders:
        row = [0.0, 0.0, 0.0, 0.0]
        for ps in wo.petrol_stations:
            if 0 <= ps.slot < 4:
                row[ps.slot] = float(ps.amount or 0)
        petrol_amounts[wo.id] = row
    return render_template("work_order_records/view.html", wo=current_wo, work_orders=work_orders, current_wo=current_wo, mines=mines, mines_json=mines_json, petrol_headers=petrol_headers, petrol_amounts=petrol_amounts)


@work_order_records_bp.route("/save/<int:id>", methods=["POST"])
@login_required
def save_wo(id):
    wo = WorkOrder.query.get_or_404(id)
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "No data"}), 400

    editable = {
        "date": "date", "lorry_number": "text", "work_order_number": "text",
        "account_name": "text", "remark": "text", "status": "text",
        "tds": "number", "tds_percent": "number", "account_advance": "number",
        "mines_qty": "number", "plant_qty": "number", "rate": "number",
        "cash": "number", "loading": "number",
        "short_amt": "number", "short_rate": "number", "munsiyana": "number",
        "mine_id": "select", "ddtds_from": "date", "ddtds_to": "date",
    }

    try:
        for field, value in data.items():
            if field not in editable:
                continue
            ftype = editable[field]
            if ftype == "date":
                setattr(wo, field, datetime.strptime(value, "%Y-%m-%d").date() if value else None)
            elif ftype == "number":
                setattr(wo, field, float(value or 0))
            elif ftype == "select":
                setattr(wo, field, int(value) if value else None)
            else:
                setattr(wo, field, str(value).strip() if value else None)

        # Handle TDS auto-calculation
        if wo.tds_auto and any(f in data for f in ["date", "mines_qty", "rate", "plant_qty", "mine_id"]):
            from app.models import calculate_tds
            wo.tds = calculate_tds(wo)

        if "tds" in data:
            wo.tds_auto = False

        # Munsiyana is a per-work-order setting: applying it to one row
        # must apply to the whole work order group (parent + all entries).
        family = None
        if "munsiyana" in data:
            mun = float(data.get("munsiyana") or 0)
            family = _work_order_family(wo)
            for f in family:
                f.munsiyana = mun
                f.recalculate()
        else:
            wo.recalculate()
        db.session.commit()
        return jsonify({
            "ok": True,
            "total_freight": float(wo.total_freight or 0),
            "total_advance": float(wo.total_advance or 0),
            "shortage": float(wo.shortage or 0),
            "balance": float(wo.balance or 0),
            "status": wo.status,
            "tds": float(wo.tds or 0),
            "family": [{
                "id": f.id,
                "munsiyana": float(f.munsiyana or 0),
                "balance": float(f.balance or 0),
                "status": f.status,
            } for f in family] if family else None,
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


@work_order_records_bp.route("/save-new", methods=["POST"])
@login_required
def save_new_rows():
    rows = request.get_json()
    if not rows or not isinstance(rows, list):
        return jsonify({"ok": False, "error": "No data"}), 400

    editable = {
        "date": "date", "lorry_number": "text", "work_order_number": "text",
        "account_name": "text", "remark": "text", "status": "text",
        "tds": "number", "tds_percent": "number", "account_advance": "number",
        "mines_qty": "number", "plant_qty": "number", "rate": "number",
        "cash": "number", "loading": "number",
        "short_amt": "number", "short_rate": "number", "munsiyana": "number",
        "mine_id": "select", "ddtds_from": "date", "ddtds_to": "date",
    }

    ids = []
    try:
        for data in rows:
            wo = WorkOrder()
            for field, value in data.items():
                if field not in editable:
                    continue
                ftype = editable[field]
                if ftype == "date":
                    setattr(wo, field, datetime.strptime(value, "%Y-%m-%d").date() if value else datetime.utcnow().date())
                elif ftype == "number":
                    setattr(wo, field, float(value or 0))
                elif ftype == "select":
                    setattr(wo, field, int(value) if value else None)
                else:
                    setattr(wo, field, str(value).strip() if value else None)
            if not wo.date:
                wo.date = datetime.utcnow().date()
            if not wo.lorry_number:
                wo.lorry_number = "-"
            if not wo.tds_percent:
                wo.tds_percent = 1.0
            db.session.add(wo)
            db.session.flush()
            wo.recalculate()
            ids.append(wo.id)
        db.session.commit()
        return jsonify({"ok": True, "ids": ids})
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


@work_order_records_bp.route("/autosave/<int:id>", methods=["POST"])
@login_required
def autosave(id):
    from app.models import calculate_tds

    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "No data"}), 400

    wo_id = int(data.get("wo_id", id))
    wo = WorkOrder.query.get_or_404(wo_id)

    field = data.get("field", "")
    value = data.get("value")

    editable = {
        "date": "date", "lorry_number": "text", "work_order_number": "text",
        "account_name": "text", "remark": "text", "status": "text",
        "tds": "number", "tds_percent": "number", "account_advance": "number",
        "mines_qty": "number", "plant_qty": "number", "rate": "number",
        "cash": "number", "loading": "number",
        "short_amt": "number", "short_rate": "number", "munsiyana": "number",
        "mine_id": "select", "ddtds_from": "date", "ddtds_to": "date",
    }

    if field not in editable:
        return jsonify({"ok": False, "error": f"Field '{field}' not editable"}), 400

    try:
        ftype = editable[field]
        if ftype == "date":
            parsed = _parse_any_date(value)
            if field == "date" and parsed is None:
                return jsonify({"ok": True, "field": field, "value": wo.date.isoformat() if wo.date else None})
            setattr(wo, field, parsed)
        elif ftype == "number":
            setattr(wo, field, float(value or 0))
        elif ftype == "select":
            setattr(wo, field, int(value) if value else None)
        else:
            setattr(wo, field, str(value).strip() if value else None)

        # Handle TDS: manual override or auto-recalculate
        if field == "tds":
            wo.tds = float(value or 0)
            wo.tds_auto = False
        elif field in ("tds_percent", "ddtds_from", "ddtds_to", "mines_qty", "rate"):
            wo.tds_auto = True
            wo.tds = calculate_tds(wo)

        # Munsiyana is a per-work-order setting: applying it to one row
        # must apply to the whole work order group (parent + all entries).
        family = None
        if field == "munsiyana":
            family = _work_order_family(wo)
            for f in family:
                f.munsiyana = float(value or 0)
                f.recalculate()
        else:
            wo.recalculate()
        db.session.commit()

        return jsonify({
            "ok": True,
            "field": field,
            "value": value,
            "total_freight": float(wo.total_freight or 0),
            "total_advance": float(wo.total_advance or 0),
            "shortage": float(wo.shortage or 0),
            "short_amt": float(wo.short_amt or 0),
            "balance": float(wo.balance or 0),
            "tds": float(wo.tds or 0),
            "status": wo.status,
            "family": [{
                "id": f.id,
                "munsiyana": float(f.munsiyana or 0),
                "balance": float(f.balance or 0),
                "status": f.status,
            } for f in family] if family else None,
        })
    except Exception as e:
        print(f"[AUTOSAVE] ERROR WO#{wo.id if 'wo' in dir() else id} field={field} error={e}")
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 400


@work_order_records_bp.route("/petrol/list/<int:wo_id>")
@login_required
def petrol_list(wo_id):
    wo = WorkOrder.query.get_or_404(wo_id)
    stations = [{"id": ps.id, "name": ps.name, "amount": float(ps.amount or 0)} for ps in wo.petrol_stations]
    return jsonify({"ok": True, "stations": stations})


@work_order_records_bp.route("/api/truck-account")
@login_required
def truck_account_lookup():
    from app.models import TransporterTruck

    lorry = "".join(request.args.get("lorry", "").strip().upper().split())
    if not lorry:
        return jsonify({"ok": True, "found": False})
    for tt in TransporterTruck.query.order_by(TransporterTruck.id.desc()).all():
        stored = "".join((tt.lorry_number or "").strip().upper().split())
        if stored == lorry:
            transporter = tt.transporter
            return jsonify({"ok": True, "found": True, "account_name": transporter.name})
    return jsonify({"ok": True, "found": False})


@work_order_records_bp.route("/petrol/add", methods=["POST"])
@login_required
def petrol_add():
    data = request.get_json()
    wo_id = data.get("work_order_id")
    name = (data.get("value") or "").strip() or "New Station"
    if not wo_id:
        return jsonify({"ok": False, "error": "Missing work order id"}), 400
    try:
        ps = PetrolStation(work_order_id=int(wo_id), name=name, amount=0)
        db.session.add(ps)
        db.session.commit()
        wo = WorkOrder.query.get(int(wo_id))
        if wo:
            wo.recalculate()
            db.session.commit()
        return jsonify({
            "ok": True,
            "ps_id": ps.id,
            "total_advance": float(wo.total_advance or 0) if wo else 0,
            "balance": float(wo.balance or 0) if wo else 0,
            "status": wo.status if wo else "PENDING",
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


@work_order_records_bp.route("/petrol/<int:ps_id>", methods=["POST", "DELETE"])
@login_required
def petrol_update(ps_id):
    ps = PetrolStation.query.get_or_404(ps_id)
    if request.method == "DELETE":
        wo_id = ps.work_order_id
        try:
            db.session.delete(ps)
            db.session.commit()
            wo = WorkOrder.query.get(wo_id)
            if wo:
                wo.recalculate()
                db.session.commit()
            return jsonify({
                "ok": True,
                "total_advance": float(wo.total_advance or 0) if wo else 0,
                "balance": float(wo.balance or 0) if wo else 0,
                "status": wo.status if wo else "PENDING",
            })
        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "error": str(e)}), 500
    data = request.get_json()
    field = data.get("field")
    value = data.get("value")
    try:
        if field == "name":
            ps.name = str(value).strip()
        elif field == "amount":
            ps.amount = float(value or 0)
        db.session.commit()
        wo = WorkOrder.query.get(ps.work_order_id)
        if wo:
            wo.recalculate()
            db.session.commit()
        return jsonify({
            "ok": True,
            "ps_id": ps.id,
            "total_advance": float(wo.total_advance or 0) if wo else 0,
            "balance": float(wo.balance or 0) if wo else 0,
            "status": wo.status if wo else "PENDING",
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


@work_order_records_bp.route("/petrol/save", methods=["POST"])
@login_required
def petrol_save_slot():
    """Upsert a petrol station at a given column slot for a work order.
    JSON: {work_order_id, slot, amount?, name?}
    """
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "No data"}), 400
    wo_id = data.get("work_order_id")
    slot = int(data.get("slot") or 0)
    if not wo_id or not (0 <= slot <= 3):
        return jsonify({"ok": False, "error": "Invalid work order or slot"}), 400
    wo = WorkOrder.query.get_or_404(int(wo_id))
    try:
        ps = PetrolStation.query.filter_by(work_order_id=wo.id, slot=slot).first()
        if not ps:
            ps = PetrolStation(work_order_id=wo.id, slot=slot, name=f"Petrol {slot+1}", amount=0)
            db.session.add(ps)
        if data.get("name") is not None:
            ps.name = str(data.get("name")).strip() or f"Petrol {slot+1}"
        if data.get("amount") is not None:
            ps.amount = float(data.get("amount") or 0)
        wo.recalculate()
        db.session.commit()
        return jsonify({
            "ok": True,
            "ps_id": ps.id,
            "total_advance": float(wo.total_advance or 0),
            "balance": float(wo.balance or 0),
            "status": wo.status,
            "family": [{
                "id": f.id,
                "munsiyana": float(f.munsiyana or 0),
                "balance": float(f.balance or 0),
                "status": f.status,
            } for f in _work_order_family(wo)],
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


@work_order_records_bp.route("/view/<int:id>/add-rows", methods=["POST"])
@login_required
def add_rows(id):
    current_wo = WorkOrder.query.get_or_404(id)
    dates = request.form.getlist("date[]")
    if not dates:
        flash("No rows to add", "danger")
        return redirect(url_for("work_order_records.view", id=id))

    wo_number = current_wo.work_order_number
    if not wo_number:
        wo_number = f"WO-{current_wo.id}"
        current_wo.work_order_number = wo_number

    lorry_numbers = request.form.getlist("lorry_number[]")
    mine_ids = request.form.getlist("mine_id[]")
    tds_list = request.form.getlist("tds[]")
    ddtds_from_list = request.form.getlist("ddtds_from[]")
    ddtds_to_list = request.form.getlist("ddtds_to[]")
    account_advance_list = request.form.getlist("account_advance[]")
    mines_qty_list = request.form.getlist("mines_qty[]")
    plant_qty_list = request.form.getlist("plant_qty[]")
    rate_list = request.form.getlist("rate[]")
    cash_list = request.form.getlist("cash[]")
    loading_list = request.form.getlist("loading[]")
    total_advance_list = request.form.getlist("total_advance[]")
    shortage_list = request.form.getlist("shortage[]")
    short_amt_list = request.form.getlist("short_amt[]")
    munsiyana_list = request.form.getlist("munsiyana[]")
    account_name_list = request.form.getlist("account_name[]")
    remark_list = request.form.getlist("remark[]")
    petrol_list = request.form.getlist("petrol[]")

    try:
        count = 0
        for i, date_str in enumerate(dates):
            date_str = date_str.strip()
            lorry_number = lorry_numbers[i].strip() if i < len(lorry_numbers) else ""
            if not date_str or not lorry_number:
                continue

            wo = WorkOrder(
                date=datetime.strptime(date_str, "%Y-%m-%d").date(),
                lorry_number=lorry_number,
                work_order_number=wo_number,
                mine_id=int(mine_ids[i]) if i < len(mine_ids) and mine_ids[i] else None,
                tds=float(tds_list[i] or 0) if i < len(tds_list) else 0,
                tds_percent=1.0,
                ddtds_from=datetime.strptime(ddtds_from_list[i].strip(), "%Y-%m-%d").date() if i < len(ddtds_from_list) and ddtds_from_list[i].strip() else None,
                ddtds_to=datetime.strptime(ddtds_to_list[i].strip(), "%Y-%m-%d").date() if i < len(ddtds_to_list) and ddtds_to_list[i].strip() else None,
                account_advance=float(account_advance_list[i] or 0) if i < len(account_advance_list) else 0,
                mines_qty=float(mines_qty_list[i] or 0) if i < len(mines_qty_list) else 0,
                plant_qty=float(plant_qty_list[i] or 0) if i < len(plant_qty_list) else 0,
                rate=float(rate_list[i] or 0) if i < len(rate_list) else 0,
                total_freight=0,
                cash=float(cash_list[i] or 0) if i < len(cash_list) else 0,
                loading=float(loading_list[i] or 0) if i < len(loading_list) else 0,
                short_amt=float(short_amt_list[i] or 0) if i < len(short_amt_list) else 0,
                munsiyana=float(munsiyana_list[i] or 300) if i < len(munsiyana_list) else 300,
                balance=0,
                account_name=account_name_list[i].strip() if i < len(account_name_list) else "",
                remark=remark_list[i].strip() if i < len(remark_list) else "",
            )
            wo.recalculate()
            db.session.add(wo)
            db.session.flush()

            petrol_raw = petrol_list[i].strip() if i < len(petrol_list) and petrol_list[i] else ""
            if petrol_raw:
                for part in petrol_raw.split(","):
                    part = part.strip()
                    if ":" in part:
                        pname, pamt = part.rsplit(":", 1)
                        pname = pname.strip()
                        if pname:
                            db.session.add(PetrolStation(work_order_id=wo.id, name=pname, amount=float(pamt or 0)))
                    elif part:
                        db.session.add(PetrolStation(work_order_id=wo.id, name=part, amount=0))

            count += 1

        log_audit("create", "work_order", current_wo.id, f"Added {count} row(s) to WO {wo_number}")
        db.session.commit()
        flash(f"{count} row(s) added successfully", "success")
        return redirect(url_for("work_order_records.view", id=id))
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {str(e)}", "danger")
        return redirect(url_for("work_order_records.view", id=id))


work_order_fields = [
    ("date", "Date", "date"),
    ("lorry_number", "Truck #", "text"),
    ("work_order_number", "WO Number", "text"),
    ("mine_name", "Mine Name", "text"),
    ("tds", "TDS", "number"),
    ("ddtds_from", "DD TDS From", "date"),
    ("ddtds_to", "DD TDS To", "date"),
    ("account_advance", "Acc Adv", "number"),
    ("mines_qty", "Mines Qty", "number"),
    ("plant_qty", "Plant Qty", "number"),
    ("rate", "Rate", "number"),
    ("total_freight", "Freight", "computed"),
    ("cash", "Cash", "number"),
    ("loading", "Loading", "number"),
    ("petrol", "Petrol", "text"),
    ("total_advance", "Advance", "computed"),
    ("shortage", "Short", "computed"),
    ("short_amt", "Short Amt", "number"),
    ("munsiyana", "Munsiyana", "number"),
    ("balance", "Balance", "computed"),
    ("status", "Status", "text"),
    ("remark", "Remark", "text"),
    ("account_name", "Account Name", "text"),
]

COLUMN_SYNONYMS = {
    "lorry_number": ["truck", "vehicle", "lorry", "truck no", "truck number", "vehicle no",
                      "vehicle number", "truck#", "vehicle#", "lorry no", "lorry number",
                      "truck_no", "vehicle_no", "lorry_no", "registration", "reg no",
                      "plate", "truck plate", "vehicle plate", "gaadi", "gaadi no"],
    "date": ["date", "trip date", "do date", "delivery date", "dispatch date",
             "trip_date", "do_date", "loading date", "tripdate", "dodate"],
    "mine_name": ["mine", "mine name", "source", "from", "loading point", "origin",
                  "mines", "mine_name", "quarry", "quarry name", "minename"],
    "plant_qty": ["plant qty", "plant quantity", "unload qty", "delivery qty", "qty plant",
                  "plant_qty", "planted", "plant ton", "plant tons", "delivery qty tons"],
    "mines_qty": ["mines qty", "mines quantity", "loading qty", "load qty", "mine qty",
                  "mines_qty", "loaded qty", "mines ton", "mines tons", "load ton"],
    "rate": ["rate", "rate per ton", "per ton", "price", "freight rate", "rate/ton",
             "rate per trip", "trip rate", "rate_per_ton"],
    "total_freight": ["freight", "total freight", "amount", "total amount", "trip amount",
                      "total_freight", "freight amount", "payable", "total payable"],
    "cash": ["cash", "cash paid", "cash amount", "peti", "cashpayment", "cash_paid"],
    "loading": ["loading", "loading charge", "loading cost", "loading fee",
                "loading_charges", "loading_chrg"],
    "short_amt": ["short amt", "shortage amount", "shortage", "short amount", "short_amt",
                  "shortage_amt", "shortageamt"],
    "munsiyana": ["munsiyana", "munisiyana", "manusiyana", "mansiya", "munsiana",
                  "munsiyana amount", "commission"],
    "tds": ["tds", "tds amount", "tax deducted", "tax", "tds rate"],
    "account_advance": ["acc adv", "account advance", "acc advance", "account adv",
                        "account_advance", "advance account", "accadv"],
    "remark": ["remark", "remarks", "note", "notes", "comment", "comments",
               "description", "narration", "narr"],
    "account_name": ["account", "account name", "party", "party name", "ac name",
                     "ac_name", "account_name", "firm", "firm name"],
    "status": ["status", "stage", "state"],
}


def _fuzzy_match_csv_header(header, fields):
    """Match a CSV header to a field key using synonyms and partial matching."""
    h = header.lower().strip()
    h_clean = h.replace("-", " ").replace("_", " ").replace("#", "").strip()

    for fkey, flabel, ftype in fields:
        if ftype == "computed":
            continue
        if fkey in COLUMN_SYNONYMS:
            for syn in COLUMN_SYNONYMS[fkey]:
                if syn in h_clean or h_clean in syn:
                    return fkey

    for fkey, flabel, ftype in fields:
        if ftype == "computed":
            continue
        fk_words = fkey.replace("_", " ").split()
        fl_words = flabel.lower().split()
        for word in fk_words + fl_words:
            if len(word) > 3 and word in h_clean:
                return fkey

    return None


@work_order_records_bp.route("/import", methods=["GET", "POST"])
@login_required
def import_work_orders():
    mines = Mine.query.order_by(Mine.name).all()
    wo_id = request.args.get("wo_id", type=int)
    current_wo = WorkOrder.query.get(wo_id) if wo_id else None

    if request.method == "POST":
        file = request.files.get("file")
        if not file or not file.filename:
            flash("Please choose a file to import", "danger")
            return render_template("work_order_records/import.html", fields=work_order_fields, mines=mines, current_wo=current_wo)

        fname = file.filename.lower()
        if fname.endswith(".csv"):
            content = file.stream.read().decode("utf-8-sig")
            reader = csv.reader(io.StringIO(content))
            try:
                src_headers = next(reader, [])
            except StopIteration:
                src_headers = []
            all_rows = [r for r in reader]
        elif fname.endswith(".xlsx"):
            import openpyxl
            wb = openpyxl.load_workbook(file.stream, read_only=True, data_only=True)
            ws = wb.active or wb.worksheets[0]
            all_rows = []
            for row in ws.iter_rows(values_only=True):
                if row is None:
                    continue
                all_rows.append(["" if v is None else (v.date().isoformat() if hasattr(v, "date") and hasattr(v, "hour") else (v.isoformat() if hasattr(v, "isoformat") else str(v))) for v in row])
            wb.close()
            src_headers = all_rows[0] if all_rows else []
            all_rows = all_rows[1:]
        else:
            flash("Please upload a .csv or .xlsx file", "danger")
            return render_template("work_order_records/import.html", fields=work_order_fields, mines=mines, current_wo=current_wo)

        if not src_headers:
            flash("File is empty or has no headers", "danger")
            return render_template("work_order_records/import.html", fields=work_order_fields, mines=mines, current_wo=current_wo)
        preview_rows = all_rows[:5]

        fuzzy_mappings = {}
        for idx, header in enumerate(src_headers):
            matched = _fuzzy_match_csv_header(header, work_order_fields)
            if matched:
                fuzzy_mappings[str(idx)] = matched

        ai_mappings = dict(fuzzy_mappings)

        groq_api_key = _groq_api_key()
        if groq_api_key and src_headers:
            field_descriptions = ""
            for _, (fkey, flabel, ftype) in enumerate(work_order_fields):
                field_descriptions += f'  "{fkey}" = {flabel} ({ftype})\n'

            sample_rows_text = ""
            for i, row in enumerate(preview_rows):
                sample_rows_text += f"Row {i}: {row}\n"

            prompt = f"""The user uploaded a CSV with these headers: {', '.join(src_headers)}

Here are sample rows from the CSV:
{sample_rows_text}

Available work order fields (use the exact key):
{field_descriptions}

Map each CSV column (by its header name and sample data) to the correct work order field key.
Return a JSON object where keys are column indices as strings ("0", "1", etc.) and values are the EXACT field key from the list above (like "date", "lorry_number", "mine_name", etc.).
Only include columns with high confidence.
Return ONLY the JSON object, no other text.
"""

            try:
                response = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {groq_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "openai/gpt-oss-120b",
                        "messages": [
                            {"role": "system", "content": "You are a helpful assistant that maps CSV column indices to work order field names."},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.0,
                    },
                    timeout=15,
                )

                if response.status_code == 200:
                    result = response.json()
                    choice = result.get("choices", [{}])[0]
                    message = choice.get("message", {})
                    content = message.get("content", "").strip()
                    mapping = json.loads(content)
                    valid_keys = {fkey for fkey, _, _ in work_order_fields}
                    if isinstance(mapping, dict):
                        for k, v in mapping.items():
                            if v and str(v) in valid_keys:
                                ai_mappings[str(k)] = v
            except (requests.RequestException, json.JSONDecodeError, KeyError, ValueError):
                pass

        return render_template(
            "work_order_records/import.html",
            fields=work_order_fields,
            mines=mines,
            ai_mappings=ai_mappings,
            src_headers=src_headers,
            preview_rows=preview_rows,
            total_rows=len(all_rows),
            raw_data=[src_headers] + all_rows,
            current_wo=current_wo,
        )
    return render_template("work_order_records/import.html", fields=work_order_fields, mines=mines, current_wo=current_wo)


@work_order_records_bp.route("/import/execute", methods=["POST"])
@login_required
def import_execute():
    wo_id = request.form.get("wo_id", type=int)
    current_wo = WorkOrder.query.get(wo_id) if wo_id else None
    if current_wo and not current_wo.work_order_number:
        current_wo.work_order_number = f"WO-{current_wo.id}"
    mapping = {}
    for key in request.form:
        if key.startswith("map_"):
            src_col = int(key[4:])
            dst_field = request.form[key]
            if dst_field:
                mapping[src_col] = dst_field

    if not mapping:
        flash("No field mapping provided", "danger")
        return redirect(url_for("work_order_records.import_work_orders"))

    data_keys = [k for k in request.form if k.startswith("data_")]
    row_indices = set()
    for k in data_keys:
        parts = k.split("_")
        if len(parts) >= 3:
            row_indices.add(int(parts[1]))
    sorted_rows = sorted(row_indices)
    count = 0
    try:
        for i in sorted_rows:
            date_str = request.form.get(f"data_{i}_0", "").strip()
            lorry_number = request.form.get(f"data_{i}_1", "").strip()
            if not date_str or not lorry_number:
                continue

            data = {}
            for src_col, dst_field in mapping.items():
                val = request.form.get(f"data_{i}_{src_col}", "").strip()
                data[dst_field] = val

            mine_id = None
            if current_wo and current_wo.mine_id:
                mine_id = current_wo.mine_id
            elif "mine_name" in data and data["mine_name"]:
                mine = Mine.query.filter_by(name=data["mine_name"]).first()
                if not mine:
                    mine = Mine(name=data["mine_name"])
                    db.session.add(mine)
                    db.session.flush()
                mine_id = mine.id

            wo_number = current_wo.work_order_number if current_wo else (data.get("work_order_number", None) or None)

            wo = WorkOrder(
                date=_parse_any_date(data.get("date", date_str)) or _parse_any_date(date_str),
                lorry_number=data.get("lorry_number", lorry_number),
                work_order_number=wo_number if not current_wo else current_wo.work_order_number,
                parent_id=current_wo.id if current_wo else None,
                mine_id=mine_id or (current_wo.mine_id if current_wo else None),
                tds=float(data.get("tds", 0) or 0),
                tds_percent=float(data.get("tds_percent", 0) or 0) or 1.0,
                ddtds_from=datetime.strptime(data["ddtds_from"], "%Y-%m-%d").date() if data.get("ddtds_from", "").strip() else None,
                ddtds_to=datetime.strptime(data["ddtds_to"], "%Y-%m-%d").date() if data.get("ddtds_to", "").strip() else None,
                account_advance=float(data.get("account_advance", 0) or 0),
                mines_qty=float(data.get("mines_qty", 0) or 0),
                plant_qty=float(data.get("plant_qty", 0) or 0),
                rate=float(data.get("rate", 0) or 0),
                total_freight=0,
                cash=float(data.get("cash", 0) or 0),
                loading=float(data.get("loading", 0) or 0),
                short_amt=float(data.get("short_amt", 0) or 0),
                munsiyana=float(data.get("munsiyana", 300) or 300),
                balance=0,
                account_name=data.get("account_name", ""),
                remark=data.get("remark", ""),
            )
            db.session.add(wo)
            wo.recalculate()
            db.session.flush()

            petrol_raw = data.get("petrol", "")
            if petrol_raw:
                PetrolStation.query.filter_by(work_order_id=wo.id).delete()
                for part in petrol_raw.split(","):
                    part = part.strip()
                    if ":" in part:
                        pname, pamt = part.rsplit(":", 1)
                        pname = pname.strip()
                        if pname:
                            db.session.add(PetrolStation(work_order_id=wo.id, name=pname, amount=float(pamt or 0)))
                    elif part:
                        db.session.add(PetrolStation(work_order_id=wo.id, name=part, amount=0))

            count += 1

        log_audit("import", "work_order", current_wo.id if current_wo else 0, f"Imported {count} work order(s)")
        db.session.commit()
        flash(f"{count} work order(s) imported successfully", "success")
        if current_wo:
            return redirect(url_for("work_order_records.view", id=current_wo.id))
        return redirect(url_for("work_order_records.work_orders"))
    except Exception as e:
        db.session.rollback()
        flash(f"Import error: {str(e)}", "danger")
        if current_wo:
            return redirect(url_for("work_order_records.import_work_orders", wo_id=current_wo.id))
        return redirect(url_for("work_order_records.import_work_orders"))


@work_order_records_bp.route("/daily-payments")
@login_required
def daily_payments():
    today = datetime.today().strftime("%Y-%m-%d")
    date_from = request.args.get("date_from", today)
    date_to = request.args.get("date_to", today)

    query = WorkOrder.query.filter(
        db.and_(
            WorkOrder.plant_qty.isnot(None),
            WorkOrder.plant_qty > 0,
            db.or_(
                WorkOrder.cash > 0,
                WorkOrder.total_advance > 0,
                WorkOrder.account_advance > 0,
                WorkOrder.total_freight > 0,
            ),
        )
    )

    if date_from:
        query = query.filter(WorkOrder.date >= datetime.strptime(date_from, "%Y-%m-%d").date())
    if date_to:
        query = query.filter(WorkOrder.date <= datetime.strptime(date_to, "%Y-%m-%d").date())

    rows = query.options(
        db.joinedload(WorkOrder.mine),
        db.joinedload(WorkOrder.petrol_stations),
    ).order_by(WorkOrder.date.desc(), WorkOrder.id).all()

    account_groups = {}
    account_totals = {}
    grand_balance = 0

    for wo in rows:
        d = wo.date.isoformat()
        an = wo.account_name or "-"
        if an not in account_groups:
            account_groups[an] = []

        entry = {
            "id": wo.id,
            "date": d,
            "lorry_number": wo.lorry_number,
            "work_order_number": wo.work_order_number or "",
            "account_name": an,
            "mine_name": wo.mine.name if wo.mine else "-",
            "plant_name": (wo.mine.plant.name if wo.mine and wo.mine.plant else "-"),
            "balance": round(float(wo.balance or 0), 2),
            "payable_date": wo.payable_date.isoformat() if wo.payable_date else "",
        }
        account_groups[an].append(entry)
        account_totals[an] = round(account_totals.get(an, 0) + entry["balance"], 2)
        grand_balance += entry["balance"]

    for an in account_groups:
        account_groups[an].sort(key=lambda e: (e["date"], e["id"]), reverse=True)

    sorted_account_names = sorted(account_groups.keys())

    all_entries = []
    for an in sorted_account_names:
        for e in account_groups[an]:
            all_entries.append(e)

    export = request.args.get("export")
    account_filter = (request.args.get("account_name") or "").strip().upper()

    if export in ("xlsx", "pdf"):
        if account_filter in account_groups:
            export_entries = account_groups[account_filter]
        else:
            export_entries = all_entries

        headers = ["#", "WO", "Date", "Lorry", "Account Name", "Plant", "Mine", "Balance", "Payable Date"]
        export_rows = [
            [
                i, e["work_order_number"], e["date"], e["lorry_number"],
                e["account_name"], e["plant_name"], e["mine_name"],
                e["balance"], e["payable_date"] or "",
            ]
            for i, e in enumerate(export_entries, start=1)
        ]

        if export == "xlsx":
            name_part = f"_{account_filter.replace(' ', '_')}" if account_filter else ""
            return generate_xlsx(headers, export_rows, filename=f"daily_payments{name_part}_{date_from}_{date_to}.xlsx")

        if export == "pdf":
            return _daily_payments_pdf(
                headers, export_rows, account_filter,
                date_from, date_to, total=sum(e["balance"] for e in export_entries),
            )

    return render_template(
        "work_order_records/daily_payments.html",
        account_groups=account_groups,
        account_totals=account_totals,
        sorted_account_names=sorted_account_names,
        date_from=date_from,
        date_to=date_to,
        grand_balance=round(grand_balance, 2),
        mines=Mine.query.order_by(Mine.name).all(),
        plants=Plant.query.order_by(Plant.name).all(),
        today=today,
    )


def _daily_payments_pdf(headers, export_rows, account_name, date_from, date_to, total=0):
    from io import BytesIO
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas

    buf = BytesIO()
    p = pdfcanvas.Canvas(buf, pagesize=landscape(A4))
    width, height = landscape(A4)

    title = f"Daily Payments {account_name}" if account_name else "Daily Payments"
    subtitle = f"{date_from} to {date_to}"

    colors_light_grey = colors.HexColor("#f2f2f2")

    def draw_table():
        p.setFont("Helvetica-Bold", 10)
        col_widths = [14 * mm, 30 * mm, 24 * mm, 30 * mm, 38 * mm, 36 * mm, 36 * mm, 26 * mm, 26 * mm]
        start_y = height - 50 * mm

        x_offset = 15 * mm
        p.setFillColor(colors.HexColor("#333"))
        p.drawString(x_offset, height - 25 * mm, title)
        p.setFont("Helvetica", 9)
        p.setFillColor(colors.grey)
        p.drawString(x_offset, height - 32 * mm, subtitle)

        for i, header in enumerate(headers):
            if i < len(col_widths):
                p.drawString(x_offset, start_y, header)
                x_offset += col_widths[i]
            else:
                p.drawString(x_offset, start_y, header)
                x_offset += 20 * mm

        y = start_y - 6 * mm
        p.setLineWidth(0.5)
        p.line(15 * mm, start_y - 2 * mm, width - 15 * mm, start_y - 2 * mm)

        if not export_rows:
            p.drawString(15 * mm, y, "No records")
            return

        row_h = 6 * mm
        total_row_y = 0

        for idx, row in enumerate(export_rows):
            if y < 30 * mm:
                p.showPage()
                p.setFont("Helvetica", 9)
                y = height - 30 * mm
                total_row_y = 0

            if idx % 2 == 0:
                p.setFillColor(colors_light_grey)
                p.rect(15 * mm, y - row_h + 1 * mm, width - 30 * mm, row_h, stroke=0, fill=1)

            p.setFillColor(colors.black)
            x_offset = 15 * mm
            for i, cell in enumerate(row):
                if i < len(col_widths):
                    p.drawString(x_offset + 2 * mm, y - 1 * mm, str(cell))
                    x_offset += col_widths[i]
                else:
                    p.drawString(x_offset + 2 * mm, y - 1 * mm, str(cell))
                    x_offset += 20 * mm
            y -= row_h
            total_row_y = y

        p.setLineWidth(0.5)
        p.line(15 * mm, y, width - 15 * mm, y)

        if total_row_y == 0:
            total_row_y = y

        p.setFillColor(colors.HexColor("#333"))
        p.setFont("Helvetica-Bold", 10)
        p.drawString(15 * mm, total_row_y - 1 * mm, f"Grand Total ({len(export_rows)} records)")
        p.drawRightString((width - 15 * mm), total_row_y - 1 * mm, f"\u20B9 {total:,.2f}")

    draw_table()
    p.showPage()
    p.save()
    buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype="application/pdf",
        headers={
            "Content-Disposition": (
                f"attachment;filename=daily_payments_{account_name.replace(' ', '_')}_{date_from}_{date_to}.pdf"
                if account_name
                else f"attachment;filename=daily_payments_{date_from}_{date_to}.pdf"
            )
        },
    )


@work_order_records_bp.route("/daily-payments/api/mines")
@login_required
def daily_payments_mines_api():
    plant_id = request.args.get("plant_id", type=int)
    q = Mine.query
    if plant_id:
        q = q.filter(Mine.plant_id == plant_id)
    mines = q.order_by(Mine.name).all()
    return jsonify([{"id": m.id, "name": m.name} for m in mines])


@work_order_records_bp.route("/daily-payments/api/work-orders")
@login_required
def daily_payments_work_orders_api():
    mine_id = request.args.get("mine_id", type=int)
    q = WorkOrder.query.filter(
        db.and_(
            WorkOrder.plant_qty.isnot(None),
            WorkOrder.plant_qty > 0,
            db.or_(
                WorkOrder.cash > 0,
                WorkOrder.total_advance > 0,
                WorkOrder.account_advance > 0,
                WorkOrder.total_freight > 0,
            ),
        )
    )
    if mine_id:
        q = q.filter(WorkOrder.mine_id == mine_id)
    wos = q.options(db.joinedload(WorkOrder.mine), db.joinedload(WorkOrder.petrol_stations)).order_by(WorkOrder.id.desc()).all()
    result = []
    for wo in wos:
        petrol = sum(float(p.amount or 0) for p in wo.petrol_stations)
        result.append({
            "id": wo.id,
            "lorry_number": wo.lorry_number,
            "work_order_number": wo.work_order_number or "",
            "date": wo.date.isoformat(),
            "mine_name": wo.mine.name if wo.mine else "-",
            "account_name": wo.account_name or "-",
            "plant_qty": float(wo.plant_qty or 0),
            "mines_qty": float(wo.mines_qty or 0),
            "rate": float(wo.rate or 0),
            "total_freight": float(wo.total_freight or 0),
            "cash": float(wo.cash or 0),
            "loading": float(wo.loading or 0),
            "total_advance": float(wo.total_advance or 0),
            "shortage": float(wo.shortage or 0),
            "short_amt": float(wo.short_amt or 0),
            "munsiyana": float(wo.munsiyana or 0),
            "tds": float(wo.tds or 0),
            "account_advance": float(wo.account_advance or 0),
            "petrol": petrol,
            "balance": float(wo.balance or 0),
            "payable_date": wo.payable_date.isoformat() if wo.payable_date else None,
            "status": wo.status,
        })
    return jsonify(result)


@work_order_records_bp.route("/daily-payments/save-payment/confirm", methods=["POST"])
@login_required
def confirm_daily_payment():
    try:
        data = request.get_json()
        wo_id = int(data.get("work_order_id") or 0)
        payment_date = data.get("payment_date", "").strip()

        if not wo_id:
            return jsonify({"ok": False, "error": "Invalid work order"}), 400

        wo = WorkOrder.query.get_or_404(wo_id)
        if not payment_date:
            payment_date = datetime.today().strftime("%Y-%m-%d")

        wo.payable_date = datetime.strptime(payment_date, "%Y-%m-%d").date()
        wo.status = "COMPLETED"
        log_audit("payment", "work_order", wo.id, f"Payment recorded on {payment_date} for {wo.lorry_number}")
        db.session.commit()
        return jsonify({"ok": True, "balance": float(wo.balance), "status": wo.status})
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


@work_order_records_bp.route("/daily-payments/add", methods=["POST"])
@login_required
def add_daily_payment():
    try:
        date_str = request.form.get("date", "").strip()
        lorry_number = request.form.get("lorry_number", "").strip()
        if not date_str or not lorry_number:
            flash("Date and Lorry Number are required", "danger")
            return redirect(url_for("work_order_records.daily_payments"))

        freight = float(request.form.get("total_freight", 0) or 0)
        cash = float(request.form.get("cash", 0) or 0)
        loading = float(request.form.get("loading", 0) or 0)
        total_advance = float(request.form.get("total_advance", 0) or 0)
        shortage = float(request.form.get("shortage", 0) or 0)
        short_amt = float(request.form.get("short_amt", 0) or 0)
        munsiyana = float(request.form.get("munsiyana", 300) or 300)
        tds = float(request.form.get("tds", 0) or 0)
        acc_adv = float(request.form.get("account_advance", 0) or 0)
        petrol_amt = float(request.form.get("petrol", 0) or 0)

        petrol_name = request.form.get("petrol_name", "").strip()
        ddtds_from_str = request.form.get("ddtds_from", "").strip()
        ddtds_to_str = request.form.get("ddtds_to", "").strip()

        deductions = cash + loading + total_advance + shortage + short_amt + munsiyana + tds + acc_adv + petrol_amt
        balance = freight - deductions

        wo = WorkOrder(
            date=datetime.strptime(date_str, "%Y-%m-%d").date(),
            lorry_number=lorry_number,
            work_order_number=None,
            mine_id=int(request.form.get("mine_id")) if request.form.get("mine_id") else None,
            account_name=request.form.get("account_name", "").strip(),
            remark=request.form.get("remark", "").strip(),
            total_freight=freight,
            cash=cash,
            loading=loading,
            total_advance=total_advance,
            shortage=shortage,
            short_amt=short_amt,
            munsiyana=munsiyana,
            tds=tds,
            tds_percent=1.0,
            ddtds_from=datetime.strptime(ddtds_from_str, "%Y-%m-%d").date() if ddtds_from_str else None,
            ddtds_to=datetime.strptime(ddtds_to_str, "%Y-%m-%d").date() if ddtds_to_str else None,
            account_advance=acc_adv,
            mines_qty=0,
            plant_qty=0,
            rate=0,
            balance=round(balance, 2),
        )
        db.session.add(wo)
        db.session.flush()

        if petrol_amt > 0 and petrol_name:
            ps = PetrolStation(work_order_id=wo.id, name=petrol_name, amount=petrol_amt)
            db.session.add(ps)

        log_audit("create", "work_order", wo.id, f"Added daily payment for {lorry_number}")
        db.session.commit()
        flash("Payment added successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error adding payment: {str(e)}", "danger")
    return redirect(url_for("work_order_records.daily_payments"))


@work_order_records_bp.route("/ai-search", methods=["POST"])
@login_required
def ai_search():
    data = request.get_json()
    query_text = data.get("query", "").strip() if data else ""
    if not query_text:
        return jsonify({"results": [], "query": ""})

    groq_api_key = _groq_api_key()
    if not groq_api_key:
        return jsonify({"results": [], "query": query_text, "error": "AI not configured"})

    field_names = [
        "date", "date_from", "date_to", "lorry_number", "work_order_number",
        "mine_name", "account_name", "status", "mines_qty", "plant_qty",
        "rate", "total_freight", "cash", "loading", "total_advance",
        "balance", "tds", "remark"
    ]

    prompt = f"""You are a search filter generator for a transport management system.

The user types a natural language query. Convert it to a JSON filter object.

Available filter fields:
- date_from (YYYY-MM-DD), date_to (YYYY-MM-DD)
- lorry_number (truck number, partial match OK)
- work_order_number
- mine_name (partial match OK)
- account_name (partial match OK)
- status ("Pending" or "Completed")
- min_freight, max_freight (number)
- min_balance, max_balance (number)

User query: "{query_text}"

Return ONLY a JSON object with the filter fields. Example:
{{"lorry_number": "TRK-01", "status": "Pending"}}

If the query is unclear, return an empty object {{}}.
Do NOT include any explanation, just the JSON."""

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {groq_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "openai/gpt-oss-120b",
                "messages": [
                    {"role": "system", "content": "You convert natural language queries into JSON filter objects for a transport database. Return only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
            },
            timeout=10,
        )

        if response.status_code != 200:
            return jsonify({"results": [], "query": query_text, "error": "AI service error"})

        content = response.json()["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

        filters = json.loads(content)
    except (requests.RequestException, json.JSONDecodeError, KeyError, ValueError):
        return jsonify({"results": [], "query": query_text, "error": "Could not understand query"})

    query = WorkOrder.query

    if filters.get("date_from"):
        try:
            d = datetime.strptime(filters["date_from"], "%Y-%m-%d").date()
            query = query.filter(WorkOrder.date >= d)
        except ValueError:
            pass
    if filters.get("date_to"):
        try:
            d = datetime.strptime(filters["date_to"], "%Y-%m-%d").date()
            query = query.filter(WorkOrder.date <= d)
        except ValueError:
            pass
    if filters.get("lorry_number"):
        query = query.filter(WorkOrder.lorry_number.ilike(f"%{filters['lorry_number']}%"))
    if filters.get("work_order_number"):
        query = query.filter(WorkOrder.work_order_number.ilike(f"%{filters['work_order_number']}%"))
    if filters.get("mine_name"):
        query = query.join(Mine, WorkOrder.mine_id == Mine.id, isouter=True).filter(Mine.name.ilike(f"%{filters['mine_name']}%"))
    if filters.get("account_name"):
        query = query.filter(WorkOrder.account_name.ilike(f"%{filters['account_name']}%"))
    if filters.get("status"):
        query = query.filter(WorkOrder.status == filters["status"].upper())
    if filters.get("min_freight"):
        try:
            query = query.filter(WorkOrder.total_freight >= float(filters["min_freight"]))
        except (ValueError, TypeError):
            pass
    if filters.get("max_freight"):
        try:
            query = query.filter(WorkOrder.total_freight <= float(filters["max_freight"]))
        except (ValueError, TypeError):
            pass
    if filters.get("min_balance"):
        try:
            query = query.filter(WorkOrder.balance >= float(filters["min_balance"]))
        except (ValueError, TypeError):
            pass
    if filters.get("max_balance"):
        try:
            query = query.filter(WorkOrder.balance <= float(filters["max_balance"]))
        except (ValueError, TypeError):
            pass

    results = query.options(
        db.joinedload(WorkOrder.mine)
    ).order_by(WorkOrder.date.desc()).limit(50).all()

    results_data = []
    for wo in results:
        results_data.append({
            "id": wo.id,
            "date": wo.date.isoformat() if wo.date else None,
            "lorry_number": wo.lorry_number or "",
            "work_order_number": wo.work_order_number or "",
            "mine_name": wo.mine.name if wo.mine else "",
            "account_name": wo.account_name or "",
            "total_freight": float(wo.total_freight or 0),
            "balance": float(wo.balance or 0),
            "status": wo.status or "",
            "rate": float(wo.rate or 0),
            "cash": float(wo.cash or 0),
        })

    return jsonify({
        "results": results_data,
        "filters": filters,
        "query": query_text,
        "count": len(results_data),
    })


@work_order_records_bp.route("/ai-map", methods=["POST"])
@login_required
def ai_map():
    data = request.get_json()
    if not data:
        return jsonify({})

    headers = data.get("headers", [])
    sample_rows = data.get("sampleRows", [])

    groq_api_key = _groq_api_key()
    if not groq_api_key:
        return jsonify({})

    # Build field descriptions prompt
    field_descriptions = "Work order CSV columns and their meanings:\n"
    for col_idx, (_, label, _) in enumerate(work_order_fields):
        field_descriptions += f"{col_idx}: {label}\n"

    # Add sample rows context
    if sample_rows:
        field_descriptions += "\nSample data rows:\n"
        for i, row in enumerate(sample_rows[:5]):
            field_descriptions += f"Row {i}: {row}\n"

    prompt = f"""{field_descriptions}

Based on the headers and sample rows above, map each column index to the corresponding work order field name.
Return a JSON object mapping column index (string) to field name (string).
Only include columns with high confidence (>=0.7 matching).
If unsure, omit the column.
Return ONLY the JSON object, no other text.
"""

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {groq_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "openai/gpt-oss-120b",
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant that maps CSV column indices to work order field names."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
            },
            timeout=15,
        )

        if response.status_code != 200:
            return jsonify({})

        result = response.json()
        choice = result.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content", "").strip()

        # Parse JSON from content
        mapping = json.loads(content)
        if isinstance(mapping, dict):
            # Filter to high-confidence mappings only (keys are strings, values are field names)
            high_conf = {k: v for k, v in mapping.items() if isinstance(k, str) and v}
            return jsonify(high_conf)

        return jsonify({})
    except (requests.RequestException, json.JSONDecodeError, KeyError, ValueError):
        return jsonify({})


@work_order_records_bp.route("/tds")
@login_required
def tds_page():
    today = datetime.today().strftime("%Y-%m-%d")
    date_from = request.args.get("date_from", today)
    date_to = request.args.get("date_to", today)

    query = WorkOrder.query.filter(WorkOrder.tds != 0)

    if date_from:
        query = query.filter(WorkOrder.date >= datetime.strptime(date_from, "%Y-%m-%d").date())
    if date_to:
        query = query.filter(WorkOrder.date <= datetime.strptime(date_to, "%Y-%m-%d").date())

    rows = query.options(
        db.joinedload(WorkOrder.transporter),
    ).order_by(WorkOrder.date.desc(), WorkOrder.id).all()

    entries = []
    total_tds = 0.0
    total_freight = 0.0
    total_balance = 0.0
    for wo in rows:
        tds = round(float(wo.tds or 0), 2)
        freight = round(float(wo.total_freight or 0), 2)
        tds_percent = round(float(wo.tds_percent or 0), 2)
        balance = round(float(wo.balance or 0), 2)
        total_tds += tds
        total_freight += freight
        total_balance += balance
        transporter = wo.transporter or _transporter_by_truck(wo.lorry_number)
        account_name = wo.account_name or "-"
        if transporter and transporter.name and (account_name == "-" or not wo.account_name):
            account_name = transporter.name
        entries.append({
            "id": wo.id,
            "date": wo.date.isoformat(),
            "payable_date": wo.payable_date.isoformat() if wo.payable_date else "",
            "lorry_number": wo.lorry_number,
            "account_name": account_name,
            "remark": "NOT DECLARATION" if tds > 0 else "DECLARATION",
            "pan_card": transporter.pan_card if transporter and transporter.pan_card else "",
            "freight": freight,
            "tds_percent": tds_percent,
            "tds": tds,
            "balance": balance,
            "status": wo.status,
        })

    if request.args.get("export") in ("csv", "xlsx"):
        headers = ["#", "Payable Date", "Date", "Truck #", "Account Name", "Remark", "PAN Card", "Freight", "TDS %", "TDS Amount", "Balance", "Status"]
        export_rows = [
            [
                i,
                e["payable_date"] or "",
                e["date"],
                e["lorry_number"],
                e["account_name"],
                e["remark"],
                e["pan_card"] if e["pan_card"] else "",
                e["freight"],
                e["tds_percent"],
                e["tds"],
                e["balance"],
                e["status"],
            ]
            for i, e in enumerate(entries, start=1)
        ]
        return generate_xlsx(headers, export_rows, filename=f"tds_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")

    return render_template(
        "work_order_records/tds.html",
        entries=entries,
        date_from=date_from,
        date_to=date_to,
        today=today,
        total_tds=round(total_tds, 2),
        total_freight=round(total_freight, 2),
        total_balance=round(total_balance, 2),
    )


@work_order_records_bp.route("/tds/edit/<int:wo_id>", methods=["POST"])
@login_required
def tds_edit(wo_id):
    wo = WorkOrder.query.get_or_404(wo_id)
    data = request.get_json() or {}
    account_name = (data.get("account_name") or wo.account_name or "").strip()
    pan_card = (data.get("pan_card") or "").strip().upper()

    supplier = wo.transporter or _transporter_by_truck(wo.lorry_number)
    try:
        if supplier:
            if account_name and account_name != "-":
                supplier.name = account_name
            if pan_card:
                supplier.pan_card = pan_card
            if not any(t.lorry_number == wo.lorry_number for t in supplier.trucks):
                supplier.trucks.append(TransporterTruck(lorry_number=wo.lorry_number))
        elif account_name and account_name != "-":
            supplier = Transporter(name=account_name, pan_card=pan_card or None)
            db.session.add(supplier)
            db.session.flush()
            if wo.lorry_number and wo.lorry_number != "-":
                supplier.trucks.append(TransporterTruck(lorry_number=wo.lorry_number))

        if account_name and account_name != "-":
            wo.account_name = account_name
        if supplier:
            wo.transporter_id = supplier.id
        log_audit("update", "work_order", wo.id, f"TDS edit on WO#{wo.id}: account={account_name or '-'}, PAN={pan_card or '-'}")
        db.session.commit()
        return jsonify({"ok": True, "pan_card": (supplier.pan_card or "") if supplier else "", "account_name": wo.account_name or ""})
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500

