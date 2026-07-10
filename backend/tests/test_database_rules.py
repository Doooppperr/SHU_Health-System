import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    Comment,
    FriendRelation,
    HealthIndicator,
    HealthRecord,
    IndicatorCategory,
    IndicatorDict,
    Institution,
    Package,
    User,
)


def _constraint_names(model):
    return {constraint.name for constraint in model.__table__.constraints}


def test_database_check_constraints_are_registered():
    expected = {
        User: {"ck_users_role", "ck_users_username_not_blank"},
        FriendRelation: {"ck_friend_not_self", "ck_friend_relation_name_not_blank"},
        Comment: {"ck_comments_rating_range", "ck_comments_content_not_blank"},
        IndicatorCategory: {"ck_indicator_categories_name_not_blank"},
        IndicatorDict: {
            "ck_indicator_dicts_code_not_blank",
            "ck_indicator_dicts_name_not_blank",
            "ck_indicator_dicts_value_type",
            "ck_indicator_dicts_reference_range",
        },
        Institution: {
            "ck_institutions_name_not_blank",
            "ck_institutions_branch_not_blank",
            "ck_institutions_address_not_blank",
            "ck_institutions_district_not_blank",
        },
        Package: {
            "ck_packages_name_not_blank",
            "ck_packages_focus_area_not_blank",
            "ck_packages_gender_scope",
            "ck_packages_price_non_negative",
        },
        HealthRecord: {"ck_health_records_status"},
        HealthIndicator: {"ck_health_indicators_value_not_blank", "ck_health_indicators_source"},
    }

    for model, constraint_names in expected.items():
        assert constraint_names <= _constraint_names(model)


def test_sqlite_foreign_keys_are_enabled(app):
    with app.app_context():
        assert db.session.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_named_constraints_exist_in_sqlite_schema(app):
    expected_names = {
        constraint.name
        for model in (
            User,
            FriendRelation,
            Comment,
            IndicatorCategory,
            IndicatorDict,
            Institution,
            Package,
            HealthRecord,
            HealthIndicator,
        )
        for constraint in model.__table__.constraints
        if constraint.name
    }

    with app.app_context():
        table_sql = db.session.execute(
            text(
                """
                SELECT sql
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                """
            )
        ).scalars()
        schema = "\n".join(sql for sql in table_sql if sql)

    missing_names = expected_names - {
        name for name in expected_names if name in schema
    }
    assert missing_names == set()


def test_sqlite_check_constraint_is_enforced(app):
    with app.app_context():
        with pytest.raises(IntegrityError):
            db.session.execute(
                text(
                    """
                    INSERT INTO users
                        (username, password_hash, role, created_at)
                    VALUES
                        ('invalid-role-user', 'not-used', 'invalid', CURRENT_TIMESTAMP)
                    """
                )
            )
            db.session.commit()
        db.session.rollback()


def test_sqlite_foreign_key_is_enforced(app):
    with app.app_context():
        with pytest.raises(IntegrityError):
            db.session.execute(
                text(
                    """
                    INSERT INTO comments
                        (user_id, institution_id, content, rating, is_visible, created_at)
                    VALUES
                        (999999, 999999, 'invalid foreign keys', 5, 0, CURRENT_TIMESTAMP)
                    """
                )
            )
            db.session.commit()
        db.session.rollback()


def test_sqlite_candidate_key_is_enforced(app):
    with app.app_context():
        with pytest.raises(IntegrityError):
            db.session.execute(
                text(
                    """
                    INSERT INTO institutions
                        (name, branch_name, address, district)
                    SELECT
                        name, branch_name, address, district
                    FROM institutions
                    ORDER BY id
                    LIMIT 1
                    """
                )
            )
            db.session.commit()
        db.session.rollback()
