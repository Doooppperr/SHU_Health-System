from app.extensions import db
from app.models import HealthIndicator, HealthRecord, User
from scripts import seed_rag_demo


def test_demo_seed_is_idempotent_and_cleanup_preserves_existing_data(
    app, monkeypatch, tmp_path
):
    monkeypatch.setattr(seed_rag_demo, "MANIFEST_PATH", tmp_path / "demo_seed.json")
    monkeypatch.setenv("RAG_DEMO_PASSWORD", "test-only-demo-password")

    with app.app_context():
        baseline = {
            "users": db.session.query(User).count(),
            "records": db.session.query(HealthRecord).count(),
            "indicators": db.session.query(HealthIndicator).count(),
        }
        applied = seed_rag_demo.apply_seed()
        repeated = seed_rag_demo.apply_seed()

        assert applied == {
            "status": "applied",
            "users": 5,
            "records": 100,
            "indicators": 900,
        }
        assert repeated["status"] == "already_applied"
        assert seed_rag_demo.verify() == {
            "users": 5,
            "records": 100,
            "indicators": 900,
        }

        cleaned = seed_rag_demo.cleanup()
        assert cleaned == {
            "status": "cleaned",
            "users": 5,
            "records": 100,
            "indicators": 900,
        }
        assert db.session.query(User).count() == baseline["users"]
        assert db.session.query(HealthRecord).count() == baseline["records"]
        assert db.session.query(HealthIndicator).count() == baseline["indicators"]
