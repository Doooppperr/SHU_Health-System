import sqlite3

from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine


@event.listens_for(Engine, "connect")
def configure_sqlite_connection(dbapi_connection, _connection_record):
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return

    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


db = SQLAlchemy()
migrate = Migrate()
jwt = JWTManager()
cors = CORS()


def init_extensions(app):
    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    @jwt.additional_claims_loader
    def add_token_version(identity):
        from app.models import User
        try:
            user = db.session.get(User, int(identity))
        except (TypeError, ValueError):
            user = None
        return {"token_version": user.token_version if user else -1}

    @jwt.token_verification_loader
    def verify_token_version(_header, payload):
        from app.models import User
        try:
            user = db.session.get(User, int(payload.get("sub")))
        except (TypeError, ValueError):
            return False
        return bool(user and payload.get("token_version", 0) == user.token_version)

    @jwt.token_verification_failed_loader
    def token_version_failed(_header, _payload):
        return {"message": "登录状态已经失效，请重新登录", "code": "TOKEN_REVOKED"}, 401

    @jwt.unauthorized_loader
    def missing_token(_reason):
        return {"message": "请先登录后再继续操作", "code": "LOGIN_REQUIRED"}, 401

    @jwt.invalid_token_loader
    def invalid_token(_reason):
        return {"message": "登录凭证无效，请重新登录", "code": "TOKEN_INVALID"}, 401

    @jwt.expired_token_loader
    def expired_token(_header, _payload):
        return {"message": "登录已过期，请重新登录", "code": "TOKEN_EXPIRED"}, 401
    cors.init_app(app, resources={r"/api/*": {"origins": "*"}})
