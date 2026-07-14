import os
from datetime import timedelta

from dotenv import load_dotenv


load_dotenv()


class Config:
    # Local development keeps using SQLite.  Production deployments can set
    # DATABASE_URL to an opengauss+psycopg2 GaussDB/openGauss connection URL.
    # Flask-SQLAlchemy resolves the default relative SQLite path under
    # backend/instance.
    SQLALCHEMY_DATABASE_URI = (
        os.getenv("DATABASE_URL")
        or os.getenv("LOCAL_DATABASE_URL")
        or "sqlite:///health_system.db"
    )
    SQLALCHEMY_ENGINE_OPTIONS = {}
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-jwt-secret-change-me-please-32chars")
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=30)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=7)
    CAPTCHA_TTL_SECONDS = int(os.getenv("CAPTCHA_TTL_SECONDS", "300"))

    OCR_PROVIDER = os.getenv("OCR_PROVIDER", "huawei")
    OCR_USE_MOCK = os.getenv("OCR_USE_MOCK", "1").strip().lower() in {"1", "true", "yes", "on"}

    HUAWEI_OCR_ENDPOINT = os.getenv("HUAWEI_OCR_ENDPOINT", "")
    HUAWEI_OCR_AK = os.getenv("HUAWEI_OCR_AK", "")
    HUAWEI_OCR_SK = os.getenv("HUAWEI_OCR_SK", "")
    HUAWEI_PROJECT_ID = os.getenv("HUAWEI_PROJECT_ID", "")
    OCR_API_PATH = os.getenv("OCR_API_PATH", "/v2/{project_id}/ocr/general-table")
    OCR_PDF_MAX_PAGES = int(os.getenv("OCR_PDF_MAX_PAGES", "8"))
    OCR_AUTO_CONFIRM_MIN_SCORE = float(os.getenv("OCR_AUTO_CONFIRM_MIN_SCORE", "0.92"))

    AI_PROVIDER = os.getenv("AI_PROVIDER", "deepseek")
    AI_USE_MOCK = os.getenv("AI_USE_MOCK", "0").strip().lower() in {"1", "true", "yes", "on"}
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
    DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    AI_CONNECT_TIMEOUT_SECONDS = float(os.getenv("AI_CONNECT_TIMEOUT_SECONDS", "5"))
    AI_READ_TIMEOUT_SECONDS = float(os.getenv("AI_READ_TIMEOUT_SECONDS", "30"))
    AI_REQUEST_TIMEOUT_SECONDS = float(os.getenv("AI_REQUEST_TIMEOUT_SECONDS", "60"))
    AI_SUPPORT_PHONE = os.getenv("AI_SUPPORT_PHONE", "")
    AI_MAX_HISTORY_MESSAGES = int(os.getenv("AI_MAX_HISTORY_MESSAGES", "20"))
    AI_GUEST_RATE_LIMIT_PER_MINUTE = int(os.getenv("AI_GUEST_RATE_LIMIT_PER_MINUTE", "10"))
    AI_AUTH_RATE_LIMIT_PER_MINUTE = int(os.getenv("AI_AUTH_RATE_LIMIT_PER_MINUTE", "30"))

    UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(os.getcwd(), "uploads"))
    UPLOAD_URL_BASE = os.getenv("UPLOAD_URL_BASE", "/uploads")
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024
    INSTITUTION_IMAGE_MAX_BYTES = int(
        os.getenv("INSTITUTION_IMAGE_MAX_BYTES", str(5 * 1024 * 1024))
    )
    INVITE_CODE_BYTES = int(os.getenv("INVITE_CODE_BYTES", "24"))
    REQUIRE_SECURE_DEFAULT_ADMIN = False


class DevelopmentConfig(Config):
    DEBUG = True


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    JWT_SECRET_KEY = "test-jwt-secret-at-least-32-chars-long"
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=5)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(minutes=30)
    OCR_USE_MOCK = True
    AI_USE_MOCK = True
    AI_GUEST_RATE_LIMIT_PER_MINUTE = 1000
    AI_AUTH_RATE_LIMIT_PER_MINUTE = 1000


class ProductionConfig(Config):
    DEBUG = False
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "").strip()
    REQUIRE_SECURE_DEFAULT_ADMIN = True


config_by_name = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
