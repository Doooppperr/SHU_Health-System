from app.extensions import db


class IndicatorCategory(db.Model):
    __tablename__ = "indicator_categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    indicators = db.relationship("IndicatorDict", back_populates="category", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "sort_order": self.sort_order,
        }


class IndicatorDict(db.Model):
    __tablename__ = "indicator_dicts"
    __table_args__ = (
        db.UniqueConstraint("category_id", "name", name="uq_indicator_category_name"),
    )

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey("indicator_categories.id"), nullable=False, index=True)
    code = db.Column(db.String(40), nullable=False, unique=True, index=True)
    name = db.Column(db.String(120), nullable=False)
    aliases = db.Column(db.JSON, nullable=False, default=list)
    unit = db.Column(db.String(40), nullable=True)
    reference_low = db.Column(db.Numeric(10, 2), nullable=True)
    reference_high = db.Column(db.Numeric(10, 2), nullable=True)
    clinical_significance = db.Column(db.Text, nullable=True)
    value_type = db.Column(db.String(20), nullable=False, default="numeric")

    category = db.relationship("IndicatorCategory", back_populates="indicators")
    indicators = db.relationship("HealthIndicator", back_populates="indicator_dict")

    def to_dict(self):
        return {
            "id": self.id,
            "category_id": self.category_id,
            "category_name": self.category.name if self.category else None,
            "code": self.code,
            "name": self.name,
            "aliases": self.aliases or [],
            "unit": self.unit,
            "reference_low": float(self.reference_low) if self.reference_low is not None else None,
            "reference_high": float(self.reference_high) if self.reference_high is not None else None,
            "clinical_significance": self.clinical_significance,
            "value_type": self.value_type,
        }
