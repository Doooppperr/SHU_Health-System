"""Upgrade the local SQLite database to HealthDoc schema v13.

The v6-to-v7 path preserves all current business data while adding health
domains, package versions, booking groups, waitlists and private assets. Older
supported snapshots are copied by common
columns into the current schema; much older unsupported schemas
retain only the current system administrator identity before rebuilding.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = BACKEND_DIR / "instance" / "health_system.db"
SCHEMA_VERSION = 13
ADDITIVE_V12_SECURITY_COLUMNS = {
    "password_verification_challenges.token_version_snapshot": (
        "password_verification_challenges",
        "token_version_snapshot",
    ),
    "oauth_clients.approval_version": ("oauth_clients", "approval_version"),
    "oauth_authorization_codes.user_token_version_snapshot": (
        "oauth_authorization_codes",
        "user_token_version_snapshot",
    ),
    "oauth_authorization_codes.client_approval_version_snapshot": (
        "oauth_authorization_codes",
        "client_approval_version_snapshot",
    ),
    "oauth_access_tokens.user_token_version_snapshot": (
        "oauth_access_tokens",
        "user_token_version_snapshot",
    ),
    "oauth_access_tokens.client_approval_version_snapshot": (
        "oauth_access_tokens",
        "client_approval_version_snapshot",
    ),
    "oauth_refresh_tokens.user_token_version_snapshot": (
        "oauth_refresh_tokens",
        "user_token_version_snapshot",
    ),
    "oauth_refresh_tokens.client_approval_version_snapshot": (
        "oauth_refresh_tokens",
        "client_approval_version_snapshot",
    ),
}
sys.path.insert(0, str(BACKEND_DIR))

from app import models as _models  # noqa: E402,F401
from app.demo_indicator_values import DEMO_REALISTIC_SERIES, demo_realistic_status  # noqa: E402
from app.config import DevelopmentConfig  # noqa: E402
from app.extensions import db, init_extensions  # noqa: E402


@dataclass(frozen=True)
class SchemaReport:
    version: int
    missing_tables: tuple[str, ...]
    missing_columns: tuple[str, ...]
    missing_constraints: tuple[str, ...]

    @property
    def is_current(self):
        return self.version == SCHEMA_VERSION and not self.missing_tables and not self.missing_columns and not self.missing_constraints


def parse_args():
    parser = argparse.ArgumentParser(description="Upgrade the local SQLite database to schema v13.")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument(
        "--allow-data-loss",
        action="store_true",
        help=(
            "Explicitly allow the unsupported legacy fallback that retains "
            "only one administrator account"
        ),
    )
    return parser.parse_args()


def table_names(connection):
    return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def inspect_schema(connection):
    tables = table_names(connection)
    expected = set(db.metadata.tables)
    missing_columns = []
    physical_issues = []

    def sqlite_affinity(declared_type):
        value = str(declared_type or "").upper()
        if "INT" in value:
            return "integer"
        if any(token in value for token in ("CHAR", "CLOB", "TEXT")):
            return "text"
        if not value or "BLOB" in value:
            return "blob"
        if any(token in value for token in ("REAL", "FLOA", "DOUB")):
            return "real"
        return "numeric"

    for name in sorted(expected & tables):
        table = db.metadata.tables[name]
        info_rows = connection.execute(
            f'PRAGMA table_info("{name}")'
        ).fetchall()
        actual_info = {row[1]: row for row in info_rows}
        actual = set(actual_info)
        missing_columns.extend(f"{name}.{column.name}" for column in db.metadata.tables[name].columns if column.name not in actual)
        for column in table.columns:
            row = actual_info.get(column.name)
            if row is None:
                continue
            if not column.primary_key and bool(row[3]) != (not column.nullable):
                physical_issues.append(
                    f"{name}.{column.name}.nullability"
                )
            if sqlite_affinity(row[2]) != sqlite_affinity(column.type):
                physical_issues.append(f"{name}.{column.name}.type")

        unique_sets = set()
        for index_row in connection.execute(f'PRAGMA index_list("{name}")'):
            if not index_row[2]:
                continue
            unique_sets.add(tuple(
                row[2] for row in connection.execute(
                    f'PRAGMA index_info("{index_row[1]}")'
                )
            ))
        for constraint in table.constraints:
            if constraint.__visit_name__ != "unique_constraint":
                continue
            columns = tuple(column.name for column in constraint.columns)
            if columns not in unique_sets:
                physical_issues.append(
                    f"{name}.unique({','.join(columns)})"
                )

        foreign_key_rows = connection.execute(
            f'PRAGMA foreign_key_list("{name}")'
        ).fetchall()
        foreign_key_groups = {}
        for row in foreign_key_rows:
            group = foreign_key_groups.setdefault(row[0], {
                "table": row[2],
                "from": [],
                "to": [],
            })
            group["from"].append((row[1], row[3]))
            group["to"].append((row[1], row[4]))
        actual_foreign_keys = {
            (
                tuple(value for _order, value in sorted(group["from"])),
                group["table"],
                tuple(value for _order, value in sorted(group["to"])),
            )
            for group in foreign_key_groups.values()
        }
        for constraint in table.constraints:
            if constraint.__visit_name__ != "foreign_key_constraint":
                continue
            expected_fk = (
                tuple(column.name for column in constraint.columns),
                next(iter(constraint.elements)).column.table.name,
                tuple(element.column.name for element in constraint.elements),
            )
            if expected_fk not in actual_foreign_keys:
                physical_issues.append(
                    f"{name}.foreign_key({','.join(expected_fk[0])})"
                )
    ddl = "\n".join((row[0] or "") for row in connection.execute("SELECT sql FROM sqlite_master WHERE type IN ('table','index') AND sql IS NOT NULL"))
    named = {
        item.name
        for table in db.metadata.tables.values()
        for item in (*table.constraints, *table.indexes)
        if item.name
    }
    incompatible_constraints = list(physical_issues)
    if "users" in tables:
        for index_row in connection.execute('PRAGMA index_list("users")'):
            if not index_row[2]:
                continue
            columns = tuple(
                row[2] for row in connection.execute(
                    f'PRAGMA index_info("{index_row[1]}")'
                )
            )
            if columns == ("email",):
                incompatible_constraints.append("users.email_unique_must_be_removed")
    return SchemaReport(
        int(connection.execute("PRAGMA user_version").fetchone()[0]),
        tuple(sorted(expected - tables)), tuple(missing_columns),
        tuple(sorted([*(name for name in named if name not in ddl), *incompatible_constraints])),
    )


def validate(connection):
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise RuntimeError("SQLite integrity_check failed")
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"SQLite foreign_key_check found {len(violations)} violation(s)")
    report = inspect_schema(connection)
    if not report.is_current:
        raise RuntimeError(f"schema v13 validation failed: {report}")


def repair_result_statuses(connection):
    """Remove false normal labels from measurements without a usable rule."""
    if not {"report_indicators", "indicator_dicts", "indicator_reference_rules"} <= table_names(connection):
        return
    connection.execute(
        """
        UPDATE report_indicators
        SET result_status='unknown', is_abnormal=0
        WHERE indicator_dict_id IN (
            SELECT id FROM indicator_dicts WHERE code IN ('HEIGHT','WEIGHT','HIP')
        )
        """
    )
    rows = connection.execute(
        """
        SELECT report_indicator.id, report_indicator.report_id, indicator.code
        FROM report_indicators AS report_indicator
        JOIN indicator_dicts AS indicator
          ON indicator.id=report_indicator.indicator_dict_id
        WHERE report_indicator.method_snapshot='v10 合成体检演示'
        """
    ).fetchall()
    for indicator_id, report_id, code in rows:
        values = DEMO_REALISTIC_SERIES.get(code)
        if not values:
            continue
        value = values[int(report_id) % len(values)]
        status = demo_realistic_status(code, value)
        abnormal_flag = {"high": "H", "low": "L"}.get(status)
        connection.execute(
            """
            UPDATE report_indicators
            SET value=?, original_value=?, result_status=?,
                is_abnormal=?, abnormal_flag=?
            WHERE id=?
            """,
            (
                value,
                value,
                status,
                int(status in {"high", "low"}),
                abnormal_flag,
                indicator_id,
            ),
        )
    return connection.total_changes


def backfill_v13_finance(database_path: Path) -> int:
    """Backfill historical fulfilled visits inside the isolated candidate."""
    app = Flask("healthdoc-schema-v13-upgrade")
    app.config.from_object(DevelopmentConfig)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path.as_posix()}",
        SQLALCHEMY_ENGINE_OPTIONS={},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    init_extensions(app)
    with app.app_context():
        from app.services.finance import backfill_historical_settlements

        created = backfill_historical_settlements()
        db.session.commit()
        db.session.remove()
        db.engine.dispose()
        return created


def read_admin(connection):
    if "users" not in table_names(connection):
        return None
    columns = {row[1] for row in connection.execute('PRAGMA table_info("users")')}
    required = {"id", "username", "password_hash"}
    if not required <= columns:
        return None
    optional = [name for name in ("email", "phone", "created_at", "is_active") if name in columns]
    selected = ["id", "username", "password_hash", *optional]
    role_clause = "role = 'admin'" if "role" in columns else "username = 'admin'"
    row = connection.execute(f"SELECT {', '.join(selected)} FROM users WHERE {role_clause} ORDER BY CASE WHEN username='admin' THEN 0 ELSE 1 END, id LIMIT 1").fetchone()
    return dict(zip(selected, row)) if row else None


def backup_path(database):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return database.with_name(f"{database.stem}.before-schema-v13-{stamp}-{uuid.uuid4().hex[:6]}.db")


def prepare_v8_source(database_path):
    """Add deterministic organization rows to a temporary legacy snapshot."""
    prepared = database_path.with_name(f".{database_path.stem}.v8-source-{uuid.uuid4().hex}.db")
    shutil.copy2(database_path, prepared)
    connection = sqlite3.connect(prepared)
    try:
        tables = table_names(connection)
        if "institutions" in tables and "organizations" not in tables:
            connection.execute("CREATE TABLE organizations (id INTEGER PRIMARY KEY, name VARCHAR(120) NOT NULL UNIQUE, description TEXT, service_features JSON NOT NULL DEFAULT '[]', is_active BOOLEAN NOT NULL DEFAULT 1, created_at DATETIME NOT NULL)")
            columns = {row[1] for row in connection.execute('PRAGMA table_info("institutions")')}
            if "organization_id" not in columns:
                connection.execute("ALTER TABLE institutions ADD COLUMN organization_id INTEGER")
            rows = connection.execute("SELECT name, MIN(id) FROM institutions GROUP BY name ORDER BY MIN(id)").fetchall()
            for index, (name, _first_id) in enumerate(rows, start=1):
                connection.execute("INSERT INTO organizations (id,name,description,service_features,is_active,created_at) VALUES (?,?,?,?,1,?)", (index, name, f"{name}旗下体检服务机构。", "[]", datetime.now().isoformat()))
                connection.execute("UPDATE institutions SET organization_id=? WHERE name=?", (index, name))
        connection.commit()
    except Exception:
        connection.close(); prepared.unlink(missing_ok=True); raise
    finally:
        connection.close()
    return prepared


def rebuild_database(database_path, *, allow_data_loss=False):
    database_path = database_path.resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {database_path}")
    with closing(sqlite3.connect(database_path)) as source:
        if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("source SQLite integrity_check failed")
        report = inspect_schema(source)
        if report.is_current:
            backup = backup_path(database_path)
            shutil.copy2(database_path, backup)
            before_changes = source.total_changes
            try:
                repair_result_statuses(source)
                changed = source.total_changes > before_changes
                validate(source)
                source.commit()
            except Exception:
                source.rollback()
                backup.unlink(missing_ok=True)
                raise
            if not changed:
                backup.unlink(missing_ok=True)
                return None
            return backup
        additive_v12_security_columns = (
            report.version == SCHEMA_VERSION
            and not report.missing_tables
            and bool(report.missing_columns)
            and set(report.missing_columns).issubset(
                ADDITIVE_V12_SECURITY_COLUMNS
            )
            and not report.missing_constraints
        )
        if additive_v12_security_columns:
            backup = backup_path(database_path)
            temporary = database_path.with_name(
                f".{database_path.stem}.v12-security-{uuid.uuid4().hex}.db"
            )
            target = None
            try:
                # Apply non-transactional SQLite DDL only to an isolated copy.
                # The live file is replaced after the copy passes the complete
                # schema, integrity and foreign-key contract.
                with closing(sqlite3.connect(backup)) as recovery:
                    source.backup(recovery)
                with closing(sqlite3.connect(temporary)) as candidate:
                    source.backup(candidate)
                source.close()
                target = sqlite3.connect(temporary)
                missing = set(report.missing_columns)
                for qualified_name in sorted(missing):
                    table_name, column_name = (
                        ADDITIVE_V12_SECURITY_COLUMNS[qualified_name]
                    )
                    target.execute(
                        f'ALTER TABLE "{table_name}" ADD COLUMN '
                        f'"{column_name}" INTEGER NOT NULL DEFAULT 0'
                    )
                if (
                    "password_verification_challenges."
                    "token_version_snapshot" in missing
                ):
                    target.execute(
                        "UPDATE password_verification_challenges "
                        "SET token_version_snapshot=COALESCE(("
                        "SELECT users.token_version FROM users "
                        "WHERE users.id=password_verification_challenges.user_id"
                        "), 0), consumed_at=COALESCE("
                        "consumed_at, CURRENT_TIMESTAMP)"
                    )
                if any(name.startswith("oauth_") for name in missing):
                    target.execute(
                        "UPDATE oauth_authorization_codes SET consumed_at="
                        "COALESCE(consumed_at, CURRENT_TIMESTAMP)"
                    )
                    target.execute(
                        "UPDATE oauth_access_tokens SET revoked_at="
                        "COALESCE(revoked_at, CURRENT_TIMESTAMP)"
                    )
                    target.execute(
                        "UPDATE oauth_refresh_tokens SET revoked_at="
                        "COALESCE(revoked_at, CURRENT_TIMESTAMP)"
                    )
                validate(target)
                target.commit()
                target.close()
                os.replace(temporary, database_path)
            except Exception:
                if target is not None:
                    target.close()
                temporary.unlink(missing_ok=True)
                raise
            return backup
        admin = read_admin(source)
        available_tables = table_names(source)

    if report.version in {4, 5, 6, 7, 8, 9, 10, 11, 12}:
        from scripts.migrate_sqlite_to_gaussdb import migrate

        temporary = database_path.with_name(f".{database_path.stem}.v13-{uuid.uuid4().hex}.db")
        prepared = prepare_v8_source(database_path)
        backup = backup_path(database_path)
        shutil.copy2(database_path, backup)
        try:
            migrate(
                prepared,
                f"sqlite:///{temporary.as_posix()}",
                replace=True,
                allow_legacy_source=True,
            )
            backfill_v13_finance(temporary)
            with closing(sqlite3.connect(temporary)) as target:
                target.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                repair_result_statuses(target)
                target.commit()
                validate(target)
            os.replace(temporary, database_path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            prepared.unlink(missing_ok=True)
        return backup

    if available_tables and not allow_data_loss:
        raise RuntimeError(
            "unsupported or incomplete SQLite schema; refusing the "
            "administrator-only rebuild without --allow-data-loss"
        )

    temporary = database_path.with_name(f".{database_path.stem}.v13-{uuid.uuid4().hex}.db")
    backup = backup_path(database_path)
    engine = create_engine(f"sqlite:///{temporary.as_posix()}")
    try:
        db.metadata.create_all(engine)
    finally:
        engine.dispose()
    target = sqlite3.connect(temporary)
    try:
        target.execute("PRAGMA foreign_keys=ON")
        if admin:
            target.execute(
                "INSERT INTO users (id, username, password_hash, email, phone, role, managed_institution_id, health_id, is_active, created_at) VALUES (?, ?, ?, ?, ?, 'admin', NULL, NULL, ?, ?)",
                (admin["id"], admin["username"], admin["password_hash"], admin.get("email"), admin.get("phone"), admin.get("is_active", 1), admin.get("created_at") or datetime.now().isoformat()),
            )
        target.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        target.commit()
        validate(target)
    except Exception:
        target.close(); temporary.unlink(missing_ok=True); raise
    finally:
        if temporary.exists():
            try: target.close()
            except Exception: pass
    shutil.copy2(database_path, backup)
    try:
        os.replace(temporary, database_path)
    except Exception:
        temporary.unlink(missing_ok=True); backup.unlink(missing_ok=True); raise
    return backup


def print_report(database, report):
    print(f"database={database}")
    print(f"user_version={report.version}")
    print(f"expected_user_version={SCHEMA_VERSION}")
    print(f"schema_current={'yes' if report.is_current else 'no'}")
    for label, values in (("table", report.missing_tables), ("column", report.missing_columns), ("constraint", report.missing_constraints)):
        print(f"missing_{label}s={len(values)}")
        for value in values: print(f"{label}:{value}")


def main():
    args = parse_args(); database = args.database.resolve()
    if args.check_only:
        with closing(sqlite3.connect(database)) as connection: print_report(database, inspect_schema(connection))
        return
    backup = rebuild_database(database, allow_data_loss=args.allow_data_loss)
    print(f"database={database}")
    print("schema_upgrade=already-current" if backup is None else f"backup={backup}\nschema_upgrade=ok")


if __name__ == "__main__":
    main()
