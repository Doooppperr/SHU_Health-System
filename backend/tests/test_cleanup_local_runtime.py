import json
import sqlite3

from scripts.cleanup_local_runtime import cleanup_uploads


def _create_database(path):
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE health_records "
        "(report_file_url TEXT, ocr_raw_text TEXT)"
    )
    connection.execute("CREATE TABLE institution_images (storage_key TEXT)")
    connection.execute(
        "INSERT INTO health_records VALUES (?, ?)",
        (
            "/uploads/reports/current.pdf",
            json.dumps({"pending": {"file_url": "/uploads/reports/pending.pdf"}}),
        ),
    )
    connection.execute(
        "INSERT INTO institution_images VALUES (?)",
        ("institutions/1/cover.png",),
    )
    connection.commit()
    connection.close()


def test_cleanup_uploads_is_dry_run_by_default(tmp_path):
    database = tmp_path / "health.db"
    upload_dir = tmp_path / "uploads"
    _create_database(database)
    for relative in (
        "reports/current.pdf",
        "reports/pending.pdf",
        "institutions/1/cover.png",
        "reports/orphan.pdf",
    ):
        path = upload_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode())

    report = cleanup_uploads(database, upload_dir)

    assert report.referenced_count == 3
    assert report.orphan_count == 1
    assert report.removed_count == 0
    assert (upload_dir / "reports/orphan.pdf").exists()


def test_cleanup_uploads_removes_only_unreferenced_files(tmp_path):
    database = tmp_path / "health.db"
    upload_dir = tmp_path / "uploads"
    _create_database(database)
    referenced = upload_dir / "reports/current.pdf"
    orphan = upload_dir / "stale/orphan.pdf"
    referenced.parent.mkdir(parents=True)
    orphan.parent.mkdir(parents=True)
    referenced.write_bytes(b"keep")
    orphan.write_bytes(b"remove")

    report = cleanup_uploads(database, upload_dir, apply=True)

    assert report.removed_count == 1
    assert referenced.read_bytes() == b"keep"
    assert not orphan.exists()
    assert not orphan.parent.exists()
