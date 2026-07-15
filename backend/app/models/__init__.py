from .comment import Comment
from .friend import FriendRelation
from .health import InstitutionReport, ReportIndicator, SelfMeasurement
from .indicator import IndicatorCategory, IndicatorDict
from .institution import Institution, Package
from .institution_image import InstitutionImage
from .institution_invite import InstitutionInvite
from .user import User

# Internal compatibility names for the existing AI reasoning layer only.  The
# old health_records/health_indicators tables and public CRUD routes are gone.
HealthRecord = InstitutionReport
HealthIndicator = ReportIndicator

__all__ = [
    "User", "Comment", "FriendRelation", "Institution", "InstitutionImage",
    "InstitutionInvite", "Package", "IndicatorCategory", "IndicatorDict",
    "SelfMeasurement", "InstitutionReport", "ReportIndicator",
    "HealthRecord", "HealthIndicator",
]
