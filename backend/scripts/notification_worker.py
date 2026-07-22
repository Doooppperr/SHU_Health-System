"""Deliver notification_outbox rows with retry-safe idempotent state transitions."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
import smtplib
import sys
import time

from sqlalchemy import update

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import NotificationDelivery, NotificationOutbox  # noqa: E402


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

    if row.event_type == "password_verification_code":
        purpose = "找回密码" if payload.get("purpose") == "reset" else "修改密码"
        subject = f"HealthDoc {purpose}验证码"
        body = (
            f"{payload.get('username') or '用户'}，您好，您正在进行{purpose}操作。"
            f"本次验证码为{payload.get('verification_code') or '验证码生成失败'}，"
            f"验证码在{int(payload.get('expires_minutes') or 10)}分钟内有效，请勿转发给他人。"
            "如果不是您本人操作，请忽略本邮件并尽快检查账号安全。"
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
        body = (
            f"{recipient_name}，您好，您的体检预约已经成功。预约服务为{payload.get('package') or '体检服务'}，"
            f"体检日期为{appointment_date}，地点为{institution_label}，地址是{address}，{identity_text}。"
            f"检查前请注意：{notice}。请登录康康健健 HealthDoc 平台查看或管理本次预约。"
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


def run_batch(app, limit=50):
    now = datetime.now(timezone.utc)
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
        # Claim with a conditional UPDATE so accidentally starting two workers
        # cannot send the same Outbox row twice.
        claim = db.session.execute(
            update(NotificationOutbox)
            .where(
                NotificationOutbox.id == row_id,
                NotificationOutbox.status.in_(("pending", "failed")),
                NotificationOutbox.next_attempt_at <= now,
            )
            .values(
                status="sending",
                attempts=NotificationOutbox.attempts + 1,
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
            if row.event_type == "password_verification_code":
                row.payload = {"challenge_id": (row.payload or {}).get("challenge_id"), "sensitive_content_cleared": True}
            db.session.add(NotificationDelivery(outbox_id=row.id, success=True, provider_message_id=provider_id))
            delivered += 1
        except Exception as exc:
            row.status = "failed"
            row.next_attempt_at = datetime.now(timezone.utc) + timedelta(minutes=min(2 ** row.attempts, 60))
            db.session.add(NotificationDelivery(outbox_id=row.id, success=False, error_message=str(exc)[:500]))
        db.session.commit()
    return attempted, delivered


def run_watch(app, limit=50, interval_seconds=5, *, max_cycles=None, sleep=time.sleep):
    """Continuously drain the Outbox; ``max_cycles`` exists for deterministic tests."""
    cycles = 0
    totals = [0, 0]
    while max_cycles is None or cycles < max_cycles:
        with app.app_context():
            attempted, delivered = run_batch(app, limit)
        totals[0] += attempted
        totals[1] += delivered
        if attempted:
            print(
                f"notification_batch attempted={attempted} delivered={delivered}",
                flush=True,
            )
        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            break
        sleep(interval_seconds)
    return tuple(totals)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=5)
    parser.add_argument("--config", choices=("development", "production"), default="development")
    args = parser.parse_args()
    app = create_app(args.config)
    limit = max(1, min(args.limit, 500))
    if args.watch:
        interval = max(1.0, min(args.interval_seconds, 300.0))
        print(f"notification_worker=watching interval_seconds={interval:g}", flush=True)
        try:
            run_watch(app, limit, interval)
        except KeyboardInterrupt:
            print("notification_worker=stopped", flush=True)
        return
    with app.app_context():
        attempted, delivered = run_batch(app, limit)
    print(f"attempted={attempted} delivered={delivered}")


if __name__ == "__main__": main()
