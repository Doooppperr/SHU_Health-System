#!/usr/bin/env bash
set -euo pipefail

archive=${1:-}
release_id=${2:-}
demo_database=${3:-}
demo_assets=${4:-}
mail_settings=${5:-}
expected_commit=${6:-}
public_app_url=${7:-}

if [[ ! "$archive" =~ ^/home/[^/]+/healthdoc-app-[0-9]{8}T[0-9]{6}Z\.tar\.gz$ ]]; then
    echo "Refusing unexpected archive path: $archive" >&2
    exit 2
fi
if [[ ! "$release_id" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]; then
    echo "Invalid release id: $release_id" >&2
    exit 2
fi
if [[ ! -f "$archive" ]]; then
    echo "Release archive not found: $archive" >&2
    exit 2
fi
if [[ ! "$expected_commit" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Invalid release commit: $expected_commit" >&2
    exit 2
fi
if [[ ! "$public_app_url" =~ ^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$ ]]; then
    echo "Invalid public application URL: $public_app_url" >&2
    exit 2
fi
if [[ -n "$demo_database" ]]; then
    if [[ ! "$demo_database" =~ ^/home/[^/]+/healthdoc-demo-[0-9]{8}T[0-9]{6}Z\.db$ ]]; then
        echo "Refusing unexpected demo database path: $demo_database" >&2
        exit 2
    fi
    if [[ ! -f "$demo_database" ]]; then
        echo "Demo database snapshot not found: $demo_database" >&2
        exit 2
    fi
fi
if [[ -n "$demo_assets" ]]; then
    if [[ ! "$demo_assets" =~ ^/home/[^/]+/healthdoc-demo-assets-[0-9]{8}T[0-9]{6}Z\.tar\.gz$ || ! -f "$demo_assets" ]]; then
        echo "Refusing unexpected demo asset archive: $demo_assets" >&2
        exit 2
    fi
fi
if [[ -n "$mail_settings" ]]; then
    if [[ ! "$mail_settings" =~ ^/home/[^/]+/healthdoc-mail-[0-9]{8}T[0-9]{6}Z\.env$ || ! -f "$mail_settings" ]]; then
        echo "Refusing unexpected mail settings file: $mail_settings" >&2
        exit 2
    fi
fi
if [[ -n "$demo_database" && -z "$demo_assets" ]]; then
    echo "Database sync requires the matching report media archive." >&2
    exit 2
fi

release="/opt/healthdoc/releases/$release_id"
previous=$(readlink -f /opt/healthdoc/current 2>/dev/null || true)
env_file=/etc/healthdoc/healthdoc.env
apache_config=/etc/apache2/sites-available/healthdoc.conf
rag_root=/var/lib/healthdoc/rag
env_backup=$(mktemp /tmp/healthdoc-env.XXXXXX)
apache_backup=$(mktemp /tmp/healthdoc-apache.XXXXXX)
apache_candidate="/etc/apache2/sites-available/healthdoc.conf.new.$release_id"
apache_config_prepared=0
apache_config_committed=0
deployment_started=0
release_activated=0
recovery_complete=0
release_committed=0
expected_asset_count=0
release_created=0
systemd_backup_complete=0
database_container_stopped=0
database_backup=""
backup_root=""
release_python="$release/venv/bin/python"
notification_gate=/var/lib/healthdoc/notification-worker.enabled
systemd_units=(
    healthdoc.service
    healthdoc-notifications.service
    healthdoc-mcp.service
    healthdoc-agent-cleanup.service
    healthdoc-agent-cleanup.timer
)

exec 9>/run/lock/healthdoc-release.lock
if ! flock -n 9; then
    echo "Another HealthDoc release is already running." >&2
    exit 2
fi

cleanup() {
    local status=$?
    local cleanup_failed=0
    trap - EXIT
    set +e

    if [[ "$status" != 0 && "$deployment_started" == 1 \
        && "$recovery_complete" == 0 && "$release_committed" == 0 ]]; then
        echo "Release failure detected; restoring the complete previous server state." >&2
        if ! declare -F rollback_release >/dev/null || ! rollback_release; then
            cleanup_failed=1
        fi
    elif [[ "$status" != 0 && "$release_committed" == 1 ]]; then
        echo "Release failed after the public commit point; preserving the new database and release to avoid discarding acknowledged writes." >&2
    elif [[ "$database_container_stopped" == 1 ]]; then
        if ! declare -F ensure_database_container_running >/dev/null \
            || ! ensure_database_container_running; then
            cleanup_failed=1
        fi
    fi
    if [[ "$status" != 0 && "$apache_config_prepared" == 1 && "$apache_config_committed" == 0 ]]; then
        cp -p "$apache_backup" "$apache_config" || cleanup_failed=1
    fi
    if [[ "$status" != 0 && "$release_created" == 1 && -d "$release" \
        && "$release" =~ ^/opt/healthdoc/releases/[0-9]{8}T[0-9]{6}Z$ \
        && "$(readlink -f /opt/healthdoc/current 2>/dev/null || true)" != "$release" ]]; then
        rm -rf -- "$release" || cleanup_failed=1
    fi
    rm -f "$env_backup" "$apache_backup" "$apache_candidate" \
        "$mail_settings" "$demo_assets" "$demo_database" \
        || cleanup_failed=1
    if [[ -n "$backup_root" ]]; then
        rm -f "$backup_root/opengauss.tar.gz.partial" \
            "$backup_root/uploads.tar.gz.partial" \
            "$backup_root/current-release.tar.gz.partial" || cleanup_failed=1
    fi
    if [[ "$cleanup_failed" == 1 ]]; then
        echo "WARNING: automatic recovery did not complete; keep writes stopped and inspect the cold backup before manual action." >&2
        status=1
    fi
    exit "$status"
}
trap cleanup EXIT

if [[ ! -f "$env_file" ]]; then
    echo "Production environment file is missing: $env_file" >&2
    exit 2
fi
if [[ ! -f "$apache_config" ]]; then
    echo "Production Apache configuration is missing: $apache_config" >&2
    exit 2
fi
cp -p "$env_file" "$env_backup"
cp -p "$apache_config" "$apache_backup"

upsert_env() {
    local key=$1
    local value=$2
    if grep -q "^${key}=" "$env_file"; then
        sed -i "s|^${key}=.*$|${key}=${value}|" "$env_file"
    else
        printf '%s=%s\n' "$key" "$value" >>"$env_file"
    fi
}

apply_mail_settings() {
    "$release_python" - "$env_file" "$mail_settings" <<'PY'
import json
import os
import re
import shlex
import stat
import sys
import tempfile
from pathlib import Path


allowed = {
    "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD",
    "SMTP_FROM", "SMTP_USE_TLS", "NOTIFICATION_EMAIL_DRY_RUN",
    "NOTIFICATION_EMAIL_REDIRECT",
}
env_path = Path(sys.argv[1])
payload_path = Path(sys.argv[2])
payload = json.loads(payload_path.read_text(encoding="utf-8"))
if not isinstance(payload, dict) or not set(payload).issubset(allowed):
    raise RuntimeError("mail settings contain an unexpected key")
values = {}
for key, value in payload.items():
    if not isinstance(value, str) or any(char in value for char in ("\0", "\r", "\n")):
        raise RuntimeError(f"mail setting {key} is not a safe scalar value")
    values[key] = value

source = env_path.read_text(encoding="utf-8").splitlines()
rendered = []
written = set()
assignment = re.compile(r"^([A-Z0-9_]+)=")
for line in source:
    match = assignment.match(line)
    key = match.group(1) if match else None
    if key not in values:
        rendered.append(line)
        continue
    if key not in written:
        rendered.append(f"{key}={shlex.quote(values[key])}")
        written.add(key)
for key in sorted(values):
    if key not in written:
        rendered.append(f"{key}={shlex.quote(values[key])}")

metadata = env_path.stat()
descriptor, temporary = tempfile.mkstemp(
    prefix=f".{env_path.name}.",
    dir=env_path.parent,
    text=True,
)
try:
    os.fchmod(descriptor, stat.S_IMODE(metadata.st_mode))
    os.fchown(descriptor, metadata.st_uid, metadata.st_gid)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
        output.write("\n".join(rendered) + "\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, env_path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

restore_uploads_backup() {
    if [[ -z "${backup_root:-}" ]]; then
        return 0
    fi
    if [[ -f "$backup_root/uploads.was-absent" ]]; then
        rm -rf /var/lib/healthdoc/uploads
        return 0
    fi
    if [[ ! -f "$backup_root/uploads.tar.gz" ]]; then
        return 0
    fi
    if ! tar -tzf "$backup_root/uploads.tar.gz" >/dev/null; then
        echo "Cold uploads backup is unreadable; refusing destructive restore." >&2
        return 1
    fi
    rm -rf /var/lib/healthdoc/uploads || return 1
    tar -C /var/lib/healthdoc -xzf "$backup_root/uploads.tar.gz" || return 1
    chown -R healthdoc:www-data /var/lib/healthdoc/uploads || return 1
}

stop_application_services() {
    local unit
    local failed=0
    local load_state
    local active_state
    local stop_order=(
        healthdoc-agent-cleanup.timer
        healthdoc-agent-cleanup.service
        healthdoc-notifications.service
        healthdoc-mcp.service
        healthdoc.service
    )

    for unit in "${stop_order[@]}"; do
        if ! load_state=$(systemctl show -p LoadState --value "$unit" 2>/dev/null); then
            echo "Unable to query write-capable unit: $unit" >&2
            failed=1
            continue
        fi
        if [[ "$load_state" == "not-found" ]]; then
            if [[ "$unit" == "healthdoc.service" ]]; then
                echo "Required application unit is missing: $unit" >&2
                failed=1
            fi
            continue
        fi
        systemctl stop "$unit" 2>/dev/null || failed=1
    done
    for unit in "${stop_order[@]}"; do
        load_state=$(systemctl show -p LoadState --value "$unit" 2>/dev/null || true)
        [[ "$load_state" == "not-found" ]] && continue
        active_state=$(systemctl is-active "$unit" 2>/dev/null || true)
        case "$active_state" in
            inactive|failed)
                ;;
            *)
                echo "Unable to prove write-capable unit inactive: $unit ($active_state)" >&2
                failed=1
                ;;
        esac
    done
    if [[ "$failed" == 0 ]]; then
        rm -f "$notification_gate" || failed=1
    fi
    return "$failed"
}

stop_public_entrypoint() {
    local load_state
    local active_state

    if ! load_state=$(systemctl show -p LoadState --value apache2 2>/dev/null) \
        || [[ "$load_state" == "not-found" ]]; then
        echo "Unable to query the required Apache entrypoint." >&2
        return 1
    fi
    systemctl stop apache2 || return 1
    active_state=$(systemctl is-active apache2 2>/dev/null || true)
    case "$active_state" in
        inactive|failed)
            return 0
            ;;
        *)
            echo "Unable to prove Apache inactive: $active_state" >&2
            return 1
            ;;
    esac
}

quiesce_external_writes() {
    local failed=0

    stop_public_entrypoint || failed=1
    stop_application_services || failed=1
    return "$failed"
}

backup_systemd_units() {
    local backup_dir="$backup_root/systemd-units"
    local unit
    local unit_path
    local state
    local gate_state

    install -d -o root -g root -m 700 "$backup_dir"
    for unit in "${systemd_units[@]}"; do
        unit_path="/etc/systemd/system/$unit"
        if [[ -e "$unit_path" || -L "$unit_path" ]]; then
            cp -a -- "$unit_path" "$backup_dir/$unit"
        else
            install -m 600 /dev/null "$backup_dir/$unit.was-absent"
        fi
        state=$(systemctl is-enabled "$unit" 2>/dev/null || true)
        printf '%s\n' "${state:-not-found}" >"$backup_dir/$unit.enabled-state"
        chmod 600 "$backup_dir/$unit.enabled-state"
        state=$(systemctl is-active "$unit" 2>/dev/null || true)
        printf '%s\n' "${state:-unknown}" >"$backup_dir/$unit.active-state"
        chmod 600 "$backup_dir/$unit.active-state"
    done
    if [[ -e "$notification_gate" || -L "$notification_gate" ]]; then
        if [[ ! -f "$notification_gate" || -L "$notification_gate" ]]; then
            echo "Notification gate must be a regular file: $notification_gate" >&2
            return 1
        fi
        gate_state=present
    else
        gate_state=absent
    fi
    printf '%s\n' "$gate_state" >"$backup_dir/notification-gate.state"
    chmod 600 "$backup_dir/notification-gate.state"
    systemd_backup_complete=1
}

restore_systemd_units() {
    local backup_dir="$backup_root/systemd-units"
    local unit
    local unit_path
    local state
    local failed=0

    if [[ "$systemd_backup_complete" != 1 || ! -d "$backup_dir" ]]; then
        return 0
    fi
    for unit in "${systemd_units[@]}"; do
        systemctl unmask "$unit" >/dev/null 2>&1 || true
        systemctl unmask --runtime "$unit" >/dev/null 2>&1 || true
        systemctl disable --now "$unit" >/dev/null 2>&1 || true
        systemctl disable --runtime --now "$unit" >/dev/null 2>&1 || true
    done
    for unit in "${systemd_units[@]}"; do
        unit_path="/etc/systemd/system/$unit"
        rm -f -- "$unit_path" || failed=1
        if [[ -e "$backup_dir/$unit" || -L "$backup_dir/$unit" ]]; then
            cp -a -- "$backup_dir/$unit" "$unit_path" || failed=1
        fi
    done
    systemctl daemon-reload || failed=1
    for unit in "${systemd_units[@]}"; do
        state=$(tr -d '\r\n' <"$backup_dir/$unit.enabled-state")
        case "$state" in
            enabled)
                systemctl enable "$unit" >/dev/null 2>&1 || failed=1
                ;;
            enabled-runtime)
                systemctl enable --runtime "$unit" >/dev/null 2>&1 || failed=1
                ;;
            masked)
                systemctl mask "$unit" >/dev/null 2>&1 || failed=1
                ;;
            masked-runtime)
                systemctl mask --runtime "$unit" >/dev/null 2>&1 || failed=1
                ;;
        esac
    done
    return "$failed"
}

