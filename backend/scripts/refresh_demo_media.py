"""Validate or apply the narrowly scoped schema-v10 demo media refresh.

The command never creates or deletes business rows. In apply mode it only
updates metadata for report assets whose exact storage keys are present in the
checked-in generated-and-licensed media manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from PIL import Image
from sqlalchemy import create_engine, text


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = BACKEND_ROOT / "demo_media_manifest.json"
EXPECTED_PREFIXES = (
    "health-assets/demo-v8/",
    "health-assets/demo-v10/",
    "institutions/demo-v8/",
)
CORE_TABLES = ("users", "institutions", "appointments", "institution_reports", "comments", "packages")
ALLOWED_ASSET_TYPES = {
    "US_THYROID", "US_ABDOMEN", "SPIROMETRY", "ECG_12",
    "CHEST_IMAGE", "ECHO_HEART", "BLOOD_MICROSCOPY",
}
ALLOWED_LICENSE_MARKERS = ("public domain", "cc0", "cc by", "cc-by")
LEGACY_STORAGE_KEYS = {
    "health-assets/demo-v10/report-65-spirometry.png":
        ("health-assets/demo-v10/report-65-ecg_12.png", 65),
    "health-assets/demo-v10/report-65-chest_image.png":
        ("health-assets/demo-v10/report-65-echo_heart.png", 65),
    "health-assets/demo-v8/report-10-echo_heart.png":
        ("health-assets/demo-v8/report-21-basic.png", 10),
}


def parse_args():
    parser = argparse.ArgumentParser(description="校验或刷新开放授权演示素材")
    parser.add_argument("--upload-dir", type=Path, default=Path(os.getenv("UPLOAD_DIR", BACKEND_ROOT / "uploads")))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or os.getenv("LOCAL_DATABASE_URL"))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--yes", action="store_true")
    return parser.parse_args()


def validate(upload_dir: Path) -> list[dict]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    items = manifest.get("items") or []
    if not items or len({item.get("storage_key") for item in items}) != len(items):
        raise RuntimeError("素材清单不能为空且不得包含重复文件")
    expected_report_count = sum(item.get("kind") == "report_attachment" for item in items)
    expected_cover_count = sum(item.get("kind") == "institution_cover" for item in items)
    if expected_report_count != 19 or expected_cover_count != 15:
        raise RuntimeError("素材清单必须恰好包含 19 张体检附件和 15 张机构封面")
    upload_root = upload_dir.resolve()
    for item in items:
        key = item["storage_key"]
        if not key.startswith(EXPECTED_PREFIXES) or ".." in Path(key).parts:
            raise RuntimeError(f"素材路径超出允许范围：{key}")
        path = (upload_root / key).resolve()
        try:
            path.relative_to(upload_root)
        except ValueError as exc:
            raise RuntimeError(f"素材路径超出上传目录：{key}") from exc
        if not path.is_file():
            raise RuntimeError(f"缺少演示素材：{key}")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != item["sha256"] or len(raw) != item["byte_size"]:
            raise RuntimeError(f"素材哈希或大小不匹配：{key}")
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            if image.format != "PNG" or image.size != (item["width"], item["height"]):
                raise RuntimeError(f"素材格式或尺寸不匹配：{key}")
            if image.getexif() or any(name in image.info for name in ("exif", "iptc", "xmp", "XML:com.adobe.xmp")):
                raise RuntimeError(f"素材仍包含 EXIF/IPTC/XMP 元数据：{key}")
        if item.get("kind") == "report_attachment":
            if item.get("asset_type_code") not in ALLOWED_ASSET_TYPES:
                raise RuntimeError(f"医学附件缺少标准槽位：{key}")
            if not str(item.get("source_url", "")).startswith("https://"):
                raise RuntimeError(f"医学附件来源必须为 HTTPS：{key}")
            if not str(item.get("original_download_url", "")).startswith("https://"):
                raise RuntimeError(f"医学附件缺少 HTTPS 原始下载地址：{key}")
            if not str(item.get("author", "")).strip():
                raise RuntimeError(f"医学附件缺少作者或来源机构：{key}")
            if not str(item.get("clinical_presentation", "")).strip():
                raise RuntimeError(f"医学附件缺少医学表现说明：{key}")
            if not str(item.get("processing", "")).strip():
                raise RuntimeError(f"医学附件缺少处理方式说明：{key}")
            if not str(item.get("retrieved_at", "")).strip():
                raise RuntimeError(f"医学附件缺少获取时间：{key}")
            if not item.get("license_url"):
                raise RuntimeError(f"医学附件缺少许可证链接：{key}")
            license_name = str(item.get("license") or "").lower()
            if not any(marker in license_name for marker in ALLOWED_LICENSE_MARKERS):
                raise RuntimeError(f"医学附件许可证不允许项目再分发：{key}")
            if "synthetic" in str(item.get("source_url", "")).lower():
                raise RuntimeError(f"医学附件不得使用合成来源：{key}")
            if not item.get("pii_review"):
                raise RuntimeError(f"医学附件缺少患者身份信息复核记录：{key}")
    return items


def apply(database_url: str, items: list[dict]) -> None:
    if not database_url:
        raise RuntimeError("应用素材刷新时必须提供 DATABASE_URL")
    report_items = [item for item in items if item["kind"] == "report_attachment"]
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            before = {
                name: connection.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar_one()
                for name in CORE_TABLES
            }
            matched = 0
            for item in report_items:
                legacy = LEGACY_STORAGE_KEYS.get(item["storage_key"])
                if legacy:
                    legacy_key, target_report_id = legacy
                    connection.execute(text("""
                        UPDATE report_assets
                        SET storage_key = :storage_key,
                            report_id = :target_report_id,
                            uploaded_by_user_id = (
                                SELECT created_by_user_id
                                FROM institution_reports
                                WHERE id = :target_report_id
                            )
                        WHERE storage_key = :legacy_key
                          AND NOT EXISTS (
                              SELECT 1
                              FROM report_assets AS current_asset
                              WHERE current_asset.storage_key = :storage_key
                          )
                    """), {
                        "storage_key": item["storage_key"],
                        "legacy_key": legacy_key,
                        "target_report_id": target_report_id,
                    })
                    connection.execute(text("""
                        UPDATE report_asset_annotations
                        SET created_by_user_id = (
                            SELECT r.created_by_user_id
                            FROM report_assets AS a
                            JOIN institution_reports AS r ON r.id = a.report_id
                            WHERE a.storage_key = :storage_key
                        )
                        WHERE report_asset_id = (
                            SELECT id FROM report_assets
                            WHERE storage_key = :storage_key
                        )
                    """), {"storage_key": item["storage_key"]})
                result = connection.execute(text("""
                    UPDATE report_assets
                    SET modality = :modality,
                        asset_type_id = (SELECT id FROM report_asset_types WHERE code = :asset_type_code),
                        health_domain_id = (SELECT health_domain_id FROM report_asset_types WHERE code = :asset_type_code),
                        title = :title,
                        mime_type = :mime_type,
                        byte_size = :byte_size,
                        width = :width,
                        height = :height,
                        sha256 = :sha256,
                        annotation_text = :annotation
                    WHERE storage_key = :storage_key
                """), {
                    **item,
                    "modality": "open_license_demo_image",
                    "annotation": "开放授权真实医学样例，仅用于系统功能展示，不对应系统用户，不作为诊断依据。",
                })
                matched += result.rowcount
                connection.execute(text("""
                    INSERT INTO package_version_domains (
                        package_version_id, health_domain_id, sort_order
                    )
                    SELECT
                        r.package_version_id,
                        t.health_domain_id,
                        COALESCE((
                            SELECT MAX(existing.sort_order)
                            FROM package_version_domains AS existing
                            WHERE existing.package_version_id = r.package_version_id
                        ), -1) + 1
                    FROM report_assets AS a
                    JOIN institution_reports AS r ON r.id = a.report_id
                    JOIN report_asset_types AS t ON t.id = a.asset_type_id
                    WHERE a.storage_key = :storage_key
                      AND r.package_version_id IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1
                          FROM package_version_domains AS existing
                          WHERE existing.package_version_id = r.package_version_id
                            AND existing.health_domain_id = t.health_domain_id
                      )
                """), {"storage_key": item["storage_key"]})
            if matched != len(report_items):
                raise RuntimeError(f"数据库中只找到 {matched}/{len(report_items)} 个目标附件，已取消刷新")
            after = {
                name: connection.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar_one()
                for name in CORE_TABLES
            }
            if before != after:
                raise RuntimeError("素材刷新改变了核心业务数据数量，已取消")
    finally:
        engine.dispose()


def main():
    args = parse_args()
    items = validate(args.upload_dir)
    if args.check_only:
        report_count = sum(item.get("kind") == "report_attachment" for item in items)
        cover_count = sum(item.get("kind") == "institution_cover" for item in items)
        print(f"演示素材校验通过：{report_count} 张体检附件、{cover_count} 张机构封面")
        return
    if not args.yes:
        raise RuntimeError("应用素材刷新必须同时传入 --yes")
    apply(args.database_url, items)
    print("开放授权演示素材刷新完成，核心业务数据数量未改变")


if __name__ == "__main__":
    main()
