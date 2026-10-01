from datetime import date

from app import create_app, db
from app.models import (
    Transporter,
    TransporterTruck,
    Plant,
    Mine,
    Trip,
    Expense,
    Payment,
    WorkOrder,
    PetrolStation,
)


def get_or_create_transporter(name, pan, bank, ifsc, contact, tds_rate, trucks):
    t = Transporter.query.filter_by(name=name).first()
    if t:
        return t, False
    t = Transporter(
        name=name,
        pan_card=pan,
        bank_account=bank,
        ifsc_code=ifsc,
        contact=contact,
        tds_rate=tds_rate,
    )
    db.session.add(t)
    db.session.flush()
    for lorry in trucks:
        db.session.add(TransporterTruck(transporter_id=t.id, lorry_number=lorry))
    return t, True


def get_or_create_plant(name, location):
    p = Plant.query.filter_by(name=name).first()
    if p:
        return p, False
    p = Plant(name=name, location=location)
    db.session.add(p)
    db.session.flush()
    return p, True


def get_or_create_mine(name, plant_id):
    m = Mine.query.filter_by(name=name).first()
    if m:
        return m, False
    m = Mine(name=name, plant_id=plant_id)
    db.session.add(m)
    db.session.flush()
    return m, True


def make_trip(d, lorry, transporter_id, plant_id, mine_id, freight, tds_percent, mines_qty, expenses=(), payments=()):
    trip = Trip(
        date=d,
        lorry_number=lorry,
        transporter_id=transporter_id,
        plant_id=plant_id,
        mine_id=mine_id,
        mines_qty=mines_qty,
        total_freight=freight,
        tds_percent=tds_percent,
    )
    db.session.add(trip)
    db.session.flush()
    for desc, amt in expenses:
        db.session.add(Expense(trip_id=trip.id, description=desc, amount=amt))
    for method, amt, exec_date, ref in payments:
        db.session.add(Payment(
            trip_id=trip.id,
            payment_method=method,
            amount=amt,
            execution_date=exec_date,
            beneficiary_name=transporter_name(transporter_id),
            reference_number=ref,
        ))
    trip.recalculate()
    return trip


def transporter_name(tid):
    t = Transporter.query.get(tid)
    return t.name if t else None


def make_wo(
    wo_number, d, lorry, mine_id, tid, mines_qty, plant_qty, rate,
    cash, loading, petrol, acc_adv, tds_pct, dd_from, dd_to,
    tds_manual=None, payable=None, account_name=None, remark=None,
):
    wo = WorkOrder(
        work_order_number=wo_number,
        date=d,
        lorry_number=lorry,
        mine_id=mine_id,
        transporter_id=tid,
        tds_percent=tds_pct,
        ddtds_from=dd_from,
        ddtds_to=dd_to,
        account_advance=acc_adv,
        mines_qty=mines_qty,
        plant_qty=plant_qty,
        rate=rate,
        cash=cash,
        loading=loading,
        account_name=account_name,
        payable_date=payable,
        remark=remark,
        tds_auto=tds_manual is None,
    )
    db.session.add(wo)
    db.session.flush()
    for name, amt in petrol:
        db.session.add(PetrolStation(work_order_id=wo.id, name=name, amount=amt))
    wo.recalculate()
    if tds_manual is not None:
        wo.tds = tds_manual
        wo.tds_auto = False
        wo.recalculate()
    wo.payable_date = payable
    return wo