restore_previous_service_activity() {
    local backup_dir="$backup_root/systemd-units"
    local unit
    local state
    local failed=0

    for unit in "${systemd_units[@]}"; do
        state=$(tr -d '\r\n' <"$backup_dir/$unit.active-state")
        case "$state" in
            active|activating)
                systemctl start "$unit" || failed=1
                if [[ "$unit" != "healthdoc-agent-cleanup.service" ]]; then
                    systemctl is-active --quiet "$unit" || failed=1
                fi
                ;;
        esac
    done
    return "$failed"
}

restore_notification_gate_state() {
    local backup_dir="$backup_root/systemd-units"
    local state_file="$backup_dir/notification-gate.state"
    local gate_state
    local restore_candidate="$notification_gate.restore.$release_id"

    if [[ ! -f "$state_file" ]]; then
        echo "Notification gate backup state is missing." >&2
        return 1
    fi
    gate_state=$(tr -d '\r\n' <"$state_file")
    case "$gate_state" in
        present)
            if [[ ! -d "$(dirname "$notification_gate")" ]]; then
                install -d -o healthdoc -g www-data -m 750 \
                    "$(dirname "$notification_gate")" || return 1
            fi
            install -o healthdoc -g www-data -m 640 /dev/null "$restore_candidate" \
                || return 1
            mv -Tf "$restore_candidate" "$notification_gate" || return 1
            ;;
        absent)
            rm -f -- "$restore_candidate" "$notification_gate" || return 1
            ;;
        *)
            echo "Invalid notification gate backup state: $gate_state" >&2
            return 1
            ;;
    esac
}

