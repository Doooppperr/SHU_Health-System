from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import update

from app.extensions import db
from app.models import (
    BookingParticipantToken,
    FriendRelation,
    IndicatorDict,
    InstitutionReport,
    ReportIndicator,
    SelfMeasurement,
    User,
    WaitlistSubscription,
    WaitlistSubscriptionParticipant,
)
from app.services.delegation import active_relation_for_users


TOKEN_TTL = timedelta(minutes=10)
PARTICIPANT_TOKEN_PREFIX = "bpt_"
PARTICIPANT_TOKEN_ENTROPY_BYTES = 32
PARTICIPANT_TOKEN_BODY_LENGTH = 43


def _aware(value):
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _token_hash(raw_token):
    return hashlib.sha256(str(raw_token).encode("utf-8")).hexdigest()


def masked_name(user):
    name = (user.real_name or "").strip()
    if not name:
        return "未完善姓名"
    if len(name) == 1:
        return f"{name}*"
    return f"{name[0]}{'*' * (len(name) - 1)}"


def masked_health_id(value):
    text = str(value or "").strip()
    if len(text) <= 4:
        return "****" if text else "未设置"
    return f"{text[:2]}{'*' * max(4, len(text) - 4)}{text[-2:]}"


def issue_participant_token(booker, health_id):
    normalized = str(health_id or "").strip().upper()
    target = User.query.filter_by(
        health_id=normalized,
        role="user",
        is_active=True,
    ).first()
    if (
        target is None
        or not target.profile_completed
    ):
        return None, (
            {
                "message": "无法使用该健康身份码添加受检者，请核对后重试",
                "code": "HEALTH_ID_PARTICIPANT_UNAVAILABLE",
            },
            404,
        )

    availability = recent_intake_availability(target.id)
    identity_summary = {
        "real_name": target.real_name,
        "gender": target.gender,
        "birth_year": target.birth_date.year if target.birth_date else None,
        "masked_health_id": masked_health_id(target.health_id),
        "has_recent_height": availability["has_recent_height"],
        "has_recent_weight": availability["has_recent_weight"],
        "uses_recent_measurements": (
            availability["has_recent_height"]
            and availability["has_recent_weight"]
        ),
    }
    # Entering an identity code that already belongs to the effective account
    # or to an active linked account must not create a second participant
    # source. Return the canonical source so every client can select the
    # existing option and avoid issuing a redundant bearer credential.
    if target.id == booker.id:
        return {
            **identity_summary,
            "participant_type": "self",
        }, None
    if not target.allow_health_id_proxy_booking:
        return None, (
            {
                "message": "无法使用该健康身份码添加受检者，请核对后重试",
                "code": "HEALTH_ID_PARTICIPANT_UNAVAILABLE",
            },
            404,
        )
    relation = active_relation_for_users(booker.id, target.id)
    if relation is not None:
        return {
            **identity_summary,
            "participant_type": "linked_account",
            "relation_id": relation.id,
        }, None

    raw_token = (
        f"{PARTICIPANT_TOKEN_PREFIX}"
        f"{secrets.token_urlsafe(PARTICIPANT_TOKEN_ENTROPY_BYTES)}"
    )
    now = datetime.now(timezone.utc)
    row = BookingParticipantToken(
        token_hash=_token_hash(raw_token),
        booker_user_id=booker.id,
        subject_user_id=target.id,
        authorization_version=target.booking_authorization_version,
        expires_at=now + TOKEN_TTL,
        created_at=now,
    )
    db.session.add(row)
    db.session.flush()
    return {
        **identity_summary,
        "participant_token": raw_token,
        "participant_type": "health_code_token",
        "expires_in": int(TOKEN_TTL.total_seconds()),
        "expires_at": row.expires_at.isoformat(),
    }, None


