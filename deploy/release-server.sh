#!/usr/bin/env bash
set -euo pipefail

archive=${1:-}
release_id=${2:-}
demo_database=${3:-}
demo_assets=${4:-}
mail_settings=${5:-}

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
apache_config_prepared=0
apache_config_committed=0
deployment_started=0
release_activated=0
recovery_complete=0
expected_asset_count=0

cleanup() {
    local status=$?
    if [[ "$status" != 0 && "$deployment_started" == 1 && "$recovery_complete" == 0 ]]; then
        echo "Unexpected release failure; restoring the previous server state." >&2
        systemctl stop healthdoc.service 2>/dev/null || true
        systemctl stop healthdoc-notifications.service 2>/dev/null || true
        if [[ -n "${database_backup:-}" && -f "$database_backup" ]]; then
            restore_database_backup || true
        fi
        restore_uploads_backup || true
        cp -p "$env_backup" "$env_file" || true
        cp -p "$apache_backup" "$apache_config" || true
        if [[ "$release_activated" == 1 && -n "$previous" && -d "$previous" ]]; then
            ln -sfn "$previous" /opt/healthdoc/current.rollback
            mv -Tf /opt/healthdoc/current.rollback /opt/healthdoc/current
            find /var/www/html -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
            cp -a "$previous/frontend/dist/." /var/www/html/
            chown -R root:www-data /var/www/html
        fi
        start_current_services || true
        systemctl restart apache2 || true
        recovery_complete=1
    fi
    if [[ "$status" != 0 && "$apache_config_prepared" == 1 && "$apache_config_committed" == 0 ]]; then
        cp -p "$apache_backup" "$apache_config"
    fi
    rm -f "$env_backup" "$apache_backup" "$mail_settings" "$demo_assets" "$demo_database"
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

restore_uploads_backup() {
    if [[ -z "${backup_root:-}" || ! -f "$backup_root/uploads.tar.gz" ]]; then
        return 0
    fi
    rm -rf /var/lib/healthdoc/uploads
    tar -C /var/lib/healthdoc -xzf "$backup_root/uploads.tar.gz"
    chown -R healthdoc:www-data /var/lib/healthdoc/uploads
}

start_current_services() {
    systemctl start healthdoc.service
    if systemctl cat healthdoc-notifications.service >/dev/null 2>&1 \
        && [[ -f /opt/healthdoc/current/backend/scripts/notification_worker.py ]]; then
        systemctl start healthdoc-notifications.service
    fi
    recovery_complete=1
}

if [[ -e "$release" ]]; then
    echo "Release already exists: $release" >&2
    exit 2
fi

install -d -o root -g root -m 755 "$release"
tar -xzf "$archive" -C "$release"
test -f "$release/backend/wsgi.py"
test -f "$release/frontend/dist/index.html"
test -f "$release/deploy/apache-healthdoc.conf"
if [[ -n "$demo_assets" ]]; then
    media_manifest="$release/backend/report_media_manifest.json"
    test -f "$media_manifest"
    expected_asset_count=$(
        /opt/healthdoc/venv/bin/python -c \
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

/opt/healthdoc/venv/bin/python -m pip install -r "$release/backend/requirements.txt"
/opt/healthdoc/venv/bin/python -m pip check

# Install and validate the versioned Apache configuration before switching the
# live release. This guarantees the browser receives the latest SPA shell.
install -o root -g root -m 644 "$release/deploy/apache-healthdoc.conf" "$apache_config"
apache_config_prepared=1
if ! apache2ctl configtest; then
    cp -p "$apache_backup" "$apache_config"
    rm -rf "$release"
    echo "Apache configuration validation failed; the current release was not changed." >&2
    exit 1
fi

database_backup=""
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
    /opt/healthdoc/venv/bin/python - <<'PY'
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

restore_database_backup() {
    if [[ -z "$database_backup" || ! -f "$database_backup" ]]; then
        return 0
    fi
    docker stop healthdoc-gaussdb >/dev/null || true
    failed_database="/var/backups/healthdoc/$release_id/opengauss.failed"
    if [[ -e "$failed_database" ]]; then
        echo "Refusing to overwrite failed database snapshot: $failed_database" >&2
        return 1
    fi
    mv /var/lib/healthdoc/opengauss "$failed_database"
    tar -C /var/lib/healthdoc -xzf "$database_backup"
    docker start healthdoc-gaussdb >/dev/null
    wait_for_database
}

if [[ -n "$demo_database" ]]; then
    backup_root="/var/backups/healthdoc/$release_id"
    install -d -o root -g root -m 700 "$backup_root"
    install -m 600 /etc/healthdoc/healthdoc.env "$backup_root/healthdoc.env"
    if [[ -d /var/lib/healthdoc/uploads ]]; then
        tar -C /var/lib/healthdoc -czf "$backup_root/uploads.tar.gz" uploads
        chmod 600 "$backup_root/uploads.tar.gz"
    fi

    deployment_started=1
    systemctl stop healthdoc.service
    systemctl stop healthdoc-notifications.service 2>/dev/null || true
    docker stop healthdoc-gaussdb >/dev/null
    database_backup="$backup_root/opengauss.tar.gz"
    tar -C /var/lib/healthdoc -czf "$database_backup" opengauss
    chmod 600 "$database_backup"
    docker start healthdoc-gaussdb >/dev/null

    if ! wait_for_database; then
        echo "openGauss did not become ready after backup." >&2
        exit 1
    fi

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
        /opt/healthdoc/venv/bin/python scripts/migrate_sqlite_to_gaussdb.py \
            --source "$demo_database" --replace
    ); then
        echo "Demo database import failed; restoring the pre-release database." >&2
        unset TARGET_DATABASE_URL DATABASE_URL
        restore_database_backup
        restore_uploads_backup
        start_current_services
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
    rm -f "$demo_database" "$demo_assets"
fi

if [[ -n "$mail_settings" ]]; then
    while IFS='=' read -r key value; do
        value=${value%$'\r'}
        [[ -z "$key" ]] && continue
        case "$key" in
            SMTP_HOST|SMTP_PORT|SMTP_USERNAME|SMTP_PASSWORD|SMTP_FROM|SMTP_USE_TLS|NOTIFICATION_EMAIL_DRY_RUN|NOTIFICATION_EMAIL_REDIRECT)
                upsert_env "$key" "$value"
                ;;
            *)
                echo "Unexpected key in mail settings: $key" >&2
                exit 2
                ;;
        esac
    done <"$mail_settings"
    rm -f "$mail_settings"