if [[ -e "$release" ]]; then
    echo "Release already exists: $release" >&2
    exit 2
fi

install -d -o root -g root -m 755 "$release"
release_created=1
tar -xzf "$archive" -C "$release"
test -f "$release/backend/wsgi.py"
test -f "$release/frontend/dist/index.html"
test -f "$release/deploy/apache-healthdoc.conf"
test -f "$release/RELEASE_COMMIT"
actual_commit=$(tr -d '\r\n' <"$release/RELEASE_COMMIT")
if [[ "$actual_commit" != "$expected_commit" ]]; then
    echo "Release archive commit mismatch: expected $expected_commit, found $actual_commit" >&2
    exit 1
fi

# Build dependencies inside the immutable release before stopping writes. The
# currently serving release keeps its own interpreter (or the legacy shared
# interpreter), so a failed install cannot change the rollback environment.
/usr/bin/python3 -m venv "$release/venv"
"$release_python" -m pip install --disable-pip-version-check --no-cache-dir \
    "pip==26.1.2"
"$release_python" -m pip install --disable-pip-version-check --no-cache-dir \
    -r "$release/backend/requirements.txt"
"$release_python" -m pip check
"$release_python" -m pip freeze --all | LC_ALL=C sort >"$release/DEPENDENCIES.txt"
sha256sum "$release/backend/requirements.txt" >"$release/REQUIREMENTS.sha256"

