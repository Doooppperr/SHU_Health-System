import os

from app.config import DevelopmentConfig, ProductionConfig


def test_runtime_uses_local_sqlite_by_default():
    expected_uri = (
        os.getenv("DATABASE_URL")
        or os.getenv("LOCAL_DATABASE_URL")
        or "sqlite:///health_system.db"
    )
    assert DevelopmentConfig.SQLALCHEMY_DATABASE_URI == expected_uri
    assert ProductionConfig.SQLALCHEMY_DATABASE_URI == expected_uri
    assert DevelopmentConfig.SQLALCHEMY_ENGINE_OPTIONS == {}
    assert ProductionConfig.SQLALCHEMY_ENGINE_OPTIONS == {}


def test_ai_defaults_to_deepseek_v4_flash_without_embedding_a_key():
    assert DevelopmentConfig.AI_PROVIDER == "deepseek"
    assert DevelopmentConfig.DEEPSEEK_MODEL == "deepseek-v4-flash"
    assert DevelopmentConfig.DEEPSEEK_API_KEY == os.getenv("DEEPSEEK_API_KEY", "")
