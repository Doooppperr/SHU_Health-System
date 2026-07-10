from pathlib import Path

from flask import Flask, jsonify, send_from_directory

from .auth import auth_bp
from .comments import comments_bp
from .config import config_by_name
from .extensions import db, init_extensions
from .friends import friends_bp
from .indicators import indicators_bp
from .institutions import institutions_bp
from .records import records_bp
from .seed import seed_core_data
from .trends import trends_bp
from .users import users_bp


def create_app(config_name="development"):
    app = Flask(__name__)
    app.config.from_object(config_by_name.get(config_name, config_by_name["development"]))

    init_extensions(app)

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(users_bp, url_prefix="/api/users")
    app.register_blueprint(friends_bp, url_prefix="/api/friends")
    app.register_blueprint(institutions_bp, url_prefix="/api/institutions")
    app.register_blueprint(records_bp, url_prefix="/api/records")
    app.register_blueprint(indicators_bp, url_prefix="/api/indicators")
    app.register_blueprint(comments_bp, url_prefix="/api/comments")
    app.register_blueprint(trends_bp, url_prefix="/api/trends")

    @app.get("/api/health")
    def health_check():
        return {"status": "ok"}, 200

    @app.get("/uploads/<path:filename>")
    def serve_upload(filename: str):
        upload_dir = Path(app.config["UPLOAD_DIR"]).resolve()
        return send_from_directory(upload_dir, filename)

    @app.errorhandler(404)
    def not_found(_error):
        return jsonify({"message": "Resource not found"}), 404

    @app.errorhandler(500)
    def internal_error(_error):
        return jsonify({"message": "Internal server error"}), 500

    with app.app_context():
        Path(app.config["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)
        db.create_all()
        seed_core_data()

    return app