def revoke_health_id_booking_access(subject):
    """Immediately invalidate outstanding code-based delegation artifacts."""
    from app.services.notifications import enqueue_user_notification

    now = datetime.now(timezone.utc)
    token_count = BookingParticipantToken.query.filter(
        BookingParticipantToken.subject_user_id == subject.id,
        BookingParticipantToken.consumed_at.is_(None),
        BookingParticipantToken.revoked_at.is_(None),
    ).update(
        {"revoked_at": now},
        synchronize_session=False,
    )
    waitlists = (
        WaitlistSubscription.query.join(
            WaitlistSubscriptionParticipant,
            WaitlistSubscriptionParticipant.subscription_id
            == WaitlistSubscription.id,
        )
        .filter(
            WaitlistSubscription.status == "active",
            WaitlistSubscriptionParticipant.subject_user_id == subject.id,
            WaitlistSubscriptionParticipant.participant_type.in_(
                ("health_code_token", "health_code")
            ),
        )
        .all()
    )
    seen = set()
    for subscription in waitlists:
        if subscription.id in seen:
            continue
        seen.add(subscription.id)
        subscription.status = "invalid"
        subscription.closed_at = now
        subscriber = db.session.get(User, subscription.subscriber_user_id)
        recipients = {
            user.id: user
            for user in (subscriber, subject)
            if user is not None and user.is_active
        }
        for user in recipients.values():
            is_subject = user.id == subject.id
            body = (
                "您已关闭健康身份码代预约，相关未完成空位提醒已即时失效；"
                "已创建的正式预约不受影响。"
                if is_subject
                else
                "受检者已关闭健康身份码代预约，本条空位提醒已失效；"
                "已创建的正式预约不受影响。"
            )
            key = (
                f"health-code-disabled:subject:{subject.id}:"
                f"waitlist:{subscription.id}:user:{user.id}:"
                f"version:{subject.booking_authorization_version}"
            )
            enqueue_user_notification(
                user,
                event_type="health_code_booking_disabled",
                idempotency_key=key,
                title="健康身份码代预约授权已关闭",
                body=body,
                action_url="/appointments" if not is_subject else "/profile",
                payload={
                    "subscription_id": subscription.id,
                    "subject_is_current_user": is_subject,
                },
                email_payload={
                    "message": body,
                    "subscription_id": subscription.id,
                    "login_url": "/appointments" if not is_subject else "/profile",
                },
            )
    return {
        "revoked_token_count": token_count,
        "invalidated_waitlist_count": len(seen),
    }


def _participant_error(message, code, status=400):
    return None, ({"message": message, "code": code}, status)


def _friend_participant(booker, relation, manual_intake=None):
    if relation is None or not relation.is_active:
        return _participant_error(
            "亲友关联已失效，请重新发起并接受关联申请",
            "RELATIONSHIP_INACTIVE",
            409,
        )
    target = relation.counterparty_for(booker.id) if relation else None
    if (
        target is None
        or not target.is_active
        or target.role != "user"
        or not target.profile_completed
    ):
        return _participant_error(
            "亲友账号当前不可作为受检者",
            "BOOKING_PARTICIPANT_UNAVAILABLE",
            409,
        )
    if not relation.booking_granted(booker.id, target.id):
        return _participant_error(
            "亲友关联已失效，请重新发起并接受关联申请",
            "RELATIONSHIP_INACTIVE",
            409,
        )
    return {
        "user": target,
        "participant_type": "linked_account",
        "friend_relation_id": relation.id,
        "authorization_version": relation.booking_authorization_version,
        "authorized_at": (
            relation.booking_granted_at(booker.id, target.id)
            or relation.created_at
        ),
        "participant_token_id": None,
        "manual_intake": manual_intake or {},
    }, None


def _health_code_participant(booker, raw_token, manual_intake=None):
    now = datetime.now(timezone.utc)
    row = BookingParticipantToken.query.filter_by(
        token_hash=_token_hash(raw_token),
        booker_user_id=booker.id,
    ).first()
    target = db.session.get(User, row.subject_user_id) if row else None
    if (
        row is None
        or row.consumed_at is not None
        or row.revoked_at is not None
        or _aware(row.expires_at) <= now
        or target is None
        or not target.is_active
        or target.role != "user"
        or not target.profile_completed
        or not target.allow_health_id_proxy_booking
        or row.authorization_version != target.booking_authorization_version
    ):
        return _participant_error(
            "受检者凭证无效或已过期，请重新解析健康身份码",
            "PARTICIPANT_TOKEN_EXPIRED",
            409,
        )
    return {
        "user": target,
        "participant_type": "health_code_token",
        "friend_relation_id": None,
        "authorization_version": row.authorization_version,
        "authorized_at": row.created_at,
        "participant_token_id": row.id,
        "booker_user_id": booker.id,
        "manual_intake": manual_intake or {},
    }, None


