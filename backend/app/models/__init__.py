from .account_security import PasswordVerificationChallenge
from .comment import Comment, CommentReply
from .complaint import AppointmentComplaint, ComplaintEvent, ComplaintMessage
from .friend import DelegatedActionAudit, DelegationSessionAudit, FriendRelation
from .health import InstitutionReport, ReportIndicator, SelfMeasurement
from .indicator import IndicatorCategory, IndicatorDict, IndicatorReferenceRule
from .institution import Appointment, Institution, Package, PackageChangeRequest
from .institution_image import InstitutionImage
from .institution_invite import InstitutionInvite
from .user import User
from .v7 import (
    AppointmentCapacitySlot, AppointmentEvent, AvailabilityNotificationEvent,
    BookingGroup, BookingParticipantAuthorization, BookingParticipantToken,
    HealthDomain, IndicatorDomainLink, NotificationDelivery,
    NotificationOutbox, PackageVersion, PackageVersionDomain, ReportAsset,
    ReportAssetAnnotation, ReportTextResult, WaitlistSubscription,
    WaitlistSubscriptionParticipant,
)
from .v8 import Organization, ReportAccessLog
from .v10 import PackageVersionAssetRequirement, ReportAssetType, UserNotification
from .v11 import (
    AgentActionExecution,
    AgentPendingAction,
    AgentRun,
    AgentThread,
    AgentToolEvent,
    OAuthAccessToken,
    OAuthAuthorizationCode,
    OAuthClient,
    OAuthRefreshToken,
    SupportHandoff,
)
from .moderation import CommentAppeal, CommentSanction
from .analytics import InstitutionAudienceInsightCache
from .finance import (
    FinanceLedgerEntry,
    FinanceTransaction,
    PaymentOrder,
    PaymentOrderItem,
    RefundCase,
)

# Internal compatibility names for the existing AI reasoning layer only.  The
# old health_records/health_indicators tables and public CRUD routes are gone.
HealthRecord = InstitutionReport
HealthIndicator = ReportIndicator

__all__ = [
    "User", "Comment", "CommentReply", "CommentSanction", "CommentAppeal",
    "AppointmentComplaint", "ComplaintEvent", "ComplaintMessage",
    "InstitutionAudienceInsightCache",
    "PaymentOrder", "PaymentOrderItem", "FinanceTransaction",
    "FinanceLedgerEntry", "RefundCase",
    "PasswordVerificationChallenge", "FriendRelation", "DelegationSessionAudit",
    "DelegatedActionAudit",
    "Organization", "Institution", "InstitutionImage",
    "InstitutionInvite", "Package", "Appointment", "PackageChangeRequest", "IndicatorCategory", "IndicatorDict", "IndicatorReferenceRule",
    "SelfMeasurement", "InstitutionReport", "ReportIndicator",
    "HealthDomain", "IndicatorDomainLink", "PackageVersion", "PackageVersionDomain",
    "BookingGroup", "AppointmentEvent", "AppointmentCapacitySlot",
    "BookingParticipantToken", "BookingParticipantAuthorization",
    "WaitlistSubscription", "WaitlistSubscriptionParticipant",
    "AvailabilityNotificationEvent", "NotificationOutbox", "NotificationDelivery",
    "ReportTextResult", "ReportAsset", "ReportAssetAnnotation", "ReportAccessLog",
    "UserNotification", "ReportAssetType", "PackageVersionAssetRequirement",
    "AgentThread", "AgentRun", "AgentToolEvent", "AgentPendingAction",
    "AgentActionExecution", "SupportHandoff",
    "OAuthClient", "OAuthAuthorizationCode", "OAuthAccessToken",
    "OAuthRefreshToken",
    "HealthRecord", "HealthIndicator",
]
