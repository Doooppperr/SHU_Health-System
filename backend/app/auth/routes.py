import base64
import secrets
import time
from uuid import uuid4

from flask import current_app, request
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    get_jwt_identity,
    jwt_required,
)

from app.auth import auth_bp
from app.extensions import db
from app.models import User


CAPTCHA_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_captcha_store = {}


def _purge_expired_captchas(now=None):
    now = now or time.time()
    expired_ids = [
        challenge_id
        for challenge_id, challenge in _captcha_store.items()
        if challenge["expires_at"] <= now
    ]
    for challenge_id in expired_ids:
        _captcha_store.pop(challenge_id, None)


def _generate_captcha_code(length=4):
    return "".join(secrets.choice(CAPTCHA_ALPHABET) for _ in range(length))


def _random_between(min_value, max_value):
    return min_value + secrets.randbelow(max_value - min_value + 1)


def _build_captcha_image(code):
    width = 128
    height = 44
    line_colors = ["#9db4c7", "#b6c4d2", "#a5b8a0", "#d0b47f", "#c2a2a2"]
    text_colors = ["#1f4b5f", "#34543f", "#6a4a1c", "#573c6b"]

    lines = []
    for _ in range(7):
        color = secrets.choice(line_colors)
        lines.append(
            f'<line x1="{_random_between(0, width)}" y1="{_random_between(0, height)}" '
            f'x2="{_random_between(0, width)}" y2="{_random_between(0, height)}" '
            f'stroke="{color}" stroke-width="{_random_between(1, 2)}" opacity="0.75" />'
        )

    chars = []
    for index, char in enumerate(code):
        x = 20 + index * 26 + _random_between(-3, 3)
        y = 29 + _random_between(-3, 4)
        angle = _random_between(-15, 15)
        color = secrets.choice(text_colors)
        chars.append(
            f'<text x="{x}" y="{y}" transform="rotate({angle} {x} {y})" '
            f'fill="{color}" font-size="24" font-family="Consolas, Arial, sans-serif" '
            f'font-weight="700">{char}</text>'
        )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        '<rect width="100%" height="100%" rx="6" fill="#f3f7fb" />'
        + "".join(lines)
        + "".join(chars)
        + "</svg>"
    )
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _create_captcha_challenge():
    now = time.time()
    _purge_expired_captchas(now)

    code = _generate_captcha_code()
    challenge_id = uuid4().hex
    ttl_seconds = current_app.config.get("CAPTCHA_TTL_SECONDS", 300)
    _captcha_store[challenge_id] = {
        "answer": code,
        "expires_at": now + ttl_seconds,
    }

    return challenge_id, code, _build_captcha_image(code)


def _verify_captcha(challenge_id, answer):
    _purge_expired_captchas()
    challenge = _captcha_store.pop(challenge_id, None)
    if not challenge:
        return False
    return secrets.compare_digest(challenge["answer"], answer.strip().upper())


def _build_auth_payload(user, message):
    claims = {"role": user.role}
    access_token = create_access_token(identity=str(user.id), additional_claims=claims)
    refresh_token = create_refresh_token(identity=str(user.id), additional_claims=claims)
    return {
        "message": message,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user": user.to_dict(),
    }


@auth_bp.get("/captcha")
def captcha():
    challenge_id, code, image = _create_captcha_challenge()
    payload = {
        "captcha_id": challenge_id,
        "image": image,
    }
    if current_app.config.get("TESTING"):
        payload["captcha_answer"] = code
    return payload, 200


@auth_bp.post("/register")
def register():
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    email = (payload.get("email") or "").strip() or None
    phone = (payload.get("phone") or "").strip() or None
    captcha_id = (payload.get("captcha_id") or "").strip()
    captcha_answer = (payload.get("captcha_answer") or "").strip()

    if not username or not password or not captcha_id or not captcha_answer:
        return {"message": "username, password and captcha are required"}, 400

    if len(password) < 6:
        return {"message": "password must be at least 6 characters"}, 400

    if not _verify_captcha(captcha_id, captcha_answer):
        return {"message": "invalid captcha"}, 400

    if User.query.filter_by(username=username).first():
        return {"message": "username already exists"}, 409

    if email and User.query.filter_by(email=email).first():
        return {"message": "email already exists"}, 409

    user = User(username=username, email=email, phone=phone)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    return _build_auth_payload(user, "register success"), 201


@auth_bp.post("/login")
def login():
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    captcha_id = (payload.get("captcha_id") or "").strip()
    captcha_answer = (payload.get("captcha_answer") or "").strip()

    if not username or not password or not captcha_id or not captcha_answer:
        return {"message": "username, password and captcha are required"}, 400

    if not _verify_captcha(captcha_id, captcha_answer):
        return {"message": "invalid captcha"}, 400

    user = User.query.filter_by(username=username).first()
    if user is None or not user.check_password(password):
        return {"message": "invalid username or password"}, 401

    return _build_auth_payload(user, "login success"), 200


@auth_bp.post("/refresh")
@jwt_required(refresh=True)
def refresh_token():
    user_id = get_jwt_identity()
    user = db.session.get(User, int(user_id))
    if user is None:
        return {"message": "user not found"}, 404

    access_token = create_access_token(identity=str(user.id), additional_claims={"role": user.role})
    return {"access_token": access_token}, 200


@auth_bp.post("/logout")
@jwt_required()
def logout():
    return {"message": "logout success"}, 200
