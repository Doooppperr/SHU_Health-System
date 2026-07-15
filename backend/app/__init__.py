from pathlib import Path

from flask import Flask, abort, jsonify, send_from_directory

from .admin import admin_bp
from .ai import ai_bp
from .auth import auth_bp
from .comments import comments_bp
from .config import config_by_name
from .extensions import db, init_extensions
from .friends import friends_bp
from .health import health_bp
from .indicators import indicators_bp
from .institutions import institutions_bp
from .exam_reports import exam_reports_bp
from .models import InstitutionImage
from .org import org_bp
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
    app.register_blueprint(ai_bp, url_prefix="/api/ai")
    app.register_blueprint(users_bp, url_prefix="/api/users")
    app.register_blueprint(profile_bp, url_prefix="/api/profile")
    app.register_blueprint(health_bp, url_prefix="/api")
    app.register_blueprint(exam_reports_bp, url_prefix="/api/exam-reports")
    app.register_blueprint(friends_bp, url_prefix="/api/friends")
    app.register_blueprint(institutions_bp, url_prefix="/api/institutions")
    app.register_blueprint(org_bp, url_prefix="/api/org")
    app.register_blueprint(indicators_bp, url_prefix="/api/indicators")
    app.register_blueprint(comments_bp, url_prefix="/api/comments")

    @app.get("/api/health")
    def health_check():
        return {"status": "ok"}, 200

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

    @app.errorhandler(404)
    def not_found(_error):
        return jsonify({"message": "Resource not found"}), 404

    @app.errorhandler(500)
    def internal_error(_error):
        return jsonify({"message": "Internal server error"}), 500

    with app.app_context():
        Path(app.config["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)
        initialize_or_validate_schema()
        seed_core_data()

    return app
