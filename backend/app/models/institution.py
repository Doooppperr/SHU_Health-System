from decimal import Decimal

from app.extensions import db


class Institution(db.Model):
    __tablename__ = "institutions"
    __table_args__ = (
        db.UniqueConstraint("name", "branch_name", name="uq_institution_branch"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    branch_name = db.Column(db.String(120), nullable=False)
    address = db.Column(db.String(255), nullable=False)
    district = db.Column(db.String(80), nullable=False)
    metro_info = db.Column(db.String(255), nullable=True)
    consult_phone = db.Column(db.String(30), nullable=True)
    ext = db.Column(db.String(20), nullable=True)
    closed_day = db.Column(db.String(20), nullable=True)
    description = db.Column(db.Text, nullable=True)
    logo_url = db.Column(db.String(255), nullable=True)

    packages = db.relationship("Package", back_populates="institution", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "branch_name": self.branch_name,
            "address": self.address,
            "district": self.district,
            "metro_info": self.metro_info,
            "consult_phone": self.consult_phone,
            "ext": self.ext,
            "closed_day": self.closed_day,
            "description": self.description,
            "logo_url": self.logo_url,
            "package_count": len(self.packages),
        }


class Package(db.Model):
    __tablename__ = "packages"
    __table_args__ = (
        db.UniqueConstraint("institution_id", "name", name="uq_package_institution_name"),
    )

    id = db.Column(db.Integer, primary_key=True)
    institution_id = db.Column(db.Integer, db.ForeignKey("institutions.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    focus_area = db.Column(db.String(120), nullable=False)
    gender_scope = db.Column(db.String(40), nullable=False, default="all")
    price = db.Column(db.Numeric(10, 2), nullable=False, default=Decimal("0.00"))
    description = db.Column(db.Text, nullable=True)

    institution = db.relationship("Institution", back_populates="packages")

    def to_dict(self):
        return {
            "id": self.id,
            "institution_id": self.institution_id,
            "name": self.name,
            "focus_area": self.focus_area,
            "gender_scope": self.gender_scope,
            "price": float(self.price),
            "description": self.description,
        }
