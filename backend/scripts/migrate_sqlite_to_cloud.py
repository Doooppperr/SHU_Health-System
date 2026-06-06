"""Copy the local SQLite data into a cloud SQL database.

Usage examples:
    python scripts/migrate_sqlite_to_cloud.py --target "opengauss+psycopg2://user:pass@host:8000/health_system?client_encoding=utf8"
    python scripts/migrate_sqlite_to_cloud.py --target "mysql+pymysql://user:pass@host:3306/health_system?charset=utf8mb4"
    python scripts/migrate_sqlite_to_cloud.py --target "$env:DATABASE_URL" --replace
    python scripts/migrate_sqlite_to_cloud.py --ssh-host "ecs.example.com" --ssh-user root --ssh-remote-host 192.168.0.31 --ssh-remote-port 8000
"""

from __future__ import annotations

import argparse
import json
import os
import select as select_module
import socket
import sys
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import Boolean, Date, DateTime, Integer, JSON, Numeric, create_engine, delete, func, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.elements import quoted_name


BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = BACKEND_DIR / "instance" / "health_system.db"

sys.path.insert(0, str(BACKEND_DIR))

from app.extensions import db  # noqa: E402
import app.models  # noqa: E402,F401


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate local SQLite data to a cloud database.")
    parser.add_argument(
        "--source",
        default=str(DEFAULT_SOURCE),
        help="Source SQLite file path. Default: backend/instance/health_system.db",
    )
    parser.add_argument(
        "--target",
        default=None,
        help="Target SQLAlchemy database URL. Defaults to TARGET_DATABASE_URL or DATABASE_URL.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete existing rows in the target database before copying.",
    )
    parser.add_argument(
        "--ensure-database",
        action="store_true",
        help="Create the target PostgreSQL/openGauss database first if it does not exist.",
    )
    parser.add_argument(
        "--admin-database",
        default="postgres",
        help="Admin database used with --ensure-database. Default: postgres",
    )
    parser.add_argument("--ssh-host", default=None, help="SSH bastion/ECS host for local port forwarding.")
    parser.add_argument("--ssh-port", type=int, default=22, help="SSH port. Default: 22")
    parser.add_argument("--ssh-user", default=None, help="SSH username.")
    parser.add_argument(
        "--ssh-password-env",
        default="SSH_PASSWORD",
        help="Environment variable containing the SSH password. Default: SSH_PASSWORD",
    )
    parser.add_argument("--ssh-key", default=None, help="Optional SSH private key path.")
    parser.add_argument("--ssh-local-host", default="127.0.0.1", help="Local tunnel host. Default: 127.0.0.1")
    parser.add_argument("--ssh-local-port", type=int, default=15432, help="Local tunnel port. Default: 15432")
    parser.add_argument("--ssh-remote-host", default=None, help="Remote database host reachable from SSH server.")
    parser.add_argument("--ssh-remote-port", type=int, default=None, help="Remote database port reachable from SSH server.")
    return parser.parse_args()


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def masked_url(url: str) -> str:
    return make_url(url).render_as_string(hide_password=True)


def make_engine(url: str) -> Engine:
    return create_engine(url, future=True)


