import os

from app.config import DevelopmentConfig, ProductionConfig


def test_runtime_uses_local_sqlite_by_default():
    expected_uri = (
        os.getenv("LOCAL_DATABASE_URL") or "sqlite:///health_system.db"
    )
    assert DevelopmentConfig.SQLALCHEMY_DATABASE_URI == expected_uri
    assert ProductionConfig.SQLALCHEMY_DATABASE_URI == expected_uri
    assert DevelopmentConfig.SQLALCHEMY_ENGINE_OPTIONS == {}
    assert ProductionConfig.SQLALCHEMY_ENGINE_OPTIONS == {}