if [[ -n "$demo_assets" ]]; then
    media_manifest="$release/backend/report_media_manifest.json"
    test -f "$media_manifest"
    expected_asset_count=$(
        "$release_python" -c \
            'import json,sys; print(len(json.load(open(sys.argv[1], encoding="utf-8"))["items"]))' \
            "$media_manifest"
    )
    unexpected_assets=$(tar -tzf "$demo_assets" | grep -Ev '^(institutions/demo-v8|health-assets/demo-v8|health-assets/demo-v10)(/|/[^/]+\.png)?$' || true)
    asset_count=$(tar -tzf "$demo_assets" | grep -Ec '\.png$' || true)
    if [[ -n "$unexpected_assets" || "$asset_count" != "$expected_asset_count" ]]; then
        echo "Report media archive contains an unexpected path or file count." >&2
        exit 1
    fi
fi

# Install and validate the versioned Apache configuration before switching the
# live release. This guarantees the browser receives the latest SPA shell.
apache_config_prepared=1
install -o root -g root -m 644 \
    "$release/deploy/apache-healthdoc.conf" "$apache_candidate"
mv -f "$apache_candidate" "$apache_config"
if ! apache2ctl configtest; then
    echo "Apache configuration validation failed; the current release was not changed." >&2
    exit 1
fi

backup_root="/var/backups/healthdoc/$release_id"
if [[ -e "$backup_root" ]]; then
    echo "Refusing to reuse an existing backup directory: $backup_root" >&2
    exit 2
fi
install -d -o root -g root -m 700 "$backup_root"
install -m 600 "$env_backup" "$backup_root/healthdoc.env"
install -m 600 "$apache_backup" "$backup_root/apache-healthdoc.conf"
apache_state=$(systemctl is-active apache2 2>/dev/null || true)
printf '%s\n' "${apache_state:-unknown}" >"$backup_root/apache2.active-state"
chmod 600 "$backup_root/apache2.active-state"
printf '%s\n' "${previous:-none}" >"$backup_root/previous-release-path.txt"
chmod 600 "$backup_root/previous-release-path.txt"
backup_systemd_units
if [[ ! "$previous" =~ ^/opt/healthdoc/releases/[0-9]{8}T[0-9]{6}Z$ || ! -d "$previous" ]]; then
    echo "Refusing to deploy without a validated previous release: ${previous:-none}" >&2
    exit 2
fi
tar -C "$(dirname "$previous")" -czf \
    "$backup_root/current-release.tar.gz.partial" "$(basename "$previous")"