def resolve_booking_participants(booker, payload):
    specs = payload.get("participants")
    resolved = []
    if specs is not None:
        if not isinstance(specs, list) or not 1 <= len(specs) <= 5:
            return _participant_error(
                "预约受检者应为1至5人",
                "BOOKING_PARTICIPANTS_INVALID",
            )
        for spec in specs:
            if not isinstance(spec, dict):
                return _participant_error(
                    "受检者信息格式不正确",
                    "BOOKING_PARTICIPANTS_INVALID",
                )
            kind = str(spec.get("type") or spec.get("participant_type") or "").strip()
            manual_intake = {
                "height_cm": spec.get("height_cm"),
                "weight_kg": spec.get("weight_kg"),
            }
            if kind == "self":
                resolved.append(
                    {
                        "user": booker,
                        "participant_type": "self",
                        "friend_relation_id": None,
                        "authorization_version": booker.booking_authorization_version,
                        "authorized_at": datetime.now(timezone.utc),
                        "participant_token_id": None,
                        "manual_intake": manual_intake,
                    }
                )
            elif kind in {"linked_account", "friend"}:
                try:
                    relation_id = int(spec.get("relation_id"))
                except (TypeError, ValueError):
                    return _participant_error(
                        "请选择有效的亲友关系",
                        "FRIEND_RELATION_REQUIRED",
                    )
                relation = db.session.get(FriendRelation, relation_id)
                if relation is None or booker.id not in {
                    relation.user_id,
                    relation.friend_user_id,
                }:
                    return _participant_error(
                        "没有找到该亲友关系",
                        "FRIEND_RELATION_NOT_FOUND",
                        404,
                    )
                item, error = _friend_participant(
                    booker,
                    relation,
                    manual_intake,
                )
                if error:
                    return item, error
                resolved.append(item)
            elif kind in {"health_code_token", "health_code"}:
                token = spec.get("participant_token") or spec.get("token")
                if not token:
                    return _participant_error(
                        "请先解析受检者健康身份码",
                        "BOOKING_PARTICIPANT_TOKEN_REQUIRED",
                    )
                item, error = _health_code_participant(
                    booker,
                    token,
                    manual_intake,
                )
                if error:
                    return item, error
                resolved.append(item)
            else:
                return _participant_error(
                    "受检者类型不正确",
                    "BOOKING_PARTICIPANT_TYPE_INVALID",
                )
    else:
        # Backward-compatible input. Authorization is still evaluated through
        # the new bidirectional relationship semantics.
        raw_ids = payload.get("participant_user_ids")
        if raw_ids is None:
            raw_ids = [booker.id]
        if not isinstance(raw_ids, list) or not 1 <= len(raw_ids) <= 5:
            return _participant_error(
                "预约受检者应为1至5人",
                "BOOKING_PARTICIPANTS_INVALID",
            )
        try:
            ids = [int(value) for value in raw_ids]
        except (TypeError, ValueError):
            return _participant_error(
                "受检者编号格式不正确",
                "BOOKING_PARTICIPANTS_INVALID",
            )
        users = {
            row.id: row
            for row in User.query.filter(
                User.id.in_(ids),
                User.role == "user",
                User.is_active.is_(True),
            ).all()
        }
        if set(users) != set(ids):
            return _participant_error(
                "受检者账号当前不可用",
                "BOOKING_PARTICIPANT_UNAVAILABLE",
            )
        legacy_intakes = {}
        for intake in payload.get("participant_intakes") or []:
            if not isinstance(intake, dict) or intake.get("user_id") is None:
                continue
            try:
                legacy_intakes[int(intake["user_id"])] = intake
            except (TypeError, ValueError):
                return _participant_error(
                    "受检者基本资料格式不正确",
                    "BOOKING_PARTICIPANTS_INVALID",
                )
        for user_id in ids:
            if user_id == booker.id:
                resolved.append(
                    {
                        "user": booker,
                        "participant_type": "self",
                        "friend_relation_id": None,
                        "authorization_version": booker.booking_authorization_version,
                        "authorized_at": datetime.now(timezone.utc),
                        "participant_token_id": None,
                        "manual_intake": legacy_intakes.get(user_id) or {},
                    }
                )
                continue
            relation = active_relation_for_users(booker.id, user_id)
            item, error = _friend_participant(
                booker,
                relation,
                legacy_intakes.get(user_id) or {},
            )
            if error:
                return item, error
            resolved.append(item)

    ids = [item["user"].id for item in resolved]
    if len(set(ids)) != len(ids):
        return _participant_error(
            "同一受检者不能重复添加",
            "PARTICIPANT_DUPLICATED",
        )
    if not 1 <= len(ids) <= 5:
        return _participant_error(
            "预约受检者应为1至5人",
            "BOOKING_PARTICIPANTS_INVALID",
        )
    if not booker.profile_completed or any(
        not item["user"].profile_completed for item in resolved
    ):
        return _participant_error(
            "所有受检者都必须先完成实名认证",
            "IDENTITY_REQUIRED",
            409,
        )
    return resolved, None