fi

# This helper targets the production host. A demo redirect silently sends every
# user's password code to one tester mailbox, so production releases clear it.
upsert_env NOTIFICATION_EMAIL_REDIRECT ""

# Every production release takes a full cold backup before any additive
# migration. The ordinary path never imports a local/demo database.
if [[ -z "$database_backup" ]]; then
    backup_root="/var/backups/healthdoc/$release_id"
    install -d -o root -g root -m 700 "$backup_root"
    install -m 600 /etc/healthdoc/healthdoc.env "$backup_root/healthdoc.env"
    if [[ -d /var/lib/healthdoc/uploads ]]; then
        tar -C /var/lib/healthdoc -czf "$backup_root/uploads.tar.gz" uploads
        chmod 600 "$backup_root/uploads.tar.gz"
    fi
    deployment_started=1
    systemctl stop healthdoc.service
    systemctl stop healthdoc-notifications.service 2>/dev/null || true
    docker stop healthdoc-gaussdb >/dev/null
    database_backup="$backup_root/opengauss.tar.gz"
    tar -C /var/lib/healthdoc -czf "$database_backup" opengauss
    chmod 600 "$database_backup"
    docker start healthdoc-gaussdb >/dev/null
    wait_for_database
fi

set -a
# shellcheck disable=SC1091
source "$env_file"
set +a
if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "DATABASE_URL is missing from the server environment file." >&2
    restore_database_backup
    start_current_services
    exit 1
