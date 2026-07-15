from flask import g

from app.exam_reports import exam_reports_bp
from app.models import InstitutionReport
from app.services.permissions import ROLE_USER, roles_required


@exam_reports_bp.get("")
@roles_required(ROLE_USER)
def list_reports():
    rows = InstitutionReport.query.filter_by(matched_user_id=g.current_user.id, status="published").order_by(InstitutionReport.exam_date.desc()).all()
    return {"items": [row.to_dict(user_view=True) for row in rows]}, 200


@exam_reports_bp.get("/<int:report_id>")
@roles_required(ROLE_USER)
def get_report(report_id):
    row = InstitutionReport.query.filter_by(id=report_id, matched_user_id=g.current_user.id, status="published").first()
    if not row: return {"message": "report not found"}, 404
    return {"item": row.to_dict(include_indicators=True, user_view=True)}, 200
