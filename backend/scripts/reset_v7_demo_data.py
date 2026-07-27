"""Safely replace the local schema-v10 demonstration snapshot.

This command never runs as part of normal application startup.  It preserves
all user rows and refuses to operate when non-demo personal or institution
accounts are present.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

from flask import Flask
from PIL import Image


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import DevelopmentConfig  # noqa: E402
from app.demo_v7 import (  # noqa: E402
    account_identity_snapshot,
    demo_snapshot_summary,
    rebuild_v7_demo_data,
    validate_reset_target,
)
from app.extensions import db, init_extensions  # noqa: E402
from app.models import InstitutionImage, ReportAsset  # noqa: E402
from app.schema import CURRENT_SCHEMA_VERSION, initialize_or_validate_schema  # noqa: E402


DEFAULT_DATABASE = BACKEND_DIR / "instance" / "health_system.db"
DEFAULT_UPLOAD_DIR = BACKEND_DIR / "uploads"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Replace local business demo records with the approved v8 scenario",
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--upload-dir", type=Path, default=DEFAULT_UPLOAD_DIR)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="required with --apply to confirm that all demo business records may be replaced",
    )
    return parser.parse_args()


def _sqlite_preflight(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {path}")
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("source SQLite integrity_check failed")
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                f"schema v{CURRENT_SCHEMA_VERSION} is required; current database is v{version}"
            )
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"source database has {len(violations)} foreign-key violation(s)")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup(database: Path, upload_dir: Path) -> dict:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = hashlib.sha256(f"{database}-{stamp}".encode()).hexdigest()[:6]
    backup_database = database.with_name(
        f"{database.stem}.before-demo-v10-{stamp}-{suffix}.db"
    )
    with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(backup_database)) as target:
            source.backup(target)
    if _sha256(backup_database) == hashlib.sha256(b"").hexdigest():
        raise RuntimeError("database backup is empty")

    upload_archive = database.with_name(
        f"uploads.before-demo-v10-{stamp}-{suffix}.zip"
    )
    unreadable_files = []
    with zipfile.ZipFile(upload_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if upload_dir.is_dir():
            for path in sorted(upload_dir.rglob("*")):
                if path.is_file():
                    try:
                        archive.write(path, path.relative_to(upload_dir).as_posix())
                    except PermissionError:
                        # A prior access-control test may deliberately leave an
                        # unreadable synthetic file. Record it explicitly; the
                        # database backup still preserves its storage metadata.
                        unreadable_files.append(path.relative_to(upload_dir).as_posix())
        archive.writestr("demo-v10-backup.json", json.dumps({
            "database": str(database),
            "database_sha256": _sha256(backup_database),
            "upload_dir": str(upload_dir),
            "created_at": datetime.now().isoformat(),
            "unreadable_files": unreadable_files,
        }, ensure_ascii=False, indent=2))
    return {
        "database": str(backup_database),
        "database_sha256": _sha256(backup_database),
        "uploads": str(upload_archive),
    }


def _make_app(database: Path, upload_dir: Path) -> Flask:
    app = Flask("healthdoc-demo-v10-reset")
    app.config.from_object(DevelopmentConfig)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{database.as_posix()}",
        SQLALCHEMY_ENGINE_OPTIONS={},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        UPLOAD_DIR=str(upload_dir),
        TESTING=False,
    )
    init_extensions(app)
    return app


def _registered_storage_keys() -> set[str]:
    return {
        *(row.storage_key for row in InstitutionImage.query.all()),
        *(row.storage_key for row in ReportAsset.query.all()),
    }


def _filesystem_storage_keys(upload_dir: Path) -> set[str]:
    if not upload_dir.is_dir():
        return set()
    return {
        path.relative_to(upload_dir).as_posix()
        for path in upload_dir.rglob("*")
        if path.is_file()
    }


def _remove_replaced_files(upload_dir: Path, old_keys: set[str], new_keys: set[str]) -> None:
    root = upload_dir.resolve()
    for key in sorted(old_keys - new_keys):
        candidate = (root / key).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            raise RuntimeError(f"refusing to delete storage key outside upload directory: {key}")
        if candidate.is_file():
            try:
                candidate.unlink()
            except PermissionError:
                # Unregistered access-test remnants may retain an intentional
                # deny ACL. They stay outside the new manifest and cannot be served.
                continue


def _safe_storage_path(root: Path, key: str) -> Path:
    resolved_root = root.resolve()
    candidate = (resolved_root / key).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise RuntimeError(f"storage key escapes upload directory: {key}") from exc
    return candidate


def _validate_staged_files(staging_dir: Path, storage_keys: set[str]) -> dict:
    asset_hashes = {row.storage_key: row.sha256 for row in ReportAsset.query.all()}
    manifest = {}
    for key in sorted(storage_keys):
        path = _safe_storage_path(staging_dir, key)
        if not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeError(f"staged demo attachment is missing or empty: {key}")
        digest = _sha256(path)
        expected = asset_hashes.get(key)
        if expected and digest != expected:
            raise RuntimeError(f"staged demo attachment hash mismatch: {key}")
        manifest[key] = {"sha256": digest, "bytes": path.stat().st_size}
    return manifest


def _publish_staged_files(staging_dir: Path, upload_dir: Path, storage_keys: set[str]) -> None:
    for key in sorted(storage_keys):
        source = _safe_storage_path(staging_dir, key)
        destination = _safe_storage_path(upload_dir, key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)


def _build_media_manifest(attachment_manifest: dict, staging_dir: Path) -> dict:
    manifest_path = BACKEND_DIR / "demo_media_manifest.json"
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {"items": []}
    previous_by_key = {
        item.get("storage_key"): item
        for item in previous.get("items", [])
        if item.get("storage_key")
    }
    assets = {row.storage_key: row for row in ReportAsset.query.all()}
    images = {row.storage_key: row for row in InstitutionImage.query.all()}
    source_catalog = json.loads(
        (BACKEND_DIR / "demo_media_sources.json").read_text(encoding="utf-8")
    )
    medical_sources_by_sha = {
        item["sha256"]: item for item in source_catalog.get("items", [])
    }
    items = []
    for key, file_data in sorted(attachment_manifest.items()):
        existing = dict(previous_by_key.get(key) or {})
        asset = assets.get(key)
        branch_image = images.get(key)
        with Image.open(_safe_storage_path(staging_dir, key)) as image:
            width, height = image.size
            image_format = image.format
        existing.update({
            "storage_key": key,
            "kind": "report_attachment" if asset else "institution_cover",
            "category": (
                asset.asset_type.name if asset and asset.asset_type
                else asset.domain.name if asset and asset.domain
                else branch_image.institution.branch_name if branch_image and branch_image.institution
                else "演示媒体"
            ),
            "title": asset.title if asset else "机构分院合成封面",
            "processing": "重新编码、清除元数据、添加“非诊断依据”演示标识",
            "mime_type": asset.mime_type if asset else "image/png",
            "width": width,
            "height": height,
            "format": image_format,
            "byte_size": file_data["bytes"],
            "sha256": file_data["sha256"],
        })
        # Real clinical examples are checked in under demo_media_sources.
        # Never replace their auditable provenance with the former v10
        # synthetic marker when rebuilding the SQLite snapshot.
        if asset:
            audited_source = medical_sources_by_sha.get(file_data["sha256"])
            if not audited_source:
                raise RuntimeError(f"医学附件不在已审核素材清单中：{key}")
            existing.update(audited_source)
            existing.update({
                "storage_key": key,
                "kind": "report_attachment",
                "asset_type_code": asset.asset_type.code if asset.asset_type else None,
                "category": asset.asset_type.name if asset.asset_type else asset.domain.name,
                "title": asset.title,
                "annotation_text": asset.annotation_text,
                "width": width,
                "height": height,
                "format": image_format,
                "byte_size": file_data["bytes"],
                "sha256": file_data["sha256"],
            })
            source = existing.get("source_url") or "https://commons.wikimedia.org/"
            if not source.startswith("https://") or "synthetic://" in source:
                raise RuntimeError(f"医学附件缺少开放授权来源：{key}")
            existing["annotation_text"] = (
                "开放授权真实医学样例，仅用于系统功能展示，不对应系统用户，不作为诊断依据。"
            )
        items.append(existing)
    return {
        "version": 11,
        "generated_at": datetime.now().astimezone().isoformat(),
        "usage_notice": "开放授权真实医学样例，仅用于 HealthDoc 功能验收；不对应系统用户，不作为诊断依据。",
        "license_references": previous.get("license_references", {}),
        "items": items,
    }


def _postflight(database: Path) -> None:
    with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("rebuilt SQLite integrity_check failed")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"rebuilt database has {len(violations)} foreign-key violation(s)")


def main():
    args = parse_args()
    database = args.database.resolve()
    upload_dir = args.upload_dir.resolve()
    _sqlite_preflight(database)
    if args.apply and not args.yes:
        raise SystemExit("--apply requires --yes; run --check-only first")

    if args.check_only:
        app = _make_app(database, upload_dir)
        with app.app_context():
            initialize_or_validate_schema()
            validate_reset_target()
            before_accounts = account_identity_snapshot()
            before_summary = demo_snapshot_summary()
            print(json.dumps({
                "database": str(database),
                "safe_to_reset": True,
                "account_count": len(before_accounts),
                "current_snapshot": before_summary,
            }, ensure_ascii=False, indent=2, default=str))
        return

    upload_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="healthdoc-demo-v10-stage-", dir=upload_dir.parent,
    ) as staging_name:
        staging_dir = Path(staging_name)
        app = _make_app(database, staging_dir)
        with app.app_context():
            initialize_or_validate_schema()
            validate_reset_target()
            before_accounts = account_identity_snapshot()
            backup = _backup(database, upload_dir)
            # The reset target is a strictly validated local demo library, so
            # stale OCR/demo files not referenced by current rows are replaced
            # together with registered attachments after the backup succeeds.
            old_keys = _registered_storage_keys() | _filesystem_storage_keys(upload_dir)
            try:
                result = rebuild_v7_demo_data(commit=False)
                new_keys = _registered_storage_keys()
                after_accounts = account_identity_snapshot()
                changed_accounts = sorted(
                    username
                    for username, identity in before_accounts.items()
                    if after_accounts.get(username) != identity
                )
                if changed_accounts:
                    raise RuntimeError(
                        "pre-commit account identity verification failed for preserved accounts: "
                        + ", ".join(changed_accounts)
                    )
                attachment_manifest = _validate_staged_files(staging_dir, new_keys)
                media_manifest = _build_media_manifest(attachment_manifest, staging_dir)
                db.session.commit()
            except Exception:
                db.session.rollback()
                raise
            _publish_staged_files(staging_dir, upload_dir, new_keys)
            _remove_replaced_files(upload_dir, old_keys, new_keys)
            db.session.remove()
            db.engine.dispose()
    (BACKEND_DIR / "demo_media_manifest.json").write_text(
        json.dumps(media_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    _postflight(database)
    manifest_digest = hashlib.sha256(
        json.dumps(attachment_manifest, sort_keys=True).encode("utf-8")
    ).hexdigest()
    print(json.dumps({
        "database": str(database),
        "backup": backup,
        "snapshot": result,
        "attachments": {
            "count": len(attachment_manifest),
            "manifest_sha256": manifest_digest,
        },
        "reset": "ok",
    }, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