chmod 600 "$backup_root/current-release.tar.gz.partial"
tar -tzf "$backup_root/current-release.tar.gz.partial" >/dev/null
mv -f "$backup_root/current-release.tar.gz.partial" "$backup_root/current-release.tar.gz"
wait_for_database() {
    for _ in $(seq 1 60); do
        if (echo >/dev/tcp/127.0.0.1/5432) >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

wait_for_database_connection() {
    "$release_python" - <<'PY'
import os
import time

from sqlalchemy import create_engine, text


engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
try:
    for attempt in range(120):
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            break
        except Exception:
            if attempt == 119:
                raise
            time.sleep(1)
finally:
    engine.dispose()
PY
}

recover_interrupted_notification_claims() {
    # A pre-v12 worker may be killed after committing status=sending but before
    # recording the SMTP outcome. Normalize those rows only after every writer
    # is proven inactive and before the cold backup, so even a rollback to the
    # legacy worker can retry them. SMTP remains explicitly at-least-once.
    (
        set -a
        # shellcheck disable=SC1091
        source "$env_file"
        set +a
        if [[ -z "${DATABASE_URL:-}" ]]; then
            echo "DATABASE_URL is missing while recovering notification claims." >&2
            exit 1
        fi
        wait_for_database_connection
        "$release_python" - <<'PY'
import os

from sqlalchemy import create_engine, inspect, text


engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
try:
    with engine.begin() as connection:
        if "notification_outbox" not in inspect(connection).get_table_names():
            raise RuntimeError("notification_outbox table is missing")
        recovered = connection.execute(
            text(
                "UPDATE notification_outbox "
                "SET status='failed', next_attempt_at=CURRENT_TIMESTAMP "
                "WHERE status='sending'"
            )
        ).rowcount
    print(f"notification_claims_recovered={int(recovered or 0)}", flush=True)
finally:
    engine.dispose()
PY
    )
}

verify_database_contract() {
    "$release_python" - <<'PY'
import os

from sqlalchemy import create_engine, inspect, text


expected_revision = "20260730_schema_v12"
required_tables = {
    "appointment_complaints",
    "booking_participant_tokens",
    "comment_sanctions",
    "delegated_action_audits",
    "institution_audience_insight_cache",
}
engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
try:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        if revision != expected_revision:
            raise RuntimeError(
                f"database revision mismatch: expected={expected_revision}, actual={revision}"
            )
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        missing = required_tables - tables
        if missing:
            raise RuntimeError(f"schema-v12 tables missing: {sorted(missing)}")
        user_columns = {item["name"] for item in inspector.get_columns("users")}
        if "identity_completed_at" not in user_columns:
            raise RuntimeError("schema-v12 users.identity_completed_at is missing")
finally:
    engine.dispose()
PY
}

release_payload_matches() {
    local required_commit=$1
    local payload_kind=$2
    "$release_python" -c '
import json
import sys


required_commit, payload_kind = sys.argv[1:]
try:
    payload = json.load(sys.stdin)
except (json.JSONDecodeError, TypeError):
    raise SystemExit(1)
valid = (
    isinstance(payload, dict)
    and payload.get("release_commit") == required_commit
    and type(payload.get("schema_version")) is int
    and payload["schema_version"] == 12
)
if payload_kind == "health":
    valid = valid and payload.get("status") == "ok"
elif payload_kind != "frontend":
    valid = False
raise SystemExit(0 if valid else 1)
' "$required_commit" "$payload_kind"
}

stop_database_container() {
    local state
    if ! state=$(docker inspect -f '{{.State.Running}}' healthdoc-gaussdb 2>/dev/null); then
        echo "Unable to inspect the openGauss container before stopping it." >&2
        return 1
    fi
    if [[ "$state" == "true" ]]; then
        docker stop healthdoc-gaussdb >/dev/null || return 1
    fi
    if ! state=$(docker inspect -f '{{.State.Running}}' healthdoc-gaussdb 2>/dev/null); then
        return 1
    fi
    if [[ "$state" != "false" ]]; then
        echo "openGauss container did not reach the stopped state." >&2
        return 1
    fi
    database_container_stopped=1
}

ensure_database_container_running() {
    local state
    if ! state=$(docker inspect -f '{{.State.Running}}' healthdoc-gaussdb 2>/dev/null); then
        echo "Unable to inspect the openGauss container before starting it." >&2
        return 1
    fi
    if [[ "$state" != "true" ]]; then
        docker start healthdoc-gaussdb >/dev/null || return 1
    fi
    if ! state=$(docker inspect -f '{{.State.Running}}' healthdoc-gaussdb 2>/dev/null); then
        return 1
    fi
    if [[ "$state" != "true" ]]; then
        echo "openGauss container did not reach the running state." >&2
        return 1
    fi
    if ! wait_for_database; then
        return 1
    fi
    database_container_stopped=0
}

create_uploads_backup() {
    local partial="$backup_root/uploads.tar.gz.partial"
    if [[ ! -d /var/lib/healthdoc/uploads ]]; then
        install -m 600 /dev/null "$backup_root/uploads.was-absent"
        return 0
    fi
    rm -f "$partial"
    tar -C /var/lib/healthdoc -czf "$partial" uploads
    chmod 600 "$partial"
    tar -tzf "$partial" >/dev/null
    mv -f "$partial" "$backup_root/uploads.tar.gz"
}

create_database_backup() {
    local partial="$backup_root/opengauss.tar.gz.partial"
    local backup_failed=0
    if [[ ! -d /var/lib/healthdoc/opengauss ]]; then
        echo "openGauss data directory is missing; refusing cold backup." >&2
        return 1
    fi
    rm -f "$partial"
    if ! stop_database_container; then
        return 1
    fi
    tar -C /var/lib/healthdoc -czf "$partial" opengauss || backup_failed=1
    if [[ "$backup_failed" == 0 ]]; then
        chmod 600 "$partial" || backup_failed=1
        tar -tzf "$partial" >/dev/null || backup_failed=1
    fi
    if [[ "$backup_failed" == 0 ]]; then
        mv -f "$partial" "$backup_root/opengauss.tar.gz" || backup_failed=1
    fi
    if ! ensure_database_container_running; then
        backup_failed=1
    fi
    if [[ "$backup_failed" != 0 ]]; then
        rm -f "$partial"
        return 1
    fi
    database_backup="$backup_root/opengauss.tar.gz"
}

restore_database_backup() {
    if [[ -z "$database_backup" || ! -f "$database_backup" ]]; then
        return 0
    fi
    if ! tar -tzf "$database_backup" >/dev/null; then
        echo "Cold database backup is unreadable; refusing destructive restore." >&2
        return 1
    fi
    if ! stop_database_container; then
        return 1
    fi
    # The validated compressed cold backup is the rollback source of truth.
    # Keeping a second raw copy of the failed import previously consumed
    # several gigabytes per incident and could itself make extraction fail.
    # Preserve a small diagnostic marker, then reclaim the known data path
    # before restoring the archive.
    if [[ -d /var/lib/healthdoc/opengauss ]]; then
        {
            printf 'failed_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
            du -sh /var/lib/healthdoc/opengauss 2>/dev/null \
                | sed 's/^/discarded_state_size=/'
        } >"$backup_root/failed-database-state.txt" || true
    fi
    rm -rf -- /var/lib/healthdoc/opengauss || return 1
    tar -C /var/lib/healthdoc -xzf "$database_backup" || return 1
    if [[ ! -d /var/lib/healthdoc/opengauss ]]; then
        echo "Cold database backup did not restore the expected data directory." >&2
        return 1
    fi
    ensure_database_container_running
}

rollback_release() {
    local failed=0
    local state
    local old_health_required=0
    local healthy=0

    # A rollback may replace both the database directory and uploads. Never
    # begin those destructive restores while any public entrypoint or
    # application writer remains alive.
    quiesce_external_writes || failed=1
    if [[ "$failed" != 0 ]]; then
        echo "Rollback could not prove all writers inactive; data was left untouched for manual recovery." >&2
        return 1
    fi
    if [[ -n "$database_backup" && -f "$database_backup" ]]; then
        restore_database_backup || failed=1
    fi
    ensure_database_container_running || failed=1
    restore_uploads_backup || failed=1
    cp -p "$env_backup" "$env_file" || failed=1
    cp -p "$apache_backup" "$apache_config" || failed=1

    # A listening TCP port is insufficient: prove the restored database can
    # execute SQL with the restored production credentials before any old
    # service or notification worker is allowed to start.
    if [[ "$failed" == 0 ]] && ! (
        set -a
        # shellcheck disable=SC1091
        source "$env_file"
        set +a
        [[ -n "${DATABASE_URL:-}" ]]
        wait_for_database_connection
    ); then
        echo "Restored openGauss did not accept SQL with the restored environment." >&2
        failed=1
    fi

    if [[ -n "$previous" && ! -d "$previous" \
        && -f "$backup_root/current-release.tar.gz" \
        && "$previous" =~ ^/opt/healthdoc/releases/[0-9]{8}T[0-9]{6}Z$ ]]; then
        if tar -tzf "$backup_root/current-release.tar.gz" >/dev/null; then
            tar -C "$(dirname "$previous")" -xzf "$backup_root/current-release.tar.gz" \
                || failed=1
        else
            failed=1
        fi
    fi
    if [[ -n "$previous" && -d "$previous" ]]; then
        ln -sfn "$previous" /opt/healthdoc/current.rollback || failed=1
        mv -Tf /opt/healthdoc/current.rollback /opt/healthdoc/current || failed=1
        if [[ "$(readlink -f /var/www/html 2>/dev/null || true)" == "/var/www/html" ]]; then
            find /var/www/html -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + \
                || failed=1
            cp -a "$previous/frontend/dist/." /var/www/html/ || failed=1
            chown -R root:www-data /var/www/html || failed=1
        else
            echo "Unsafe frontend document root during rollback." >&2
            failed=1
        fi
    else
        echo "Validated previous release disappeared during rollback." >&2
        failed=1
    fi

    restore_systemd_units || failed=1
    restore_notification_gate_state || failed=1

    # Never restart a writer when any static/data restore step failed. A
    # failed rollback deliberately leaves the application and proxy stopped
    # for manual recovery from the validated cold backup.
    if [[ "$failed" == 0 ]]; then
        restore_previous_service_activity || failed=1
    fi
    if [[ "$failed" == 0 ]]; then
        state=$(tr -d '\r\n' <"$backup_root/systemd-units/healthdoc.service.active-state")
        if [[ "$state" == "active" || "$state" == "activating" ]]; then
            old_health_required=1
            for _ in $(seq 1 30); do
                if curl -fsS http://127.0.0.1:5050/api/health >/dev/null; then
                    healthy=1
                    break
                fi
                sleep 1
            done
        else
            healthy=1
        fi
        if [[ "$old_health_required" == 1 && "$healthy" != 1 ]]; then
            journalctl -u healthdoc.service -n 80 --no-pager >&2 || true
            failed=1
        fi
    fi
    if [[ "$failed" == 0 ]]; then
        state=$(tr -d '\r\n' <"$backup_root/apache2.active-state")
        case "$state" in
            active|activating)
                systemctl start apache2 || failed=1
                ;;
            *)
                systemctl stop apache2 || failed=1
                ;;
        esac
    fi
    if [[ "$failed" == 0 ]]; then
        state=$(tr -d '\r\n' <"$backup_root/apache2.active-state")
        if [[ "$state" == "active" || "$state" == "activating" ]]; then
            if ! systemctl is-active --quiet apache2 \
                || ! curl -fsS http://127.0.0.1/ >/dev/null; then
                failed=1
            fi
        fi
    fi
    if [[ "$failed" != 0 ]]; then
        stop_application_services || true
        systemctl stop apache2 || true
    fi
    if [[ "$failed" == 0 ]]; then
        recovery_complete=1
        echo "Previous release, data, configuration and service state restored." >&2
    fi
    return "$failed"
}

if [[ -n "$demo_database" ]]; then
    deployment_started=1
    quiesce_external_writes
    recover_interrupted_notification_claims
    create_uploads_backup
    create_database_backup

    set -a
    # shellcheck disable=SC1091
    source /etc/healthdoc/healthdoc.env
    set +a
    if [[ -z "${DATABASE_URL:-}" ]]; then
        echo "DATABASE_URL is missing from the server environment file." >&2
        exit 1
    fi
    if ! wait_for_database_connection; then
        echo "openGauss opened its port but did not accept SQL connections." >&2
        exit 1
    fi
    export TARGET_DATABASE_URL="$DATABASE_URL"
    if ! (
        cd "$release/backend"
        "$release_python" scripts/migrate_sqlite_to_gaussdb.py \
            --source "$demo_database" --replace
    ); then
        echo "Demo database import failed; automatic rollback will restore the previous state." >&2
        unset TARGET_DATABASE_URL DATABASE_URL
        exit 1
    fi
    unset TARGET_DATABASE_URL DATABASE_URL
    rm -rf /var/lib/healthdoc/uploads
    install -d -o healthdoc -g www-data -m 750 /var/lib/healthdoc/uploads
    tar -xzf "$demo_assets" -C /var/lib/healthdoc/uploads
    chown -R healthdoc:www-data /var/lib/healthdoc/uploads
    find /var/lib/healthdoc/uploads -type d -exec chmod 750 {} +
    find /var/lib/healthdoc/uploads -type f -exec chmod 640 {} +
    test "$(find /var/lib/healthdoc/uploads -type f -name '*.png' | wc -l)" = "$expected_asset_count"
    (
        cd "$release/backend"
        "$release_python" scripts/refresh_demo_media.py \
            --upload-dir /var/lib/healthdoc/uploads --check-only
    )
    rm -f "$demo_database" "$demo_assets"
