from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListReportsArgs(StrictToolArgs):
    owner_id: int | None = None
    limit: int = Field(default=10, ge=1, le=30)


class ReportFactsArgs(StrictToolArgs):
    report_ids: list[int] = Field(min_length=1, max_length=10)
    indicator_codes: list[str] = Field(default_factory=list, max_length=20)


class IndicatorTrendArgs(StrictToolArgs):
    owner_id: int | None = None
    indicator_code: str = Field(min_length=1, max_length=40)
    limit: int = Field(default=10, ge=2, le=30)


class SearchInstitutionsArgs(StrictToolArgs):
    keyword: str = Field(default="", max_length=80)
    district: str | None = Field(default=None, max_length=80)
    limit: int = Field(default=8, ge=1, le=20)


class ComparePackagesArgs(StrictToolArgs):
    package_ids: list[int] = Field(
        default_factory=list,
        max_length=8,
        description="已知套餐 ID 时传入；按机构浏览套餐时可留空。",
    )
    institution_id: int | None = Field(
        default=None,
        description="按机构列出或选择套餐时传入机构 ID。",
    )
    sort_by: Literal["default", "price_asc", "price_desc"] = "default"
    limit: int = Field(default=8, ge=1, le=20)


class AvailabilityArgs(StrictToolArgs):
    institution_id: int
    appointment_date: date
    party_size: int = Field(default=1, ge=1, le=5)


class AppointmentStatusArgs(StrictToolArgs):
    group_id: int | None = None
    limit: int = Field(default=10, ge=1, le=30)


class BookingIntake(StrictToolArgs):
    user_id: int | None = Field(
        default=None,
        description="本人预约时留空；服务端会绑定当前登录用户。",
    )
    height_cm: float = Field(ge=80, le=250)
    weight_kg: float = Field(ge=20, le=300)


class BookingParticipantSpec(StrictToolArgs):
    type: Literal[
        "self",
        "linked_account",
        "health_code_token",
    ]
    relation_id: int | None = None
    participant_token: str | None = Field(default=None, max_length=200)
    height_cm: float | None = Field(default=None, ge=80, le=250)
    weight_kg: float | None = Field(default=None, ge=20, le=300)


class BookingDraftArgs(StrictToolArgs):
    institution_id: int
    package_id: int
    appointment_date: date
    participant_user_ids: list[int] = Field(
        default_factory=list,
        max_length=5,
        description="本人预约时传空数组；服务端会绑定当前登录用户。",
    )
    participant_intakes: list[BookingIntake] = Field(
        default_factory=list,
        max_length=5,
        description="旧版本人预约资料；新版 participants 可由服务端安全读取最新值。",
    )
    participants: list[BookingParticipantSpec] = Field(
        default_factory=list,
        max_length=5,
        description="受检者来源：self、linked_account 或 health_code_token。",
    )
    notice_confirmed: bool = False

    @model_validator(mode="after")
    def require_participant_input(self):
        if not self.participants and not self.participant_intakes:
            raise ValueError("必须提供受检者或本人身高体重资料")
        return self


class CancellationDraftArgs(StrictToolArgs):
    group_id: int


class WaitlistDraftArgs(StrictToolArgs):
    institution_id: int
    package_id: int
    appointment_date: date
    participant_user_ids: list[int] = Field(default_factory=list, max_length=5)
    participants: list[BookingParticipantSpec] = Field(
        default_factory=list,
        max_length=5,
    )


class SupportHandoffDraftArgs(StrictToolArgs):
    category: Literal["account", "record", "booking", "institution", "other"]
    summary: str = Field(min_length=5, max_length=500)
    priority: Literal["normal", "high", "urgent"] = "normal"