class SshTunnel:
    def __init__(
        self,
        *,
        ssh_host: str,
        ssh_port: int,
        ssh_user: str,
        ssh_password: str | None,
        ssh_key: str | None,
        local_host: str,
        local_port: int,
        remote_host: str,
        remote_port: int,
    ) -> None:
        self.ssh_host = ssh_host
        self.ssh_port = ssh_port
        self.ssh_user = ssh_user
        self.ssh_password = ssh_password
        self.ssh_key = ssh_key
        self.local_host = local_host
        self.local_port = local_port
        self.remote_host = remote_host
        self.remote_port = remote_port
        self._stop_event = threading.Event()
        self._server: socket.socket | None = None
        self._client = None
        self._thread: threading.Thread | None = None

    def __enter__(self):
        try:
            import paramiko
        except ImportError as exc:
            raise SystemExit("paramiko is required for --ssh-host. Install requirements.txt first.") from exc

        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: dict[str, Any] = {
            "hostname": self.ssh_host,
            "port": self.ssh_port,
            "username": self.ssh_user,
            "timeout": 20,
        }
        if self.ssh_key:
            connect_kwargs["key_filename"] = self.ssh_key
        else:
            connect_kwargs["password"] = self.ssh_password

        self._client.connect(**connect_kwargs)

        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((self.local_host, self.local_port))
        self._server.listen(50)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                with socket.create_connection((self.local_host, self.local_port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise SystemExit("SSH tunnel did not start.")

        print(
            f"SSH tunnel: {self.local_host}:{self.local_port} -> "
            f"{self.remote_host}:{self.remote_port} via {self.ssh_user}@{self.ssh_host}"
        )
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self._stop_event.set()
        if self._server is not None:
            self._server.close()
        if self._client is not None:
            self._client.close()

    def _serve(self) -> None:
        assert self._server is not None

        while not self._stop_event.is_set():
            try:
                client_socket, client_addr = self._server.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(client_socket, client_addr), daemon=True).start()

    def _handle(self, client_socket: socket.socket, client_addr) -> None:
        assert self._client is not None
        transport = self._client.get_transport()
        if transport is None:
            client_socket.close()
            return

        try:
            channel = transport.open_channel(
                "direct-tcpip",
                (self.remote_host, self.remote_port),
                client_addr,
            )
        except Exception:
            client_socket.close()
            return

        if channel is None:
            client_socket.close()
            return

        try:
            while not self._stop_event.is_set():
                readable, _, _ = select_module.select([client_socket, channel], [], [], 1)
                if client_socket in readable:
                    data = client_socket.recv(16384)
                    if not data:
                        break
                    channel.sendall(data)
                if channel in readable:
                    data = channel.recv(16384)
                    if not data:
                        break
                    client_socket.sendall(data)
        finally:
            channel.close()
            client_socket.close()


def table_count(engine: Engine, table) -> int:
    with engine.connect() as conn:
        return conn.scalar(select(func.count()).select_from(table)) or 0


def has_existing_data(engine: Engine) -> bool:
    return any(table_count(engine, table) > 0 for table in db.metadata.sorted_tables)


def ensure_database_exists(target_url: str, admin_database: str) -> None:
    url = make_url(target_url)
    if url.drivername.split("+", 1)[0] not in {"postgresql", "opengauss"}:
        return

    database_name = url.database
    if not database_name:
        raise SystemExit("Target database URL does not include a database name.")

    admin_url = url.set(database=admin_database)
    engine = make_engine(admin_url.render_as_string(hide_password=False))

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        exists = conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :database_name"), {"database_name": database_name})
        if exists:
            return

        quoted_database = conn.dialect.identifier_preparer.quote(quoted_name(database_name, quote=True))
        conn.execute(text(f"CREATE DATABASE {quoted_database} ENCODING 'UTF8'"))


def coerce_value(column, value: Any) -> Any:
    if value is None:
        return None

    column_type = column.type

    if isinstance(column_type, Boolean):
        return bool(value)

    if isinstance(column_type, JSON) and isinstance(value, str):
        return json.loads(value)

    if isinstance(column_type, DateTime) and isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=None)

    if isinstance(column_type, Date) and not isinstance(column_type, DateTime) and isinstance(value, str):
        return date.fromisoformat(value)

    if isinstance(column_type, Numeric) and not isinstance(value, Decimal):
        return Decimal(str(value))

    return value


def row_to_insert_payload(row, table) -> dict[str, Any]:
    raw = dict(row._mapping)
    return {column.name: coerce_value(column, raw[column.name]) for column in table.columns}


def ensure_source_exists(source_path: Path) -> None:
    if not source_path.exists():
        raise SystemExit(f"Source SQLite database not found: {source_path}")


def ensure_target_is_not_source(source_url: str, target_url: str) -> None:
    if make_url(source_url) == make_url(target_url):
        raise SystemExit("Target database URL points to the same SQLite file as the source.")


def clear_target(engine: Engine) -> None:
    with engine.begin() as conn:
        for table in reversed(db.metadata.sorted_tables):
            conn.execute(delete(table))


