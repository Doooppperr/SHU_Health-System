from .account_security import PasswordVerificationChallenge
from .comment import Comment, CommentReply
from .friend import FriendRelation
from .health import InstitutionReport, ReportIndicator, SelfMeasurement
from .indicator import IndicatorCategory, IndicatorDict
from .institution import Appointment, Institution, Package, PackageChangeRequest
from .institution_image import InstitutionImage
from .institution_invite import InstitutionInvite
from .user import User
from .v7 import (
    AppointmentCapacitySlot, AppointmentEvent, AvailabilityNotificationEvent,
    BookingGroup, HealthDomain, IndicatorDomainLink, NotificationDelivery,
    NotificationOutbox, PackageVersion, PackageVersionDomain, ReportAsset,
    ReportAssetAnnotation, ReportTextResult, WaitlistSubscription,
    WaitlistSubscriptionParticipant,
)
from .v8 import Organization, ReportAccessLog

# Internal compatibility names for the existing AI reasoning layer only.  The
# old health_records/health_indicators tables and public CRUD routes are gone.
HealthRecord = InstitutionReport
HealthIndicator = ReportIndicator

__all__ = [
    "User", "Comment", "CommentReply", "PasswordVerificationChallenge", "FriendRelation", "Organization", "Institution", "InstitutionImage",
    "InstitutionInvite", "Package", "Appointment", "PackageChangeRequest", "IndicatorCategory", "IndicatorDict",
    "SelfMeasurement", "InstitutionReport", "ReportIndicator",
    "HealthDomain", "IndicatorDomainLink", "PackageVersion", "PackageVersionDomain",
    "BookingGroup", "AppointmentEvent", "AppointmentCapacitySlot",
    "WaitlistSubscription", "WaitlistSubscriptionParticipant",
    "AvailabilityNotificationEvent", "NotificationOutbox", "NotificationDelivery",
    "ReportTextResult", "ReportAsset", "ReportAssetAnnotation", "ReportAccessLog",
    "HealthRecord", "HealthIndicator",
]
