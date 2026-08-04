from datetime import datetime, timezone

from app.extensions import db


def utc_now():
    return datetime.now(timezone.utc)


class InstitutionAudienceInsightCache(db.Model):
    __tablename__ = "institution_audience_insight_cache"
    __table_args__ = (
        db.UniqueConstraint(
            "scope_type",
            "scope_id",
            "period_key",
            name="uq_institution_audience_cache_scope",
        ),
        db.CheckConstraint(
            "scope_type in ('branch','organization')",
            name="ck_institution_audience_cache_scope",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    scope_type = db.Column(db.String(20), nullable=False)
    scope_id = db.Column(db.Integer, nullable=False)
    period_key = db.Column(db.String(30), nullable=False)
    data_digest = db.Column(db.String(64), nullable=False)
    aggregate_payload = db.Column(db.JSON, nullable=False)
    analysis_text = db.Column(db.Text, nullable=False)
    model_name = db.Column(db.String(100), nullable=True)
    source = db.Column(db.String(30), nullable=False, default="deterministic")
    generated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)

    def to_dict(self):
        return {
            "scope": self.scope_type,
            "scope_id": self.scope_id,
            "period_key": self.period_key,
            "aggregate": self.aggregate_payload or {},
            "analysis_text": self.analysis_text,
            "model": self.model_name,
            "source": self.source,
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }
