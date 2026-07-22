import pytest

from app.demo_v7 import (
    ACCOUNT_IDENTITY_FIELDS,
    DemoResetSafetyError,
    account_identity_snapshot,
    ensure_v7_demo_accounts,
    rebuild_v7_demo_data,
    validate_reset_target,
)
from app.extensions import db
from app.models import (
    Appointment, BookingGroup, HealthDomain, Institution, InstitutionReport, Organization,
    NotificationOutbox, Package, PackageVersion, ReportAccessLog, ReportAsset, ReportTextResult,
    User, WaitlistSubscription,
)


EXPECTED_CATALOG = {
    "澄心健康管理中心": {
        "都市年度基础体检": ({"basic", "cardio", "metabolic", "digestive", "renal"}, 699.0),
        "心脑血管风险筛查": ({"cardio"}, 899.0),
        "家庭长辈健康评估": ({"basic", "cardio", "metabolic", "renal"}, 1299.0),
    },
    "衡康代谢与慢病管理中心": {
        "糖脂代谢专项": ({"metabolic"}, 799.0),
        "肝胆代谢联合评估": ({"metabolic", "digestive"}, 999.0),
        "慢病风险综合评估": ({"basic", "cardio", "metabolic", "renal"}, 1299.0),
    },
    "云川影像与呼吸体检中心": {
        "呼吸与肺功能专项": ({"respiratory"}, 699.0),
        "心电与循环影像专项": ({"cardio"}, 899.0),
        "职场综合体检": ({"basic", "cardio", "respiratory", "digestive"}, 1099.0),
    },
}


def test_v8_demo_catalog_and_platform_scenarios_are_visible(app):
    with app.app_context():
        assert Organization.query.count() == 5
        assert Institution.query.count() == 15
        assert Package.query.count() == 25
        assert PackageVersion.query.count() == 26
        assert {item.name: len(item.branches) for item in Organization.query.all()} == {
            "澄心健康管理中心": 5,
            "衡康代谢与慢病管理中心": 4,
            "云川影像与呼吸体检中心": 3,
            "安沐女性与家庭健康中心": 2,
            "仁序职业健康与综合体检中心": 1,
        }
        assert {
            item.name: Package.query.join(Institution).filter(Institution.organization_id == item.id).count()
            for item in Organization.query.all()
        } == {
            "澄心健康管理中心": 8,
            "衡康代谢与慢病管理中心": 6,
            "云川影像与呼吸体检中心": 5,
            "安沐女性与家庭健康中心": 4,
            "仁序职业健康与综合体检中心": 2,
        }
        assert all(len(item.images) == 1 for item in Institution.query.all())
        for institution in Institution.query.order_by(Institution.id).limit(3).all():
            expected = EXPECTED_CATALOG[institution.name]
            assert {item.name for item in institution.packages} == set(expected)
            for package in institution.packages:
                version = db.session.get(PackageVersion, package.current_version_id)
                actual_domains = {row.domain.code for row in version.domains}
                expected_domains, expected_price = expected[package.name]
                assert actual_domains == expected_domains
                assert float(package.price) == expected_price
                assert package.audience and package.booking_notice

        family_groups = BookingGroup.query.filter_by(party_size=3).all()
        assert family_groups and any(len(item.appointments) == 3 for item in family_groups)
        assert {row.status for row in Appointment.query.all()} >= {
            "unfulfilled", "awaiting_report", "fulfilled", "cancelled", "invalidated"
        }
        assert {row.status for row in WaitlistSubscription.query.all()} >= {"active", "invalid"}
        assert NotificationOutbox.query.filter_by(event_type="waitlist_available", status="sent").count() >= 1
        assert ReportTextResult.query.count() >= 5
        assert ReportAsset.query.count() >= 3
        single = Organization.query.filter_by(name="仁序职业健康与综合体检中心").one()
        assert len(single.branches) == 1
        assert ReportAccessLog.query.filter(
            ReportAccessLog.actor_institution_id.in_([item.id for item in single.branches]),
            ReportAccessLog.source_institution_id.notin_([item.id for item in single.branches]),
        ).count() == 0


def test_published_demo_results_stay_inside_booking_package_domains(app):
    with app.app_context():
        for report in InstitutionReport.query.filter_by(status="published").all():
            allowed = {row.health_domain_id for row in report.package_version.domains}
            actual = {row.display_domain_id for row in report.indicators}
            actual.update(row.health_domain_id for row in report.text_results)
            actual.update(row.health_domain_id for row in report.assets)
            assert actual <= allowed
            if report.appointment is not None:
                assert report.appointment.status == "fulfilled"
            else:
                assert report.ocr_diagnostics["import_kind"] == "historical_paper_archive"


def test_v8_demo_rebuild_preserves_every_account_identity_field(app):
    with app.app_context():
        before = account_identity_snapshot()
        assert before
        result = rebuild_v7_demo_data()
        after = account_identity_snapshot()
        assert after == before
        assert result["organizations"] == 5
        assert result["institutions"] == 15
        assert result["packages"] == 25
        assert result["package_versions"] == 26
        assert result["report_assets"] >= 3
        assert all(len(values) == len(ACCOUNT_IDENTITY_FIELDS) for values in after.values())


def test_v7_demo_reset_refuses_unknown_personal_accounts(app):
    with app.app_context():
        outsider = User(
            username="real-person",
            role="user",
            health_id="HID-REALTEST1",
            email="real-person@example.test",
        )
        outsider.set_password("not-a-demo-password")
        db.session.add(outsider)
        db.session.commit()
        with pytest.raises(DemoResetSafetyError, match="unknown accounts"):
            validate_reset_target()


def test_v7_demo_reset_also_refuses_unknown_admin_accounts(app):
    with app.app_context():
        outsider = User(username="unexpected-admin", role="admin", email="unexpected-admin@example.test")
        outsider.set_password("not-a-demo-password")
        db.session.add(outsider)
        db.session.commit()
        with pytest.raises(DemoResetSafetyError, match="unknown accounts"):
            validate_reset_target()


def test_demo_mail_sync_only_updates_the_fixed_demo_account_allowlist(app):
    requested_mailbox = "shared-demo-mailbox@example.test"
    with app.app_context():
        demo = User.query.filter_by(username="test1").one()
        outsider = User(
            username="real-mail-owner",
            role="user",
            health_id="HID-REALMAIL1",
            email="keep-this-address@example.test",
        )
        outsider.set_password("not-a-demo-password")
        db.session.add(outsider)
        demo.email = "old-demo-address@example.test"
        db.session.commit()

        app.config["DEMO_SHARED_EMAIL"] = requested_mailbox
        ensure_v7_demo_accounts()

        assert User.query.filter_by(username="test1").one().email == requested_mailbox
        assert User.query.filter_by(username="real-mail-owner").one().email == "keep-this-address@example.test"


def test_v7_demo_has_multi_year_and_same_day_multi_source_health_data(app):
    with app.app_context():
        test1 = User.query.filter_by(username="test1").one()
        test5 = User.query.filter_by(username="test5").one()
        test1_dates = sorted(
            row.exam_date for row in InstitutionReport.query.filter_by(
                matched_user_id=test1.id, status="published"
            ).all()
        )
        assert (test1_dates[-1] - test1_dates[0]).days >= 365
        grouped = {}
        for report in InstitutionReport.query.filter_by(matched_user_id=test5.id, status="published").all():
            grouped.setdefault(report.exam_date, set()).add(report.institution_id)
        assert any(len(institutions) >= 2 for institutions in grouped.values())


def test_v7_demo_gives_institutions_today_work(app):
    from datetime import date

    with app.app_context():
        rows = Appointment.query.filter_by(appointment_date=date.today()).all()
        assert {row.status for row in rows} >= {"unfulfilled", "awaiting_report"}
        assert len({row.institution_id for row in rows}) >= 2
