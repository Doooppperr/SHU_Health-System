"""Deliver notification Outbox rows with bounded, at-least-once retries.

SMTP cannot atomically commit its provider delivery together with our database.
The worker therefore finishes an in-flight row on SIGTERM and leases claims so
an abruptly killed process cannot leave a row in ``sending`` forever. A crash
after the provider accepted a message but before the database commit can still
produce a retry; that explicit at-least-once trade-off is preferable to silent
permanent mail loss.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
import signal
import smtplib
import sys
import threading
import time

from sqlalchemy import text, update

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import NotificationDelivery, NotificationOutbox  # noqa: E402
from app.services.account_credentials import decrypt_account_credentials  # noqa: E402
from app.services.platform_contact import PLATFORM_CONTACT  # noqa: E402


DEFAULT_CLAIM_LEASE_SECONDS = 300


def _stop_is_requested(stop_requested) -> bool:
    return bool(stop_requested is not None and stop_requested.is_set())


def _display_date(value):
    try:
        parsed = date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return str(value or "待确认")
    return f"{parsed.year}年{parsed.month}月{parsed.day}日"


def _email_content(row):
    """Turn an Outbox payload into readable prose instead of exposing raw JSON."""
    payload = row.payload if isinstance(row.payload, dict) else {}
    institution = str(payload.get("institution") or "体检机构")
    branch = str(payload.get("branch") or "").strip()
    institution_label = f"{institution}·{branch}" if branch and branch not in institution else institution
    appointment_date = _display_date(payload.get("appointment_date"))
    party_size = max(1, int(payload.get("party_size") or 1))

    if row.event_type in {"institution_account_created", "institution_account_reset"}:
        credentials = decrypt_account_credentials(
            payload.get("encrypted_credentials"),
            purpose=payload.get("credential_purpose") or "",
        )
        subject = (
            "HealthDoc 机构账号已创建"
            if row.event_type == "institution_account_created"
            else "HealthDoc 机构账号凭据已重置"
        )
        body = (
            f"{payload.get('account_label') or '体检分院'}，您好，"
            f"您的HealthDoc机构账号用户名为{credentials.get('username') or '未设置'}，"
            f"临时密码为{credentials.get('temporary_password') or '未设置'}。"
            f"登录地址为{payload.get('login_url') or '/login'}。"
            "请使用该账号登录机构工作台，并在首次登录后尽快修改密码。"
            "本邮件包含敏感账号信息，请妥善保管且不要转发。"
        )
    elif row.event_type == "password_verification_code":
        purpose = "找回密码" if payload.get("purpose") == "reset" else "修改密码"
        subject = f"HealthDoc {purpose}验证码"
        body = (
            f"{payload.get('username') or '用户'}，您好，您正在进行{purpose}操作。"
            f"本次验证码为{payload.get('verification_code') or '验证码生成失败'}，"
            f"验证码在{int(payload.get('expires_minutes') or 10)}分钟内有效，请勿转发给他人。"
            "如果不是您本人操作，请忽略本邮件并尽快检查账号安全。"
        )
    elif row.event_type == "account_email_changed_old":
        subject = "HealthDoc 绑定邮箱变更提醒"
        body = (
            f"{payload.get('account_label') or payload.get('username') or '用户'}，您好，"
            f"您的HealthDoc绑定邮箱已经更换为{payload.get('new_email') or '新的邮箱地址'}。"
            "如果这不是您本人或本分院管理员的操作，请立即联系平台管理员处理。"
        )
    elif row.event_type == "account_email_changed_new":
        subject = "HealthDoc 邮箱换绑成功"
        body = (
            f"{payload.get('account_label') or payload.get('username') or '用户'}，您好，"
            "该邮箱现已成为您的HealthDoc绑定邮箱。后续预约、空位提醒、"
            "账户安全及其他平台邮件都会发送到这里。"
        )
    elif row.event_type == "admin_password_changed":
        subject = "HealthDoc 账号密码已由管理员修改"
        body = (
            f"{payload.get('account_label') or payload.get('username') or '用户'}，您好，"
            "系统管理员已经修改您的 HealthDoc 账号密码。"
            f"您的新密码为：{payload.get('new_password') or '密码内容不可用'}。"
            "旧登录状态已经失效，请使用新密码重新登录。"
            "邮件包含账号敏感信息，请妥善保管并避免转发。"
        )
    elif row.event_type == "appointment_institution_cancelled":
        subject = "HealthDoc 体检预约取消致歉及解决方案"
        alternatives = payload.get("alternatives") if isinstance(payload.get("alternatives"), list) else []
        branch_text = "；".join(
            f"{item.get('name') or '体检机构'}·{item.get('branch_name') or '分院'}，"
            f"地址{item.get('address') or '请在平台查看'}，电话{item.get('consult_phone') or '请在平台查看'}"
            for item in alternatives if isinstance(item, dict)
        )
        solution = branch_text or (
            f"请联系原机构{payload.get('institution_phone') or ''}"
            f"或平台客服{payload.get('support_phone') or PLATFORM_CONTACT['phone']}重新安排"
        )
        body = (
            f"{payload.get('recipient_name') or '用户'}，您好，非常抱歉，"
            f"{institution_label}因“{payload.get('reason') or '机构突发情况'}”"
            f"无法在{appointment_date}提供{payload.get('package') or '体检服务'}。"
            f"可选解决方案：{solution}。请登录平台查看并重新预约。"
        )
    elif row.event_type == "booking_group_created":
        subject = "HealthDoc 新预约提醒"
        body = (
            f"您好，{institution_label}刚刚收到一笔新的体检预约。"
            f"预约编号为{payload.get('group_code') or '待确认'}，预约服务为"
            f"{payload.get('package') or '体检服务'}，体检日期为{appointment_date}，"
            f"共{party_size}位受检者。请登录康康健健 HealthDoc 机构工作台查看预约详情，"
            "并按计划完成接待准备。"
        )
    elif row.event_type == "booking_user_confirmed":
        subject = "HealthDoc 体检预约成功"
        address = str(payload.get("address") or "请在平台查看详细地址").strip()
        notice = str(payload.get("booking_notice") or "请按机构要求提前做好体检准备").strip().rstrip("。；;，,")
        recipient_name = str(payload.get("recipient_name") or "用户").strip()
        participant = payload.get("participant") if isinstance(payload.get("participant"), dict) else None
        participants = payload.get("participants") if isinstance(payload.get("participants"), list) else []
        if payload.get("is_organizer") and participants:
            people = "、".join(
                f"{str(item.get('name') or '受检者')}（健康身份码{str(item.get('health_id_masked') or '未设置')}）"
                for item in participants if isinstance(item, dict)
            )
            identity_text = f"本次受检者为{people}"
        elif participant:
            identity_text = (
                f"本次受检者为{participant.get('name') or recipient_name}"
                f"（健康身份码{participant.get('health_id_masked') or '未设置'}）"
            )
        else:
            identity_text = "本次预约的受检者信息可在平台中查看"
        preparation_items = payload.get("preparation_items") if isinstance(payload.get("preparation_items"), list) else []
        preparation = "、".join(str(item) for item in preparation_items if str(item).strip())
        phone = str(payload.get("consult_phone") or "请在平台查看").strip()
        body = (
            f"{recipient_name}，您好，您的体检预约已经成功。预约服务为{payload.get('package') or '体检服务'}，"
            f"体检日期为{appointment_date}，地点为{institution_label}，地址是{address}，{identity_text}。"
            f"请携带：{preparation or '身份证原件、预约凭证、病历本、既往报告和用药清单'}。"
            f"机构联系电话：{phone}。检查前请注意：{notice}。"
            "请登录康康健健 HealthDoc 平台查看或管理本次预约。"
        )
    elif row.event_type == "appointment_date_full":
        subject = "HealthDoc 预约容量提醒"
        body = (
            f"您好，{institution_label}在{appointment_date}的体检预约名额现已约满。"
            "请登录康康健健 HealthDoc 机构工作台查看当天的容量与预约安排；"
            "如后续有用户取消，系统会按规则更新空位提醒。"
        )
    elif row.event_type == "waitlist_available":
        subject = "HealthDoc 空位提醒"
        body = (
            f"您好，您关注的{institution_label}在{appointment_date}出现了可预约名额，"
            f"可供您登记的{party_size}位受检者重新尝试预约。名额先到先得，"
            "本邮件仅用于提醒，不代表预约已经成功，也不会为您保留名额。"
            "请尽快登录康康健健 HealthDoc 平台查看最新容量并确认预约。"
        )
    elif row.event_type == "report_published":
        subject = "HealthDoc 体检报告已交付"
        body = (
            f"{payload.get('recipient_name') or '用户'}，您好，您于"
            f"{payload.get('exam_date') or '近期'}在{institution_label}完成的体检报告已经正式交付。"
            "请登录康康健健 HealthDoc 平台查看结构化指标、检查附件及机构批注。"
        )
    else:
        subject = "HealthDoc 服务通知"
        detail = str(payload.get("message") or "您有一条新的平台通知。")
        body = f"您好，{detail}请登录康康健健 HealthDoc 平台查看详情。"

    footer = "本邮件由康康健健 HealthDoc 自动发送，请勿直接回复。"
    return subject, f"{body}{footer}"


def _send(app, row):
    subject, body = _email_content(row)
    recipient = app.config.get("NOTIFICATION_EMAIL_REDIRECT") or row.recipient
    message = EmailMessage(); message["Subject"] = subject
    message["From"] = app.config["SMTP_FROM"]; message["To"] = recipient
    message.set_content(body)
    if app.config["NOTIFICATION_EMAIL_DRY_RUN"]:
        return f"dry-run-{row.id}"
    if not app.config["SMTP_HOST"]:
        raise RuntimeError("SMTP_HOST is not configured")
    with smtplib.SMTP(app.config["SMTP_HOST"], app.config["SMTP_PORT"], timeout=20) as client:
        if app.config["SMTP_USE_TLS"]: client.starttls()
        if app.config["SMTP_USERNAME"]: client.login(app.config["SMTP_USERNAME"], app.config["SMTP_PASSWORD"])
        response = client.send_message(message)
    return str(response or f"smtp-{row.id}")


def _recover_stale_claims(now):
    """Return expired ``sending`` leases to the retry queue atomically."""
    recovered = db.session.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.status == "sending",
            NotificationOutbox.next_attempt_at <= now,
        )
        .values(status="failed", next_attempt_at=now)
        .execution_options(synchronize_session=False)
    )
    db.session.commit()
    return int(recovered.rowcount or 0)


def run_batch(
    app,
    limit=50,
    *,
    stop_requested=None,
    lease_seconds=DEFAULT_CLAIM_LEASE_SECONDS,
):
    now = datetime.now(timezone.utc)
    from app.services.finance import run_due_finance_tasks
    run_due_finance_tasks(now=now)
    db.session.commit()
    _recover_stale_claims(now)
    row_ids = [
        row_id
        for (row_id,) in db.session.query(NotificationOutbox.id)
        .filter(
            NotificationOutbox.status.in_(("pending", "failed")),
            NotificationOutbox.next_attempt_at <= now,
        )
        .order_by(NotificationOutbox.id)
        .limit(limit)
        .all()
    ]
    delivered = 0
    attempted = 0
    for row_id in row_ids:
        if _stop_is_requested(stop_requested):
            break
        claim_now = datetime.now(timezone.utc)
        # Claim with a conditional UPDATE so accidentally starting two workers
        # cannot send the same Outbox row concurrently. ``next_attempt_at`` is
        # the claim lease deadline while the row is in ``sending``.
        claim = db.session.execute(
            update(NotificationOutbox)
            .where(
                NotificationOutbox.id == row_id,
                NotificationOutbox.status.in_(("pending", "failed")),
                NotificationOutbox.next_attempt_at <= claim_now,
            )
            .values(
                status="sending",
                attempts=NotificationOutbox.attempts + 1,
                next_attempt_at=claim_now + timedelta(
                    seconds=max(30, int(lease_seconds))
                ),
            )
            .execution_options(synchronize_session=False)
        )
        db.session.commit()
        if claim.rowcount != 1:
            continue
        attempted += 1
        row = db.session.get(NotificationOutbox, row_id)
        try:
            provider_id = _send(app, row)
            row.status = "sent"; row.sent_at = datetime.now(timezone.utc)
            if row.event_type in {
                "password_verification_code",
                "admin_password_changed",
                "institution_account_created",
                "institution_account_reset",
            }:
                row.payload = {"challenge_id": (row.payload or {}).get("challenge_id"), "sensitive_content_cleared": True}
                row.sensitive_payload_cleared_at = datetime.now(timezone.utc)
            db.session.add(NotificationDelivery(outbox_id=row.id, success=True, provider_message_id=provider_id))
            delivered += 1
        except Exception as exc:
            row.status = "failed"
            row.next_attempt_at = datetime.now(timezone.utc) + timedelta(minutes=min(2 ** row.attempts, 60))
            db.session.add(NotificationDelivery(outbox_id=row.id, success=False, error_message=str(exc)[:500]))
        db.session.commit()
    return attempted, delivered


def run_watch(
    app,
    limit=50,
    interval_seconds=5,
    *,
    max_cycles=None,
    sleep=time.sleep,
    stop_requested=None,
):
    """Continuously drain the Outbox; ``max_cycles`` exists for deterministic tests."""
    cycles = 0
    totals = [0, 0]
    while max_cycles is None or cycles < max_cycles:
        if _stop_is_requested(stop_requested):
            break
        with app.app_context():
            attempted, delivered = run_batch(
                app,
                limit,
                stop_requested=stop_requested,
            )
        totals[0] += attempted
        totals[1] += delivered
        if attempted:
            print(
                f"notification_batch attempted={attempted} delivered={delivered}",
                flush=True,
            )
        cycles += 1
        if (
            _stop_is_requested(stop_requested)
            or (max_cycles is not None and cycles >= max_cycles)
        ):
            break
        if stop_requested is not None and sleep is time.sleep:
            stop_requested.wait(interval_seconds)
        else:
            sleep(interval_seconds)
    return tuple(totals)


def wait_for_start_gate(path, *, sleep=time.sleep, stop_requested=None):
    """Keep a newly installed worker side-effect free until cutover commits."""
    gate = Path(path)
    announced = False
    while not gate.is_file():
        if _stop_is_requested(stop_requested):
            return False
        if not announced:
            print(f"notification_worker=waiting gate={gate}", flush=True)
            announced = True
        if stop_requested is not None and sleep is time.sleep:
            stop_requested.wait(1)
        else:
            sleep(1)
    return True


def check_config(app):
    """Validate runtime configuration and SQL without claiming Outbox rows."""
    with app.app_context():
        db.session.execute(text("SELECT 1"))
        db.session.rollback()
    required = ["SMTP_FROM"]
    if not app.config.get("NOTIFICATION_EMAIL_DRY_RUN"):
        required.append("SMTP_HOST")
    missing = [key for key in required if not app.config.get(key)]
    if missing:
        raise RuntimeError(
            f"notification configuration is missing: {', '.join(missing)}"
        )
    print("notification_worker=config-ok sql=ok", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--start-gate-file")
    parser.add_argument("--interval-seconds", type=float, default=5)
    parser.add_argument("--config", choices=("development", "production"), default="development")
    args = parser.parse_args()
    app = create_app(args.config)
    if args.check_config:
        check_config(app)
        return
    limit = max(1, min(args.limit, 500))
    if args.watch:
        stop_requested = threading.Event()

        def request_stop(signum, _frame):
            if not stop_requested.is_set():
                print(
                    f"notification_worker=stopping signal={signum} "
                    "after_current_delivery=true",
                    flush=True,
                )
            stop_requested.set()

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        if args.start_gate_file:
            if not wait_for_start_gate(
                args.start_gate_file,
                stop_requested=stop_requested,
            ):
                print("notification_worker=stopped", flush=True)
                return
        interval = max(1.0, min(args.interval_seconds, 300.0))
        print(f"notification_worker=watching interval_seconds={interval:g}", flush=True)
        try:
            run_watch(
                app,
                limit,
                interval,
                stop_requested=stop_requested,
            )
        except KeyboardInterrupt:
            stop_requested.set()
        print("notification_worker=stopped", flush=True)
        return
    with app.app_context():
        attempted, delivered = run_batch(app, limit)
    print(f"attempted={attempted} delivered={delivered}")


if __name__ == "__main__": main()
