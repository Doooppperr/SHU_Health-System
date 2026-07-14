"""Safely remove local upload files that are not referenced by SQLite.

The command is dry-run by default. Use ``--apply`` only after reviewing the
reported file and byte counts. It never touches the database, virtual
environment, production openGauss data, or files outside ``backend/uploads``.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = BACKEND_DIR / "instance" / "health_system.db"
DEFAULT_UPLOAD_DIR = BACKEND_DIR / "uploads"


@dataclass(frozen=True)
class CleanupReport:
    referenced_count: int
    orphan_count: int
    orphan_bytes: int
    removed_count: int


def _walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


def _normalize_upload_key(value: str) -> str | None:
    path = urlsplit(value.strip()).path.replace("\\", "/")
    marker = "/uploads/"
    if path.startswith(marker):
        path = path[len(marker) :]
    elif path.startswith("uploads/"):
        path = path[len("uploads/") :]
    else:
        return None

    key = PurePosixPath(path)
    if not path or key.is_absolute() or ".." in key.parts:
        return None
    return key.as_posix()


def referenced_upload_keys(database_path: Path) -> set[str]:
    connection = sqlite3.connect(database_path)
    references: set[str] = set()
    try:
        for report_url, raw_text in connection.execute(
            "SELECT report_file_url, ocr_raw_text FROM health_records"
        ):
            if report_url:
                key = _normalize_upload_key(report_url)
                if key:
                    references.add(key)
            if not raw_text:
                continue
            try:
                payload = json.loads(raw_text)
            except (TypeError, json.JSONDecodeError):
                continue
            for value in _walk_strings(payload):
                key = _normalize_upload_key(value)
                if key:
                    references.add(key)

        for (storage_key,) in connection.execute(
            "SELECT storage_key FROM institution_images"
        ):
            if not storage_key:
                continue
            normalized = storage_key.replace("\\", "/").lstrip("/")
            key = PurePosixPath(normalized)
            if normalized and not key.is_absolute() and ".." not in key.parts:
                references.add(key.as_posix())
    finally:
        connection.close()
    return references


def cleanup_uploads(
    database_path: Path,
    upload_dir: Path,
    *,
    apply: bool = False,
) -> CleanupReport:
    database_path = database_path.expanduser().resolve()
    upload_dir = upload_dir.expanduser().resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f"database not found: {database_path}")
    if not upload_dir.is_dir():
        raise FileNotFoundError(f"upload directory not found: {upload_dir}")

    references = referenced_upload_keys(database_path)
    orphans: list[Path] = []
    for file_path in upload_dir.rglob("*"):
        if not file_path.is_file():
            continue
        if file_path.is_symlink():
            raise RuntimeError(f"refusing symlink inside upload directory: {file_path}")
        resolved = file_path.resolve()
        if not resolved.is_relative_to(upload_dir):
            raise RuntimeError(f"upload path escapes its root: {file_path}")
        key = resolved.relative_to(upload_dir).as_posix()
        if key not in references:
            orphans.append(resolved)

    orphan_bytes = sum(path.stat().st_size for path in orphans)
    if apply:
        for path in orphans:
            path.unlink()
        directories = sorted(
            (path for path in upload_dir.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for directory in directories:
            try:
                directory.rmdir()
            except OSError:
                pass

    return CleanupReport(
        referenced_count=len(references),
        orphan_count=len(orphans),
        orphan_bytes=orphan_bytes,
        removed_count=len(orphans) if apply else 0,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--upload-dir", type=Path, default=DEFAULT_UPLOAD_DIR)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = cleanup_uploads(args.database, args.upload_dir, apply=args.apply)
    action = "removed" if args.apply else "would remove"
    print(
        f"Referenced files: {report.referenced_count}; {action}: "
        f"{report.orphan_count} files ({report.orphan_bytes} bytes)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
