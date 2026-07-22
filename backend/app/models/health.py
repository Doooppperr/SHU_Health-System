from datetime import datetime, timezone

from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import synonym

from app.extensions import db
from app.services.dates import calendar_date_iso


def utc_now():
    return datetime.now(timezone.utc)


class SelfMeasurement(db.Model):
    __tablename__ = "self_measurements"
    __table_args__ = (
        db.CheckConstraint("value >= 0", name="ck_self_measurements_value_non_negative"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    indicator_dict_id = db.Column(db.Integer, db.ForeignKey("indicator_dicts.id"), nullable=False, index=True)
    value = db.Column(db.Numeric(14, 4), nullable=False)
    measured_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    user = db.relationship("User")
    indicator_dict = db.relationship("IndicatorDict")

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "indicator_dict_id": self.indicator_dict_id,
            "value": float(self.value),
            "unit": self.indicator_dict.unit if self.indicator_dict else None,
            "indicator": self.indicator_dict.to_dict() if self.indicator_dict else None,
            "measured_at": self.measured_at.isoformat() if self.measured_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "source": "self_measurement",
        }


class InstitutionReport(db.Model):
    __tablename__ = "institution_reports"
    __table_args__ = (
        db.CheckConstraint(
            "status in ('draft', 'locked', 'published')",
            name="ck_institution_reports_status",
        ),
        db.CheckConstraint("length(trim(subject_name_snapshot)) > 0", name="ck_institution_reports_subject_name"),
        db.CheckConstraint("length(trim(subject_health_id)) > 0", name="ck_institution_reports_subject_health_id"),
        db.UniqueConstraint(
            "institution_id", "subject_health_id", "exam_date",
            name="uq_institution_reports_subject_date",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    institution_id = db.Column(db.Integer, db.ForeignKey("institutions.id"), nullable=False, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_username_snapshot = db.Column(db.String(80), nullable=False)
    subject_name_snapshot = db.Column(db.String(80), nullable=False)
    subject_health_id = db.Column(db.String(20), nullable=False, index=True)
    exam_date = db.Column(db.Date, nullable=False, index=True)
    package_id = db.Column(db.Integer, db.ForeignKey("packages.id", ondelete="SET NULL"), nullable=True)
    package_version_id = db.Column(db.Integer, db.ForeignKey("package_versions.id", ondelete="SET NULL"), nullable=True, index=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True, unique=True, index=True)
    matched_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    status = db.Column(db.String(24), nullable=False, default="draft", index=True)
    ocr_diagnostics = db.Column(db.JSON, nullable=True)
    temporary_file_url = db.Column(db.String(255), nullable=True)
    locked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    submitted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    published_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    institution = db.relationship("Institution")
    creator = db.relationship("User", foreign_keys=[created_by_user_id])
    owner = db.relationship("User", foreign_keys=[matched_user_id])
    package = db.relationship("Package")
    package_version = db.relationship("PackageVersion")
    appointment = db.relationship("Appointment", back_populates="report")
    indicators = db.relationship("ReportIndicator", back_populates="report", cascade="all, delete-orphan", order_by="ReportIndicator.id.asc()")
    text_results = db.relationship("ReportTextResult", back_populates="report", cascade="all, delete-orphan", order_by="ReportTextResult.sort_order")
    assets = db.relationship("ReportAsset", back_populates="report", cascade="all, delete-orphan", order_by="ReportAsset.sort_order")

    # Transitional internal aliases keep the mature AI fact builder usable while
    # the public v1 record API and old database tables are removed.
    owner_id = synonym("matched_user_id")

    @hybrid_property
    def display_id(self):
        return f"report{self.id}" if self.id is not None else None

    @hybrid_property
    def report_file_url(self):
        return None

    def to_dict(self, include_indicators=False, *, user_view=False):
        result = {
            "id": self.id,
            "display_id": self.display_id,
            "institution_id": self.institution_id,
            "package_id": self.package_id,
            "package_version_id": self.package_version_id,
            "appointment_id": self.appointment_id,
            "exam_date": calendar_date_iso(self.exam_date),
            "status": self.status,
            "subject_name_snapshot": self.subject_name_snapshot,
            "created_by_username_snapshot": self.created_by_username_snapshot,
            "locked_at": self.locked_at.isoformat() if self.locked_at else None,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "institution": {"id": self.institution.id, "name": self.institution.name, "branch_name": self.institution.branch_name} if self.institution else None,
            "package": {"id": self.package.id, "name": self.package.name} if self.package else None,
            "package_version": self.package_version.to_dict() if self.package_version else None,
            "indicator_count": len(self.indicators),
            "text_result_count": len(self.text_results),
            "asset_count": len(self.assets),
        }
        if not user_view:
            result["subject_health_id"] = self.subject_health_id
            result["ocr_diagnostics"] = self.ocr_diagnostics
        else:
            result.pop("created_by_username_snapshot", None)
        if include_indicators:
            result["indicators"] = [item.to_dict() for item in self.indicators]
            result["text_results"] = [item.to_dict() for item in self.text_results]
            result["assets"] = [item.to_dict(self.display_id) for item in self.assets]
        return result


class ReportIndicator(db.Model):
    __tablename__ = "report_indicators"
    __table_args__ = (
        db.UniqueConstraint("report_id", "indicator_dict_id", name="uq_report_indicator"),
        db.CheckConstraint("length(trim(value)) > 0", name="ck_report_indicators_value_not_blank"),
        db.CheckConstraint("input_source in ('manual', 'ocr')", name="ck_report_indicators_input_source"),
    )

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey("institution_reports.id", ondelete="CASCADE"), nullable=False, index=True)
    indicator_dict_id = db.Column(db.Integer, db.ForeignKey("indicator_dicts.id"), nullable=False, index=True)
    value = db.Column(db.String(120), nullable=False)
    is_abnormal = db.Column(db.Boolean, nullable=False, default=False)
    input_source = db.Column(db.String(20), nullable=False, default="manual")
    display_domain_id = db.Column(db.Integer, db.ForeignKey("health_domains.id"), nullable=True, index=True)
    original_name = db.Column(db.String(160), nullable=True)
    original_value = db.Column(db.String(160), nullable=True)
    original_unit = db.Column(db.String(40), nullable=True)
    normalized_unit = db.Column(db.String(40), nullable=True)
    reference_text = db.Column(db.String(255), nullable=True)
    method_snapshot = db.Column(db.String(160), nullable=True)
    abnormal_flag = db.Column(db.String(20), nullable=True)
    mapping_confidence = db.Column(db.Numeric(5, 4), nullable=True)
    mapping_status = db.Column(db.String(30), nullable=False, default="confirmed", server_default="confirmed")

    report = db.relationship("InstitutionReport", back_populates="indicators")
    indicator_dict = db.relationship("IndicatorDict", back_populates="report_indicators")
    display_domain = db.relationship("HealthDomain")
    record_id = synonym("report_id")
    source = synonym("input_source")

    def to_dict(self):
        return {
            "id": self.id,
            "report_id": self.report_id,
            "indicator_dict_id": self.indicator_dict_id,
            "value": self.value,
            "is_abnormal": self.is_abnormal,
            "input_source": self.input_source,
            "source": self.input_source,
            "indicator": self.indicator_dict.to_dict() if self.indicator_dict else None,
            "display_domain_id": self.display_domain_id,
            "original_name": self.original_name,
            "original_value": self.original_value,
            "original_unit": self.original_unit,
            "normalized_unit": self.normalized_unit,
            "reference_text": self.reference_text,
            "method": self.method_snapshot,
            "abnormal_flag": self.abnormal_flag,
            "mapping_confidence": float(self.mapping_confidence) if self.mapping_confidence is not None else None,
            "mapping_status": self.mapping_status,
        }