fi
if ! wait_for_database_connection || ! (
    cd "$release/backend"
    /opt/healthdoc/venv/bin/python scripts/migrate_schema_v10.py
); then
    echo "Schema v10 migration failed; restoring database and previous services." >&2
    unset DATABASE_URL
    restore_database_backup
    restore_uploads_backup
    start_current_services
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
        restore_database_backup
        restore_uploads_backup
        start_current_services
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
        /opt/healthdoc/venv/bin/python scripts/refresh_demo_media.py \
            --upload-dir /var/lib/healthdoc/uploads --apply --yes
    ); then
        unset DATABASE_URL
        restore_database_backup
        restore_uploads_backup
        start_current_services
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

set +e
runuser -u healthdoc -- env \
    HOME=/var/lib/healthdoc \
    XDG_CACHE_HOME="$rag_root/cache" \
    HF_HOME="$rag_root/huggingface" \
    RAG_RUNTIME_PATH="$rag_root" \
    RAG_STORAGE_PATH="$rag_root/qdrant" \
    RAG_MODEL_CACHE_PATH="$rag_root/models" \
    /opt/healthdoc/venv/bin/python "$release/backend/scripts/rag_sync.py" sync
rag_sync_status=$?
set -e
if [[ "$rag_sync_status" != 0 ]]; then
    cp -p "$env_backup" "$env_file"
    cp -p "$apache_backup" "$apache_config"
    rm -rf "$release"
    if [[ -n "$database_backup" && -f "$database_backup" ]]; then
        restore_database_backup
        restore_uploads_backup
    fi
    start_current_services
    if [[ -n "$demo_database" ]]; then
        rm -f "$demo_database"
    fi
    echo "RAG sync failed; the current release, environment and database were restored." >&2
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
systemctl daemon-reload
systemctl enable healthdoc-notifications.service >/dev/null
systemctl restart healthdoc.service
systemctl restart healthdoc-notifications.service
systemctl restart apache2
healthy=0
for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:5050/api/health >/dev/null; then
        healthy=1
        break
    fi
    sleep 1
done

if ! systemctl is-active --quiet apache2 || ! curl -fsS http://127.0.0.1/ >/dev/null; then
    journalctl -u apache2 -n 80 --no-pager >&2 || true
    healthy=0
fi

if ! systemctl is-active --quiet healthdoc-notifications.service; then
    journalctl -u healthdoc-notifications.service -n 80 --no-pager >&2 || true
    healthy=0
fi

if [[ "$healthy" != 1 ]]; then
    journalctl -u healthdoc.service -n 80 --no-pager >&2 || true
    systemctl stop healthdoc.service || true
    systemctl stop healthdoc-notifications.service || true
    if [[ -n "$database_backup" && -f "$database_backup" ]]; then
        restore_database_backup
        restore_uploads_backup
    fi
    cp -p "$env_backup" "$env_file"
    cp -p "$apache_backup" "$apache_config"
    if [[ -n "$previous" && -d "$previous" ]]; then
        ln -sfn "$previous" /opt/healthdoc/current.rollback
        mv -Tf /opt/healthdoc/current.rollback /opt/healthdoc/current
        find /var/www/html -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
        cp -a "$previous/frontend/dist/." /var/www/html/
        chown -R root:www-data /var/www/html
        systemctl restart healthdoc.service
        if [[ -f "$previous/backend/scripts/notification_worker.py" ]]; then
            systemctl restart healthdoc-notifications.service
        else
            systemctl disable healthdoc-notifications.service >/dev/null 2>&1 || true
        fi
    fi
    systemctl restart apache2
    recovery_complete=1
    echo "Health check failed; the previous release was restored." >&2
    exit 1
fi

rm -f "$archive"
if [[ -n "$demo_database" ]]; then
    rm -f "$demo_database"
fi
apache_config_committed=1
echo "Released $release_id successfully."
