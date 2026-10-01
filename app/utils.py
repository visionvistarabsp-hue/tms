import io
from datetime import datetime
from flask import Response
from flask_login import current_user
from app import db
from app.models import AuditLog


def log_audit(action, entity_type, entity_id=None, details=None, user_id=None):
    if user_id is None:
        try:
            user_id = current_user.id if current_user.is_authenticated else None
        except Exception:
            user_id = None
    log = AuditLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
    )
    db.session.add(log)


def validate_positive(value, name):
    try:
        v = float(value)
        if v < 0:
            raise ValueError(f"{name} cannot be negative")
        return v
    except (TypeError, ValueError):
        raise ValueError(f"Invalid {name}")


def generate_xlsx(headers, rows, filename=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment

    wb = Workbook()
    ws = wb.active
    ws.title = "Report"
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
    for row in rows:
        ws.append(["" if v is None else v for v in row])
    for col_idx, width in enumerate(_column_widths(headers, rows), start=1):
        ws.column_dimensions[chr(64 + col_idx)].width = width

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    name = filename or f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return Response(
        output.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment;filename={name}"},
    )


def _column_widths(headers, rows):
    initials = 10
    out = [initials] * len(headers)
    for i in range(len(headers)):
        w = len(str(headers[i]))
        for row in rows[:200]:
            if i < len(row):
                w = max(w, len(str(row[i])))
        out[i] = min(max(w + 3, initials), 40)
    return out