def main():
    app = create_app()
    with app.app_context():
        today = date(2026, 9, 23)

        transporters = [
            ("SHARMA ROADLINES", "AABC1234X", "0000001234567890", "SBIN0011111", "9876543210", 1.0, ["RJ14PB1234", "RJ14PB5678"]),
            ("GUPTA TRANSPORT CO", "AABD5678Y", "0000002345678901", "HDFC0002222", "9876543211", 2.0, ["MH12AB4321", "MH12AB8765"]),
            ("MALHOTRA LOGISTICS", "AABE9101Z", "0000003456789012", "ICIC0003333", "9876543212", 0.0, ["DL01CD2345"]),
            ("SINGH CARRIERS", "AABF1111A", "0000004567890123", "PNB0004444", "9876543213", 5.0, ["PB10EF6789"]),
            ("YADAV MOVERS", "AABG2222B", "0000005678901234", "AXIS0005555", "9876543214", 1.0, ["MP09GH3456"]),
            ("PATEL TRANSPORT CO", "AABH3333C", "0000006789012345", "BOB0006666", "9876543215", 2.0, ["GJ01IJ7890"]),
            ("BHATT EXPRESS", "AABJ4444D", "0000007890123456", "SBI0007777", "9876543216", 0.0, ["GJ05KL1234"]),
            ("JOSHI FREIGHT LINE", "AABK5555E", "0000008901234567", "KOTAK008888", "9876543217", 3.0, ["MH15MN5678"]),
            ("RATHORE & SONS", "AABL6666F", "0000009012345678", "CANARA00099", "9876543218", 1.0, ["RJ20OP9012"]),
        ]

        created = {"transporters": 0}
        tids = {}
        for name, pan, bank, ifsc, contact, tds_rate, trucks in transporters:
            t, is_new = get_or_create_transporter(name, pan, bank, ifsc, contact, tds_rate, trucks)
            tids[name] = t.id
            if is_new:
                created["transporters"] += 1

        plants = [
            ("STEELCO PLANT", "JAMSHEDPUR"),
            ("CENTURY CEMENT WORKS", "HOSAPETE"),
        ]
        created["plants"] = 0
        pids = {}
        for name, loc in plants:
            p, is_new = get_or_create_plant(name, loc)
            pids[name] = p.id
            if is_new:
                created["plants"] += 1

        mines = [
            ("IRON ORE MINE", pids["STEELCO PLANT"]),
            ("LIMESTONE QUARRY", pids["CENTURY CEMENT WORKS"]),
            ("COPPER MINE", None),
        ]
        created["mines"] = 0
        mids = {}
        for name, pid in mines:
            m, is_new = get_or_create_mine(name, pid)
            mids[name] = m.id
            if is_new:
                created["mines"] += 1

        ir = mids["IRON ORE MINE"]
        ls = mids["LIMESTONE QUARRY"]
        cp = mids["COPPER MINE"]
        sp = pids["STEELCO PLANT"]
        cw = pids["CENTURY CEMENT WORKS"]

        trip_specs = [
            (today, "RJ14PB1234", tids["SHARMA ROADLINES"], sp, ir, 45000, 1.0, 22.5, [("Diesel", 2000), ("Highway toll", 500)], [("UPI", 20000, today, "UTR-101")]),
            (today, "MH12AB4321", tids["GUPTA TRANSPORT CO"], sp, ir, 52000, 2.0, 26.0, [("Diesel", 2500)], [("CASH", 50000, today, "CASH-102")]),
            (date(2026, 9, 22), "DL01CD2345", tids["MALHOTRA LOGISTICS"], cw, ls, 38000, 1.0, 19.0, [("Loading charge", 800)], [("BANK", 38000, date(2026, 9, 22), "BANK-103")]),
            (date(2026, 9, 22), "PB10EF6789", tids["SINGH CARRIERS"], cw, ls, 61000, 5.0, 30.5, [("Diesel", 3000), ("Toll", 400)], [("CHEQUE", 30000, date(2026, 9, 22), "CHQ-104")]),
            (date(2026, 9, 21), "MP09GH3456", tids["YADAV MOVERS"], sp, cp, 47000, 1.0, 23.5, [("Diesel", 1500)], [("UPI", 47000, date(2026, 9, 21), "UTR-105")]),
            (date(2026, 9, 21), "GJ01IJ7890", tids["PATEL TRANSPORT CO"], cw, ls, 40000, 2.0, 20.0, [], [("CASH", 40000, date(2026, 9, 21), "CASH-106")]),
            (date(2026, 9, 20), "GJ05KL1234", tids["BHATT EXPRESS"], sp, ir, 55000, 1.0, 27.5, [("Diesel", 2200)], [("UPI", 55000, date(2026, 9, 20), "UTR-107")]),
            (date(2026, 9, 20), "MH15MN5678", tids["JOSHI FREIGHT LINE"], cw, ls, 48000, 3.0, 24.0, [("Toll", 600)], [],),
            (date(2026, 9, 19), "RJ20OP9012", tids["RATHORE & SONS"], sp, ir, 43000, 1.0, 21.5, [("Diesel", 1800)], [("BANK", 43000, date(2026, 9, 19), "BANK-108")]),
        ]
        created["trips"] = 0
        for spec in trip_specs:
            t = Trip.query.filter_by(date=spec[0], lorry_number=spec[1]).first()
            if t:
                continue
            make_trip(*spec)
            created["trips"] += 1

        wo_specs = [
            dict(wo_number="WO-1001", d=today, lorry="RJ14PB1234", mine_id=ir, tid=tids["SHARMA ROADLINES"], mines_qty=50.0, plant_qty=48.5, rate=2000, cash=20000, loading=500, petrol=[("HP Petrol", 3000), ("Indian Oil", 2000)], acc_adv=0, tds_pct=1.0, dd_from=date(2026, 5, 1), dd_to=date(2026, 5, 15), account_name="SHARMA ROADLINES", remark="Demo entry 1"),
            dict(wo_number="WO-1002", d=today, lorry="MH12AB4321", mine_id=ir, tid=tids["GUPTA TRANSPORT CO"], mines_qty=40.0, plant_qty=39.8, rate=1800, cash=15000, loading=300, petrol=[("Shell", 4000)], acc_adv=10000, tds_pct=2.0, dd_from=date(2025, 12, 15), dd_to=date(2025, 12, 31), account_name="GUPTA TRANSPORT CO", remark="TDS applied"),
            dict(wo_number="WO-1003", d=today, lorry="DL01CD2345", mine_id=ls, tid=tids["MALHOTRA LOGISTICS"], mines_qty=55.0, plant_qty=54.0, rate=1500, cash=18000, loading=0, petrol=[], acc_adv=0, tds_pct=1.0, dd_from=date(2026, 6, 1), dd_to=date(2026, 6, 30), payable=today, account_name="MALHOTRA LOGISTICS", remark="TDS exempt FY 2026-27"),
            dict(wo_number="WO-1004", d=date(2026, 9, 22), lorry="PB10EF6789", mine_id=ls, tid=tids["SINGH CARRIERS"], mines_qty=60.0, plant_qty=58.0, rate=2200, cash=25000, loading=1000, petrol=[("BPCL", 5000)], acc_adv=0, tds_pct=5.0, dd_from=date(2025, 10, 1), dd_to=date(2025, 10, 31), account_name="SINGH CARRIERS", remark="High TDS 5%"),
            dict(wo_number="WO-1005", d=date(2026, 9, 22), lorry="MP09GH3456", mine_id=cp, tid=tids["YADAV MOVERS"], mines_qty=45.0, plant_qty=44.0, rate=1700, cash=12000, loading=200, petrol=[("Indian Oil", 2500)], acc_adv=0, tds_pct=1.0, dd_from=date(2026, 7, 1), dd_to=date(2026, 7, 31), payable=date(2026, 9, 22), account_name="YADAV MOVERS"),
            dict(wo_number="WO-1006", d=date(2026, 9, 21), lorry="GJ01IJ7890", mine_id=ls, tid=tids["PATEL TRANSPORT CO"], mines_qty=38.0, plant_qty=36.5, rate=1400, cash=10000, loading=400, petrol=[("HP Petrol", 1500), ("Shell", 1000)], acc_adv=5000, tds_pct=2.0, dd_from=date(2026, 1, 5), dd_to=date(2026, 1, 20), account_name="PATEL TRANSPORT CO", remark="Shortage case"),
            dict(wo_number="WO-1007", d=date(2026, 9, 21), lorry="GJ05KL1234", mine_id=ir, tid=tids["BHATT EXPRESS"], mines_qty=70.0, plant_qty=69.0, rate=1900, cash=30000, loading=500, petrol=[], acc_adv=0, tds_pct=1.0, dd_from=date(2026, 8, 1), dd_to=date(2026, 8, 31), payable=date(2026, 9, 21), account_name="BHATT EXPRESS", remark="TDS 0"),
            dict(wo_number="WO-1008", d=date(2026, 9, 20), lorry="MH15MN5678", mine_id=ls, tid=tids["JOSHI FREIGHT LINE"], mines_qty=52.0, plant_qty=50.0, rate=2100, cash=22000, loading=600, petrol=[("Reliance", 3500)], acc_adv=0, tds_pct=3.0, dd_from=date(2025, 11, 10), dd_to=date(2025, 11, 25), account_name="JOSHI FREIGHT LINE", remark="Previous FY TDS 3%"),
            dict(wo_number="WO-1009", d=date(2026, 9, 20), lorry="RJ20OP9012", mine_id=ir, tid=tids["RATHORE & SONS"], mines_qty=33.0, plant_qty=32.2, rate=1600, cash=8000, loading=300, petrol=[], acc_adv=2000, tds_pct=1.0, dd_from=date(2026, 4, 1), dd_to=date(2026, 4, 30), payable=date(2026, 9, 20), account_name="RATHORE & SONS"),
            dict(wo_number="WO-1010", d=date(2026, 9, 19), lorry="RJ14PB5678", mine_id=cp, tid=tids["SHARMA ROADLINES"], mines_qty=62.0, plant_qty=61.0, rate=2300, cash=20000, loading=400, petrol=[("Bharat Oil", 2800)], acc_adv=0, tds_pct=1.0, dd_from=date(2025, 8, 1), dd_to=date(2025, 8, 15), tds_manual=1400, account_name="SHARMA ROADLINES", remark="Manual TDS override"),
            dict(wo_number="WO-1011", d=date(2026, 9, 19), lorry="MH12AB8765", mine_id=ir, tid=tids["GUPTA TRANSPORT CO"], mines_qty=48.0, plant_qty=47.0, rate=1750, cash=14000, loading=250, petrol=[], acc_adv=0, tds_pct=2.0, dd_from=date(2026, 9, 1), dd_to=date(2026, 9, 15), account_name="GUPTA TRANSPORT CO"),
            dict(wo_number="WO-1012", d=today, lorry="PB10EF6789", mine_id=ir, tid=tids["SINGH CARRIERS"], mines_qty=41.0, plant_qty=40.0, rate=1950, cash=11000, loading=350, petrol=[("Indian Oil", 2000), ("HP Petrol", 1200), ("Shell", 800), ("Reliance", 700)], acc_adv=0, tds_pct=5.0, dd_from=date(2026, 2, 1), dd_to=date(2026, 2, 10), account_name="SINGH CARRIERS", remark="4 petrol slots"),
        ]
        created["work_orders"] = 0
        for spec in wo_specs:
            dup = WorkOrder.query.filter_by(work_order_number=spec["wo_number"]).first()
            if dup:
                continue
            make_wo(**spec)
            created["work_orders"] += 1

        db.session.commit()

        print("Created:", created)
        print("Total in DB -> Transporters:", Transporter.query.count(),
              "| Plants:", Plant.query.count(),
              "| Mines:", Mine.query.count(),
              "| Trips:", Trip.query.count(),
              "| WorkOrders:", WorkOrder.query.count())


if __name__ == "__main__":
    main()