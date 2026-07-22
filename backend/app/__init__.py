from pathlib import Path
import re
import os

from flask import Flask, abort, jsonify, send_from_directory

from .admin import admin_bp
from .appointments import appointments_bp
from .booking_v7 import booking_v7_bp
from .ai import ai_bp
from .auth import auth_bp
from .comments import comments_bp
from .config import config_by_name
from .extensions import db, init_extensions
from .friends import friends_bp
from .health import health_bp
from .health_data_v7 import health_data_v7_bp
from .indicators import indicators_bp
from .institutions import institutions_bp
from .exam_reports import exam_reports_bp
from .models import InstitutionImage
from .org import org_bp
from .organizations import organizations_bp
from .profile import profile_bp
from .schema import initialize_or_validate_schema
from .seed import seed_core_data
from .users import users_bp


def _validate_runtime_security(app: Flask, config_name: str) -> None:
    if config_name != "production":
        return
    jwt_secret = str(app.config.get("JWT_SECRET_KEY") or "").strip()
    if len(jwt_secret) < 32 or jwt_secret == "dev-jwt-secret-change-me-please-32chars":
        raise RuntimeError(
            "Production startup requires an explicit JWT_SECRET_KEY of at least 32 characters. "
            "Set it in backend/.env before starting Waitress."
        )


def create_app(config_name="development"):
    app = Flask(__name__)
    app.config.from_object(config_by_name.get(config_name, config_by_name["development"]))
    _validate_runtime_security(app, config_name)

    init_extensions(app)

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(admin_bp, url_prefix="/api/admin")
    app.register_blueprint(appointments_bp, url_prefix="/api/appointments")
    app.register_blueprint(booking_v7_bp, url_prefix="/api")
    app.register_blueprint(ai_bp, url_prefix="/api/ai")
    app.register_blueprint(users_bp, url_prefix="/api/users")
    app.register_blueprint(profile_bp, url_prefix="/api/profile")
    app.register_blueprint(health_bp, url_prefix="/api")
    app.register_blueprint(health_data_v7_bp, url_prefix="/api")
    app.register_blueprint(exam_reports_bp, url_prefix="/api/exam-reports")
    app.register_blueprint(friends_bp, url_prefix="/api/friends")
    app.register_blueprint(institutions_bp, url_prefix="/api/institutions")
    app.register_blueprint(org_bp, url_prefix="/api/org")
    app.register_blueprint(organizations_bp, url_prefix="/api/organizations")
    app.register_blueprint(indicators_bp, url_prefix="/api/indicators")
    app.register_blueprint(comments_bp, url_prefix="/api/comments")

    @app.get("/api/health")
    def health_check():
        return {"status": "ok"}, 200

    @app.after_request
    def localize_api_messages(response):
        """Prevent internal English validation/database text from reaching a user."""
        if not response.is_json:
            return response
        payload = response.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("message"), str):
            return response
        message = payload["message"].strip()
        if not message or re.search(r"[\u3400-\u9fff]", message):
            return response
        exact = {
            "appointment_id is required": "请选择对应的预约记录",
            "registered user not found": "没有找到对应的已注册普通用户",
            "registered user not found or identity does not match": "没有找到对应的已注册普通用户，或身份信息不匹配",
            "friend relation not found": "没有找到这条亲友关系",
            "health data not found": "没有找到该健康数据",
            "review request not found": "没有找到该审核申请",
        }
        payload["message"] = exact.get(message, {
            400: "提交内容不正确，请检查后重试",
            401: "登录状态已失效，请重新登录",
            403: "当前账号没有执行此操作的权限",
            404: "没有找到请求的内容",
            405: "当前页面不支持此操作",
            409: "当前数据状态不允许此操作",
            413: "上传内容超过允许的大小",
            429: "请求过于频繁，请稍后再试",
        }.get(response.status_code, "操作没有完成，请稍后重试"))
        response.set_data(app.json.dumps(payload))
        response.content_type = "application/json; charset=utf-8"
        return response

    @app.get("/uploads/<path:filename>")
    def serve_upload(filename: str):
        normalized = filename.replace("\\", "/")
        if normalized.startswith("reports/"):
            abort(404)
        image_exists = InstitutionImage.query.filter_by(storage_key=normalized).first()
        if image_exists is None:
            abort(404)
        upload_dir = Path(app.config["UPLOAD_DIR"]).resolve()
        return send_from_directory(upload_dir, normalized)

    @app.errorhandler(400)
    def bad_request(_error):
        return jsonify({"message": "请求内容不正确，请检查后重试"}), 400

    @app.errorhandler(404)
    def not_found(_error):
        return jsonify({"message": "没有找到请求的内容"}), 404

    @app.errorhandler(405)
    def method_not_allowed(_error):
        return jsonify({"message": "当前页面不支持此操作"}), 405

    @app.errorhandler(413)
    def payload_too_large(_error):
        return jsonify({"message": "上传内容超过允许的大小"}), 413

    @app.errorhandler(500)
    def internal_error(_error):
        app.logger.exception("Unhandled application error", exc_info=_error)
        return jsonify({"message": "系统暂时无法完成操作，请稍后重试"}), 500

    with app.app_context():
        Path(app.config["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)
        if os.getenv("HEALTHDOC_SCHEMA_MIGRATION") != "1":
            initialize_or_validate_schema()
            seed_core_data()

    return app
