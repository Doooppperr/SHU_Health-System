from app.extensions import db


class IndicatorCategory(db.Model):
    __tablename__ = "indicator_categories"
    __table_args__ = (
        db.CheckConstraint("length(trim(name)) > 0", name="ck_indicator_categories_name_not_blank"),
    )

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
        db.CheckConstraint("length(trim(code)) > 0", name="ck_indicator_dicts_code_not_blank"),
        db.CheckConstraint("length(trim(name)) > 0", name="ck_indicator_dicts_name_not_blank"),
        db.CheckConstraint("value_type in ('numeric', 'text')", name="ck_indicator_dicts_value_type"),
        db.CheckConstraint(
            "reference_low is null or reference_high is null or reference_low <= reference_high",
            name="ck_indicator_dicts_reference_range",
        ),
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
    allow_self_measurement = db.Column(
        db.Boolean, nullable=False, default=False, server_default=db.false()
    )

    report_indicators = db.relationship("ReportIndicator", back_populates="indicator_dict")
    domain_links = db.relationship("IndicatorDomainLink", back_populates="indicator", cascade="all, delete-orphan")
    reference_rules = db.relationship(
        "IndicatorReferenceRule",
        back_populates="indicator",
        cascade="all, delete-orphan",
        order_by="IndicatorReferenceRule.priority.desc(), IndicatorReferenceRule.id.asc()",
    )

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
            "allow_self_measurement": self.allow_self_measurement,
            "domains": [
                {"id": link.domain.id, "code": link.domain.code, "name": link.domain.name,
                 "is_primary": link.is_primary, "sort_order": link.sort_order}
                for link in sorted(self.domain_links, key=lambda row: (not row.is_primary, row.sort_order, row.id))
                if link.domain
            ],
        }


class IndicatorReferenceRule(db.Model):
    __tablename__ = "indicator_reference_rules"
    __table_args__ = (
        db.CheckConstraint(
            "gender_scope in ('all', 'male', 'female', 'other')",
            name="ck_indicator_reference_rules_gender",
        ),
        db.CheckConstraint(
            "reference_low is null or reference_high is null or reference_low <= reference_high",
            name="ck_indicator_reference_rules_range",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    indicator_dict_id = db.Column(
        db.Integer,
        db.ForeignKey("indicator_dicts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    gender_scope = db.Column(db.String(20), nullable=False, default="all", server_default="all")
    min_age = db.Column(db.Integer, nullable=True)
    max_age = db.Column(db.Integer, nullable=True)
    reference_low = db.Column(db.Numeric(12, 4), nullable=True)
    reference_high = db.Column(db.Numeric(12, 4), nullable=True)
    reference_text = db.Column(db.String(255), nullable=True)
    source_title = db.Column(db.String(200), nullable=True)
    source_url = db.Column(db.String(500), nullable=True)
    priority = db.Column(db.Integer, nullable=False, default=0, server_default="0")

    indicator = db.relationship("IndicatorDict", back_populates="reference_rules")

    def to_dict(self):
        return {
            "id": self.id,
            "indicator_dict_id": self.indicator_dict_id,
            "gender_scope": self.gender_scope,
            "min_age": self.min_age,
            "max_age": self.max_age,
            "reference_low": float(self.reference_low) if self.reference_low is not None else None,
            "reference_high": float(self.reference_high) if self.reference_high is not None else None,
            "reference_text": self.reference_text,
            "source_title": self.source_title,
            "source_url": self.source_url,
            "priority": self.priority,
        }