def copy_data(source_engine: Engine, target_engine: Engine) -> list[tuple[str, int]]:
    copied: list[tuple[str, int]] = []

    with source_engine.connect() as source_conn, target_engine.begin() as target_conn:
        for table in db.metadata.sorted_tables:
            rows = [row_to_insert_payload(row, table) for row in source_conn.execute(select(table))]
            if rows:
                target_conn.execute(table.insert(), rows)
            copied.append((table.name, len(rows)))

    return copied


def reset_integer_pk_sequences(engine: Engine) -> None:
    if engine.dialect.name not in {"postgresql", "opengauss"}:
        return

    preparer = engine.dialect.identifier_preparer

    with engine.begin() as conn:
        for table in db.metadata.sorted_tables:
            primary_keys = list(table.primary_key.columns)
            if len(primary_keys) != 1 or not isinstance(primary_keys[0].type, Integer):
                continue

            pk_column = primary_keys[0]
            table_name = table.name
            qualified_table = preparer.format_table(table)
            quoted_pk = preparer.quote(pk_column.name)
            max_id = conn.scalar(text(f"SELECT MAX({quoted_pk}) FROM {qualified_table}")) or 0
            next_value = max(int(max_id), 1)
            conn.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence(:table_name, :column_name), "
                    ":next_value, :has_rows)"
                ),
                {
                    "table_name": table_name,
                    "column_name": pk_column.name,
                    "next_value": next_value,
                    "has_rows": max_id > 0,
                },
            )


def validate_ssh_args(args: argparse.Namespace) -> None:
    if not args.ssh_host:
        return

    missing = [
        name
        for name in ("ssh_user", "ssh_remote_host", "ssh_remote_port")
        if getattr(args, name) in {None, ""}
    ]
    if missing:
        raise SystemExit(f"Missing SSH tunnel arguments: {', '.join(missing)}")

    if not args.ssh_key and not os.getenv(args.ssh_password_env):
        raise SystemExit(f"Missing SSH password env var: {args.ssh_password_env}")


def open_optional_tunnel(args: argparse.Namespace):
    if not args.ssh_host:
        return None

    return SshTunnel(
        ssh_host=args.ssh_host,
        ssh_port=args.ssh_port,
        ssh_user=args.ssh_user,
        ssh_password=os.getenv(args.ssh_password_env),
        ssh_key=args.ssh_key,
        local_host=args.ssh_local_host,
        local_port=args.ssh_local_port,
        remote_host=args.ssh_remote_host,
        remote_port=args.ssh_remote_port,
    )


def run_migration(args: argparse.Namespace, source_path: Path, target_url: str) -> list[tuple[str, int]]:
    source_url = sqlite_url(source_path)
    ensure_target_is_not_source(source_url, target_url)

    if args.ensure_database:
        ensure_database_exists(target_url, args.admin_database)

    source_engine = make_engine(source_url)
    target_engine = make_engine(target_url)

    db.metadata.create_all(target_engine)

    if has_existing_data(target_engine):
        if not args.replace:
            raise SystemExit("Target database already has data. Re-run with --replace after backing it up.")
        clear_target(target_engine)

    copied = copy_data(source_engine, target_engine)
    reset_integer_pk_sequences(target_engine)
    return copied


def main() -> int:
    load_dotenv(BACKEND_DIR / ".env")
    args = parse_args()
    validate_ssh_args(args)

    source_path = Path(args.source)
    if not source_path.is_absolute():
        source_path = BACKEND_DIR / source_path

    target_url = args.target or os.getenv("TARGET_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not target_url:
        raise SystemExit("Missing target database URL. Pass --target or set TARGET_DATABASE_URL/DATABASE_URL.")

    ensure_source_exists(source_path)

    try:
        tunnel = open_optional_tunnel(args)
        if tunnel is None:
            copied = run_migration(args, source_path, target_url)
        else:
            with tunnel:
                copied = run_migration(args, source_path, target_url)
    except SQLAlchemyError as exc:
        raise SystemExit(f"Migration failed: {exc}") from exc

    print(f"Source: {source_path}")
    print(f"Target: {masked_url(target_url)}")
    print("Copied rows:")
    for table_name, row_count in copied:
        print(f"  {table_name}: {row_count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
