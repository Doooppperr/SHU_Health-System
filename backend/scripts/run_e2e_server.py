"""Run an isolated schema-v12 demo backend for Playwright.

The process creates its database and uploads in a temporary directory, exposes
captcha answers only through Flask's existing testing configuration, and
removes all generated state when Playwright stops the server.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

BACKEND_DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIRECTORY))

from app import create_app
from app.config import TestingConfig
from app.demo_v7 import rebuild_v7_demo_data


def _copy_manifest_media(source_root: Path, destination_root: Path) -> int:
    """Copy only the acceptance media explicitly approved by the v12 manifest."""

    manifest_path = BACKEND_DIRECTORY / "report_media_manifest.json"
    with manifest_path.open("r", encoding="utf-8") as manifest_file:
        manifest = json.load(manifest_file)

    if manifest.get("version") != 12:
        raise RuntimeError("E2E media manifest must use schema version 12")
    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        raise RuntimeError("E2E media manifest does not contain approved media")

    source_root = source_root.resolve()
    destination_root = destination_root.resolve()
    copied_keys: set[str] = set()
    for item in items:
        storage_key = item.get("storage_key")
        if not isinstance(storage_key, str) or not storage_key.strip():
            raise RuntimeError("E2E media manifest contains an invalid storage key")
        normalized_key = storage_key.replace("\\", "/")
        if normalized_key.startswith("/") or any(
            segment in {"", ".", ".."} for segment in normalized_key.split("/")
        ):
            raise RuntimeError(
                f"E2E media manifest contains an unsafe storage key: {storage_key}"
            )
        if normalized_key in copied_keys:
            raise RuntimeError(f"E2E media manifest contains a duplicate: {normalized_key}")

        source = (source_root / normalized_key).resolve()
        destination = (destination_root / normalized_key).resolve()
        try:
            source.relative_to(source_root)
            destination.relative_to(destination_root)
        except ValueError as exc:
            raise RuntimeError(
                f"E2E media manifest path escapes the upload root: {normalized_key}"
            ) from exc
        if not source.is_file():
            raise RuntimeError(f"E2E approved media is missing: {normalized_key}")

        expected_size = item.get("byte_size")
        if not isinstance(expected_size, int) or source.stat().st_size != expected_size:
            raise RuntimeError(f"E2E approved media size mismatch: {normalized_key}")
        expected_hash = item.get("sha256")
        if (
            not isinstance(expected_hash, str)
            or hashlib.sha256(source.read_bytes()).hexdigest() != expected_hash
        ):
            raise RuntimeError(f"E2E approved media hash mismatch: {normalized_key}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied_keys.add(normalized_key)
    return len(copied_keys)


runtime_directory = Path(tempfile.mkdtemp(prefix="healthdoc-e2e-")).resolve()
atexit.register(shutil.rmtree, runtime_directory, True)

TestingConfig.SQLALCHEMY_DATABASE_URI = (
    f"sqlite:///{(runtime_directory / 'healthdoc-e2e.db').as_posix()}"
)
TestingConfig.UPLOAD_DIR = str(runtime_directory / "uploads")
source_uploads = BACKEND_DIRECTORY / "uploads"
_copy_manifest_media(source_uploads, Path(TestingConfig.UPLOAD_DIR))

app = create_app("testing")
with app.app_context():
    rebuild_v7_demo_data()


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int(os.getenv("BACKEND_PORT", "5050")),
        threaded=False,
        use_reloader=False,
    )
