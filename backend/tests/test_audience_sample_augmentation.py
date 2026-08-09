from app.demo_v7 import (
    AUDIENCE_SAMPLE_USERNAMES,
    ensure_demo_audience_samples,
)
from app.models import Institution, InstitutionReport, User
from app.services.audience_insights import _aggregate
from app.services.report_conclusions import missing_conclusion_domains


def test_audience_sample_augmentation_is_inactive_diverse_and_idempotent(app):
    with app.app_context():
        first = ensure_demo_audience_samples()
        second = ensure_demo_audience_samples()

        assert first == {
            "sample_users": 12,
            "branches": 15,
            "created_users": 12,
            "created_reports": 180,
        }
        assert second["created_users"] == 0
        assert second["created_reports"] == 0
        subjects = User.query.filter(User.username.in_(AUDIENCE_SAMPLE_USERNAMES)).all()
        assert len(subjects) == 12
        assert all(not item.is_active for item in subjects)
        assert {item.gender for item in subjects} == {"female", "male"}
        sample_reports = [
            item for item in InstitutionReport.query.filter_by(status="published").all()
            if (item.ocr_diagnostics or {}).get("fixture") == "audience_sample_v1"
        ]
        assert len(sample_reports) == 180
        for report in sample_reports:
            allowed_domains = {
                item.health_domain_id for item in report.package_version.domains
            }
            assert len(report.indicators) >= 5
            assert all(
                item.display_domain_id in allowed_domains
                for item in report.indicators
            )
            assert missing_conclusion_domains(report) == []
            if report.institution.organization.name == "澄心健康管理中心":
                assert len(report.indicators) >= 12

        branch = Institution.query.order_by(Institution.id).first()
        reports = InstitutionReport.query.filter_by(
            institution_id=branch.id,
            status="published",
        ).all()
        aggregate = _aggregate(
            reports,
            scope="branch",
            period_days=0,
            period_start=None,
            package_catalog=[],
        )
        assert aggregate["unique_user_count"] >= 12
        assert len([
            row for row in aggregate["gender_distribution"] if row["count"] > 0
        ]) >= 2
        assert len([
            row for row in aggregate["age_distribution"] if row["count"] > 0
        ]) >= 5