fi

# Every production release takes a full cold backup before changing runtime
# configuration or applying an additive migration. The ordinary path never
# imports a local/demo database.
if [[ -z "$database_backup" ]]; then
    deployment_started=1
    quiesce_external_writes
    recover_interrupted_notification_claims
    create_uploads_backup
    create_database_backup
fi

if [[ -n "$mail_settings" ]]; then
    apply_mail_settings
    rm -f "$mail_settings"
fi

# This helper targets the production host. A demo redirect silently sends every
# user's password code to one tester mailbox, so production releases clear it.
upsert_env NOTIFICATION_EMAIL_REDIRECT ""
if ! grep -Eq '^ACCOUNT_CREDENTIAL_ENCRYPTION_KEY=.{32,}$' "$env_file"; then
    account_credential_key=$(
        "$release_python" -c \
            'import secrets; print(secrets.token_hex(32))'
    )
    upsert_env ACCOUNT_CREDENTIAL_ENCRYPTION_KEY "$account_credential_key"
    unset account_credential_key
fi

set -a
# shellcheck disable=SC1091
source "$env_file"
set +a
if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "DATABASE_URL is missing from the server environment file." >&2
    exit 1
fi
if ! wait_for_database_connection || ! (
    cd "$release/backend"
    "$release_python" scripts/migrate_schema_v12.py
); then
    echo "Schema v12 migration failed; automatic rollback will restore the previous state." >&2
    unset DATABASE_URL
    exit 1
fi
unset DATABASE_URL

# The media-only path refreshes the manifest-approved demo-v8/v10 directories and their
# existing report-asset metadata. It never imports a local database or removes
# any other upload. The full uploads/database backups above cover rollback.
if [[ -n "$demo_assets" && -z "$demo_database" ]]; then
    media_stage=$(mktemp -d /tmp/healthdoc-demo-media.XXXXXX)
    if ! tar -xzf "$demo_assets" -C "$media_stage"; then
        rm -rf "$media_stage"
        exit 1
    fi
    install -d -o healthdoc -g www-data -m 750 \
        /var/lib/healthdoc/uploads/institutions \
        /var/lib/healthdoc/uploads/health-assets
    rm -rf \
        /var/lib/healthdoc/uploads/institutions/demo-v8 \
        /var/lib/healthdoc/uploads/health-assets/demo-v8 \
        /var/lib/healthdoc/uploads/health-assets/demo-v10
    cp -a "$media_stage/institutions/demo-v8" /var/lib/healthdoc/uploads/institutions/
    cp -a "$media_stage/health-assets/demo-v8" /var/lib/healthdoc/uploads/health-assets/
    cp -a "$media_stage/health-assets/demo-v10" /var/lib/healthdoc/uploads/health-assets/
    rm -rf "$media_stage"
    chown -R healthdoc:www-data \
        /var/lib/healthdoc/uploads/institutions/demo-v8 \
        /var/lib/healthdoc/uploads/health-assets/demo-v8 \
        /var/lib/healthdoc/uploads/health-assets/demo-v10
    find /var/lib/healthdoc/uploads/institutions/demo-v8 /var/lib/healthdoc/uploads/health-assets/demo-v8 /var/lib/healthdoc/uploads/health-assets/demo-v10 \
        -type d -exec chmod 750 {} +
    find /var/lib/healthdoc/uploads/institutions/demo-v8 /var/lib/healthdoc/uploads/health-assets/demo-v8 /var/lib/healthdoc/uploads/health-assets/demo-v10 \
        -type f -exec chmod 640 {} +
    set -a
    # shellcheck disable=SC1091
    source "$env_file"
    set +a
    if ! (
        cd "$release/backend"
        "$release_python" scripts/refresh_demo_media.py \
            --upload-dir /var/lib/healthdoc/uploads --apply --yes
    ); then
        unset DATABASE_URL
        exit 1
    fi
    unset DATABASE_URL
    rm -f "$demo_assets"
fi

install -d -o healthdoc -g www-data -m 750 \
    "$rag_root" "$rag_root/qdrant" "$rag_root/models" \
    "$rag_root/cache" "$rag_root/huggingface"

if grep -Eiq '^RAG_ENABLED=(1|true|yes|on)$' "$env_file"; then
    systemctl stop healthdoc.service
fi

upsert_env RAG_ENABLED 1
upsert_env RAG_RUNTIME_PATH "$rag_root"
upsert_env RAG_STORAGE_PATH "$rag_root/qdrant"
upsert_env RAG_MODEL_CACHE_PATH "$rag_root/models"
upsert_env RELEASE_COMMIT "$expected_commit"
upsert_env PUBLIC_APP_URL "$public_app_url"

set +e
runuser -u healthdoc -- env \
    HOME=/var/lib/healthdoc \
    XDG_CACHE_HOME="$rag_root/cache" \
    HF_HOME="$rag_root/huggingface" \
    RAG_RUNTIME_PATH="$rag_root" \
    RAG_STORAGE_PATH="$rag_root/qdrant" \
    RAG_MODEL_CACHE_PATH="$rag_root/models" \
    "$release_python" "$release/backend/scripts/rag_sync.py" sync
