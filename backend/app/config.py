import os
from datetime import timedelta

from dotenv import load_dotenv


load_dotenv()


class Config:
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-jwt-secret-change-me-please-32chars")
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=30)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=7)

    OCR_PROVIDER = os.getenv("OCR_PROVIDER", "huawei")
    OCR_USE_MOCK = os.getenv("OCR_USE_MOCK", "1").strip().lower() in {"1", "true", "yes", "on"}

    HUAWEI_OCR_ENDPOINT = os.getenv("HUAWEI_OCR_ENDPOINT", "")
    HUAWEI_OCR_AK = os.getenv("HUAWEI_OCR_AK", "")
    HUAWEI_OCR_SK = os.getenv("HUAWEI_OCR_SK", "")
    HUAWEI_PROJECT_ID = os.getenv("HUAWEI_PROJECT_ID", "")
    OCR_API_PATH = os.getenv("OCR_API_PATH", "/v2/{project_id}/ocr/general-table")
    OCR_PDF_MAX_PAGES = int(os.getenv("OCR_PDF_MAX_PAGES", "8"))
    OCR_AUTO_CONFIRM_MIN_SCORE = float(os.getenv("OCR_AUTO_CONFIRM_MIN_SCORE", "0.92"))

    UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(os.getcwd(), "uploads"))
    UPLOAD_URL_BASE = os.getenv("UPLOAD_URL_BASE", "/uploads")
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024


class DevelopmentConfig(Config):
    DEBUG = True


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    JWT_SECRET_KEY = "test-jwt-secret-at-least-32-chars-long"
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=5)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(minutes=30)
    OCR_USE_MOCK = True


class ProductionConfig(Config):
    DEBUG = False


config_by_name = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
