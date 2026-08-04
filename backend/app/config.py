import os
from datetime import timedelta

from dotenv import load_dotenv


load_dotenv()

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Config:
    RELEASE_COMMIT = os.getenv("RELEASE_COMMIT", "development").strip()
    PUBLIC_APP_URL = os.getenv("PUBLIC_APP_URL", "").strip().rstrip("/")
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
    AI_SUPPORTS_IMAGES = os.getenv("AI_SUPPORTS_IMAGES", "0").strip().lower() in {"1", "true", "yes", "on"}
    AI_SUPPORT_PHONE = os.getenv("AI_SUPPORT_PHONE", "")
    AI_MAX_HISTORY_MESSAGES = int(os.getenv("AI_MAX_HISTORY_MESSAGES", "20"))
    AI_GUEST_RATE_LIMIT_PER_MINUTE = int(os.getenv("AI_GUEST_RATE_LIMIT_PER_MINUTE", "10"))
    AI_AUTH_RATE_LIMIT_PER_MINUTE = int(os.getenv("AI_AUTH_RATE_LIMIT_PER_MINUTE", "30"))

    AGENT_ENABLED = os.getenv("AGENT_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    AGENT_WRITE_ENABLED = os.getenv("AGENT_WRITE_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    AGENT_ROUTER_ENABLED = os.getenv("AGENT_ROUTER_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    AGENT_DATA_ENCRYPTION_KEY = os.getenv("AGENT_DATA_ENCRYPTION_KEY", "").strip()
    ACCOUNT_CREDENTIAL_ENCRYPTION_KEY = os.getenv(
        "ACCOUNT_CREDENTIAL_ENCRYPTION_KEY",
        "dev-account-credential-key-change-me",
    ).strip()
    AGENT_THREAD_TTL_HOURS = int(os.getenv("AGENT_THREAD_TTL_HOURS", "24"))
    AGENT_ACTION_TTL_SECONDS = int(os.getenv("AGENT_ACTION_TTL_SECONDS", "600"))
    AGENT_MAX_TOOL_CALLS = int(os.getenv("AGENT_MAX_TOOL_CALLS", "10"))
    AGENT_MAX_MODEL_CALLS = int(os.getenv("AGENT_MAX_MODEL_CALLS", "8"))
    AGENT_PROMPT_VERSION = os.getenv("AGENT_PROMPT_VERSION", "agent-v1")

    RAG_HYBRID_ENABLED = os.getenv("RAG_HYBRID_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    RAG_SPARSE_MODEL = os.getenv("RAG_SPARSE_MODEL", "Qdrant/bm25")
    RAG_DENSE_PREFETCH_K = int(os.getenv("RAG_DENSE_PREFETCH_K", "24"))
    RAG_SPARSE_PREFETCH_K = int(os.getenv("RAG_SPARSE_PREFETCH_K", "24"))
    RAG_FUSION_K = int(os.getenv("RAG_FUSION_K", "12"))
    OAUTH_ENABLED = os.getenv("OAUTH_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    MCP_ENABLED = os.getenv("MCP_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    OTEL_ENABLED = os.getenv("OTEL_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318/v1/traces"
    )
    OTEL_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "healthdoc-backend")
    OAUTH_ISSUER = os.getenv("OAUTH_ISSUER", "").strip()
    MCP_RESOURCE_URL = os.getenv("MCP_RESOURCE_URL", "").strip()
    MCP_INTERNAL_KEY = os.getenv("MCP_INTERNAL_KEY", "").strip()

    RAG_ENABLED = os.getenv("RAG_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    RAG_USE_MOCK = os.getenv("RAG_USE_MOCK", "0").strip().lower() in {"1", "true", "yes", "on"}
    RAG_EMBEDDING_MODEL = os.getenv("RAG_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
    RAG_EMBEDDING_THREADS = max(1, int(os.getenv("RAG_EMBEDDING_THREADS", "1")))
    RAG_VECTOR_SIZE = int(os.getenv("RAG_VECTOR_SIZE", "512"))
    RAG_QDRANT_URL = os.getenv("RAG_QDRANT_URL", "").strip()
    RAG_QDRANT_API_KEY = os.getenv("RAG_QDRANT_API_KEY", "").strip()
    RAG_RUNTIME_PATH = os.getenv(
        "RAG_RUNTIME_PATH", os.path.join(_BACKEND_DIR, "instance", "rag")
    )
    RAG_STORAGE_PATH = os.getenv(
        "RAG_STORAGE_PATH", os.path.join(_BACKEND_DIR, "instance", "rag", "qdrant")
    )
    RAG_MODEL_CACHE_PATH = os.getenv(
        "RAG_MODEL_CACHE_PATH", os.path.join(_BACKEND_DIR, "instance", "rag", "models")
    )
    RAG_COLLECTION_ALIAS = os.getenv("RAG_COLLECTION_ALIAS", "healthdoc_knowledge_current")
    RAG_TOP_K = int(os.getenv("RAG_TOP_K", "8"))
    RAG_CHAT_CONTEXT_K = int(os.getenv("RAG_CHAT_CONTEXT_K", "4"))
    RAG_ANALYSIS_CONTEXT_K = int(os.getenv("RAG_ANALYSIS_CONTEXT_K", "6"))
    RAG_MIN_SCORE = float(os.getenv("RAG_MIN_SCORE", "0.35"))
    RAG_MAX_CONTEXT_CHARS = int(os.getenv("RAG_MAX_CONTEXT_CHARS", "12000"))

    UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(os.getcwd(), "uploads"))
    UPLOAD_URL_BASE = os.getenv("UPLOAD_URL_BASE", "/uploads")
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024
    INSTITUTION_IMAGE_MAX_BYTES = int(
        os.getenv("INSTITUTION_IMAGE_MAX_BYTES", str(5 * 1024 * 1024))
    )
    HEALTH_ASSET_MAX_BYTES = int(os.getenv("HEALTH_ASSET_MAX_BYTES", str(20 * 1024 * 1024)))
    HEALTH_ASSET_MAX_PAGES = int(os.getenv("HEALTH_ASSET_MAX_PAGES", "50"))
    HEALTH_ASSET_MAX_PIXELS = int(os.getenv("HEALTH_ASSET_MAX_PIXELS", "40000000"))
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM = os.getenv("SMTP_FROM", "healthdoc@example.test")
    SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "1").strip().lower() in {"1", "true", "yes", "on"}
    NOTIFICATION_EMAIL_DRY_RUN = os.getenv("NOTIFICATION_EMAIL_DRY_RUN", "1").strip().lower() in {"1", "true", "yes", "on"}
    # Optional local/demo mail sink. Outbox rows retain their original account
    # recipient, while the SMTP envelope is redirected to one tester mailbox.
    NOTIFICATION_EMAIL_REDIRECT = os.getenv("NOTIFICATION_EMAIL_REDIRECT", "").strip()
    # Optional shared mailbox used only when creating a fresh local demo set.
    # It stays outside source control so a tester's real address is never
    # committed with the deterministic fixtures.
    DEMO_SHARED_EMAIL = os.getenv("DEMO_SHARED_EMAIL", "demo-shared@example.test").strip().lower()
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
    RAG_USE_MOCK = True
    AGENT_ENABLED = True
    AGENT_WRITE_ENABLED = True
    AGENT_DATA_ENCRYPTION_KEY = "test-agent-encryption-key-not-for-production"
    ACCOUNT_CREDENTIAL_ENCRYPTION_KEY = "test-account-credential-key-not-for-production"
    OAUTH_ENABLED = True
    MCP_ENABLED = True
    MCP_INTERNAL_KEY = "test-mcp-internal-key"


class ProductionConfig(Config):
    DEBUG = False
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "").strip()
    ACCOUNT_CREDENTIAL_ENCRYPTION_KEY = os.getenv(
        "ACCOUNT_CREDENTIAL_ENCRYPTION_KEY", ""
    ).strip()
    REQUIRE_SECURE_DEFAULT_ADMIN = True


config_by_name = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