rag_sync_status=$?
set -e
if [[ "$rag_sync_status" != 0 ]]; then
    echo "RAG sync failed; automatic rollback will restore the previous state." >&2
    exit "$rag_sync_status"
fi

ln -sfn "$release" /opt/healthdoc/current.new
mv -Tf /opt/healthdoc/current.new /opt/healthdoc/current
release_activated=1

test "$(readlink -f /var/www/html)" = /var/www/html
find /var/www/html -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
cp -a "$release/frontend/dist/." /var/www/html/
chown -R root:www-data /var/www/html
find /var/www/html -type d -exec chmod 755 {} +
find /var/www/html -type f -exec chmod 644 {} +

install -o root -g root -m 644 \
    "$release/deploy/healthdoc.service" /etc/systemd/system/healthdoc.service
install -o root -g root -m 644 \
    "$release/deploy/healthdoc-notifications.service" /etc/systemd/system/healthdoc-notifications.service
install -o root -g root -m 644 \
    "$release/deploy/healthdoc-mcp.service" /etc/systemd/system/healthdoc-mcp.service
install -o root -g root -m 644 \
    "$release/deploy/healthdoc-agent-cleanup.service" /etc/systemd/system/healthdoc-agent-cleanup.service
install -o root -g root -m 644 \
    "$release/deploy/healthdoc-agent-cleanup.timer" /etc/systemd/system/healthdoc-agent-cleanup.timer
systemctl daemon-reload
systemctl enable healthdoc-notifications.service >/dev/null
systemctl enable healthdoc-agent-cleanup.timer >/dev/null
if ! systemd-analyze verify \
    /etc/systemd/system/healthdoc.service \
    /etc/systemd/system/healthdoc-notifications.service \
    /etc/systemd/system/healthdoc-mcp.service \
    /etc/systemd/system/healthdoc-agent-cleanup.service \
    /etc/systemd/system/healthdoc-agent-cleanup.timer; then
    echo "Candidate systemd unit verification failed." >&2
    exit 1
fi
systemctl restart healthdoc.service
if grep -Eq '^MCP_ENABLED=(1|true|yes|on)$' /etc/healthdoc/healthdoc.env; then
    systemctl enable healthdoc-mcp.service >/dev/null
    systemctl restart healthdoc-mcp.service
else
    systemctl disable --now healthdoc-mcp.service >/dev/null 2>&1 || true
fi
healthy=0
for _ in $(seq 1 30); do
    health_payload=$(curl -fsS http://127.0.0.1:5050/api/health || true)
    if release_payload_matches "$expected_commit" health <<<"$health_payload"; then
        healthy=1
        break
    fi
    sleep 1
done

if ! (
    set -a
    # shellcheck disable=SC1091
    source "$env_file"
    set +a
    verify_database_contract
); then
    echo "The live database does not satisfy the schema-v12 contract." >&2
    healthy=0
fi

frontend_release_payload=$(< /var/www/html/release.json)
if ! release_payload_matches "$expected_commit" frontend \
    <<<"$frontend_release_payload"; then
    healthy=0
fi

if grep -Eq '^MCP_ENABLED=(1|true|yes|on)$' /etc/healthdoc/healthdoc.env \
    && ! systemctl is-active --quiet healthdoc-mcp.service; then
    journalctl -u healthdoc-mcp.service -n 80 --no-pager >&2 || true
    healthy=0
fi

if ! (
    set -a
    # shellcheck disable=SC1091
    source "$env_file"
    set +a
    cd "$release/backend"
    runuser -u healthdoc --preserve-environment -- \
        "$release_python" scripts/notification_worker.py \
        --config production --check-config
); then
    echo "Notification worker configuration preflight failed without sending mail." >&2
    healthy=0
fi

if [[ "$healthy" != 1 ]]; then
    journalctl -u healthdoc.service -n 80 --no-pager >&2 || true
    echo "Candidate validation failed behind maintenance mode; automatic rollback will restore the previous state." >&2
    exit 1
fi

# Only after the candidate API, real database contract, static release marker,
# notification preflight and optional MCP service all pass do we start the
# worker behind a closed filesystem gate. It can be proven active but cannot
# claim or send an Outbox row until the public cutover is committed.
systemctl start healthdoc-agent-cleanup.timer
systemctl restart healthdoc-notifications.service
if ! systemctl is-active --quiet healthdoc-notifications.service; then
    journalctl -u healthdoc-notifications.service -n 80 --no-pager >&2 || true
    exit 1
fi
if ! systemctl is-active --quiet healthdoc-agent-cleanup.timer; then
    systemctl status healthdoc-agent-cleanup.timer --no-pager >&2 || true
    exit 1
fi
# The candidate has passed every gate while Apache is still stopped. Mark the
# new data/release as irreversible before reopening the public entrypoint: once
# Apache can acknowledge a request, automatic rollback must never discard it.
release_committed=1
apache_config_committed=1
systemctl start apache2
if ! systemctl is-active --quiet apache2; then
    journalctl -u apache2 -n 80 --no-pager >&2 || true
    exit 1
fi

# Apache is live only after the public commit point. The notification worker is
# released last; any failure from here preserves the new database and release.
install -o healthdoc -g www-data -m 640 /dev/null "$notification_gate"

rm -f "$archive"
if [[ -n "$demo_database" ]]; then
    rm -f "$demo_database"
fi
echo "Released $release_id successfully."