def _latest_indicator_value(user_id, code):
    definition = IndicatorDict.query.filter_by(code=code).first()
    if definition is None:
        return None
    self_row = (
        SelfMeasurement.query.filter_by(
            user_id=user_id,
            indicator_dict_id=definition.id,
        )
        .order_by(SelfMeasurement.measured_at.desc(), SelfMeasurement.id.desc())
        .first()
    )
    report_row = (
        ReportIndicator.query.join(
            InstitutionReport,
            InstitutionReport.id == ReportIndicator.report_id,
        )
        .filter(
            InstitutionReport.matched_user_id == user_id,
            InstitutionReport.status == "published",
            ReportIndicator.indicator_dict_id == definition.id,
        )
        .order_by(InstitutionReport.exam_date.desc(), ReportIndicator.id.desc())
        .first()
    )
    selected = None
    if self_row is not None:
        selected = (self_row.measured_at.date(), self_row.value)
    if report_row is not None and report_row.report is not None:
        candidate = (report_row.report.exam_date, report_row.value)
        if selected is None or candidate[0] > selected[0]:
            selected = candidate
    if selected is None:
        return None
    try:
        return Decimal(str(selected[1]))
    except (InvalidOperation, TypeError, ValueError):
        return None


def recent_intake_availability(user_id):
    return {
        "has_recent_height": _latest_indicator_value(user_id, "HEIGHT") is not None,
        "has_recent_weight": _latest_indicator_value(user_id, "WEIGHT") is not None,
    }


def participant_intakes(resolved):
    parsed = {}
    for item in resolved:
        user = item["user"]
        manual_intake = item.get("manual_intake") or {}
        try:
            latest_height = _latest_indicator_value(user.id, "HEIGHT")
            latest_weight = _latest_indicator_value(user.id, "WEIGHT")
            manual_height = (
                Decimal(str(manual_intake.get("height_cm")))
                if manual_intake.get("height_cm") is not None
                else None
            )
            manual_weight = (
                Decimal(str(manual_intake.get("weight_kg")))
                if manual_intake.get("weight_kg") is not None
                else None
            )
            # A booking intake is an explicit point-in-time snapshot. A value
            # supplied for this booking takes precedence for every participant
            # type but is never written back as a daily measurement.
            height = manual_height if manual_height is not None else latest_height
            weight = manual_weight if manual_weight is not None else latest_weight
        except (InvalidOperation, TypeError, ValueError):
            height = weight = None
        if height is None or weight is None:
            missing_fields = []
            if height is None:
                missing_fields.append("height_cm")
            if weight is None:
                missing_fields.append("weight_kg")
            return None, (
                {
                    "message": "受检者缺少可用的最新身高或体重，请补充本次预约所缺项目",
                    "code": "PARTICIPANT_INTAKE_REQUIRED",
                    "missing_fields": missing_fields,
                },
                409,
            )
        if not Decimal("80") <= height <= Decimal("250"):
            return None, ({"message": "身高应在 80 至 250 厘米之间"}, 400)
        if not Decimal("20") <= weight <= Decimal("300"):
            return None, ({"message": "体重应在 20 至 300 千克之间"}, 400)
        metres = height / Decimal("100")
        bmi = (weight / (metres * metres)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        parsed[user.id] = {"height": height, "weight": weight, "bmi": bmi}
    return parsed, None


def consume_participant_tokens(resolved):
    now = datetime.now(timezone.utc)
    for item in resolved:
        token_id = item.get("participant_token_id")
        if token_id is None:
            continue
        result = db.session.execute(
            update(BookingParticipantToken)
            .where(
                BookingParticipantToken.id == token_id,
                BookingParticipantToken.booker_user_id
                == item["booker_user_id"],
                BookingParticipantToken.subject_user_id == item["user"].id,
                BookingParticipantToken.authorization_version
                == item["authorization_version"],
                BookingParticipantToken.consumed_at.is_(None),
                BookingParticipantToken.revoked_at.is_(None),
                BookingParticipantToken.expires_at > now,
            )
            .values(consumed_at=now)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            return {
                "message": "受检者凭证已被使用或已经过期，请重新解析健康身份码",
                "code": "PARTICIPANT_TOKEN_EXPIRED",
            }, 409
    return None
