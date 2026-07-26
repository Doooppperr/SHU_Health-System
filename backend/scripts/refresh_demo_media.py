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
    if expected_report_count < 15 or expected_cover_count < 15:
        raise RuntimeError("素材清单至少应包含 15 张体检附件和 15 张机构封面")
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
            if image.getexif():
                raise RuntimeError(f"素材仍包含 EXIF 元数据：{key}")
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
                result = connection.execute(text("""
                    UPDATE report_assets
                    SET modality = :modality,
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
                    "annotation": "开放授权演示附件，仅用于系统功能展示，不作为诊断依据。",
                })
                matched += result.rowcount
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
