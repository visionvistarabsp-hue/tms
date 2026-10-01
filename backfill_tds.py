"""Backfill TDS for existing work orders."""
from app import create_app, db
from app.models import WorkOrder, calculate_tds


def backfill_tds():
    """Recalculate TDS for all existing work orders."""
    app = create_app()
    with app.app_context():
        work_orders = WorkOrder.query.all()
        updated = 0

        for wo in work_orders:
            new_tds = calculate_tds(wo, wo.transporter)

            if float(wo.tds or 0) != new_tds:
                wo.tds = new_tds
                wo.tds_auto = True
                updated += 1

        db.session.commit()
        print(f"Updated {updated} work orders")


if __name__ == "__main__":
    backfill_tds()
