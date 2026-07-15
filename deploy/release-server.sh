#!/usr/bin/env bash
set -euo pipefail

archive=${1:-}
release_id=${2:-}

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

release="/opt/healthdoc/releases/$release_id"
previous=$(readlink -f /opt/healthdoc/current 2>/dev/null || true)
env_file=/etc/healthdoc/healthdoc.env
rag_root=/var/lib/healthdoc/rag
env_backup=$(mktemp /tmp/healthdoc-env.XXXXXX)
trap 'rm -f "$env_backup"' EXIT

if [[ ! -f "$env_file" ]]; then
    echo "Production environment file is missing: $env_file" >&2
    exit 2
fi
cp -p "$env_file" "$env_backup"

upsert_env() {
    local key=$1
    local value=$2
    if grep -q "^${key}=" "$env_file"; then
        sed -i "s|^${key}=.*$|${key}=${value}|" "$env_file"
    else
        printf '%s=%s\n' "$key" "$value" >>"$env_file"
    fi
}

if [[ -e "$release" ]]; then
    echo "Release already exists: $release" >&2
    exit 2
fi

install -d -o root -g root -m 755 "$release"
tar -xzf "$archive" -C "$release"
test -f "$release/backend/wsgi.py"
test -f "$release/frontend/dist/index.html"

/opt/healthdoc/venv/bin/python -m pip install -r "$release/backend/requirements.txt"
/opt/healthdoc/venv/bin/python -m pip check

install -d -o healthdoc -g www-data -m 750 \
    "$rag_root" "$rag_root/qdrant" "$rag_root/models" \
    "$rag_root/cache" "$rag_root/huggingface"

service_was_stopped=0
if grep -Eiq '^RAG_ENABLED=(1|true|yes|on)$' "$env_file"; then
    systemctl stop healthdoc.service
    service_was_stopped=1
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
    if [[ "$service_was_stopped" == 1 ]]; then
        systemctl start healthdoc.service
    fi
    rm -rf "$release"
    rm -f "$env_backup"
    echo "RAG sync failed; the current release and environment were preserved." >&2
    exit "$rag_sync_status"
fi

ln -sfn "$release" /opt/healthdoc/current.new
mv -Tf /opt/healthdoc/current.new /opt/healthdoc/current

test "$(readlink -f /var/www/html)" = /var/www/html
find /var/www/html -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
cp -a "$release/frontend/dist/." /var/www/html/
chown -R root:www-data /var/www/html
find /var/www/html -type d -exec chmod 755 {} +
find /var/www/html -type f -exec chmod 644 {} +

install -o root -g root -m 644 \
    "$release/deploy/healthdoc.service" /etc/systemd/system/healthdoc.service
systemctl daemon-reload
systemctl restart healthdoc.service
healthy=0
for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:5050/api/health >/dev/null; then
        healthy=1
        break
    fi
    sleep 1
done

if [[ "$healthy" != 1 ]]; then
    journalctl -u healthdoc.service -n 80 --no-pager >&2 || true
    cp -p "$env_backup" "$env_file"
    if [[ -n "$previous" && -d "$previous" ]]; then
        ln -sfn "$previous" /opt/healthdoc/current.rollback
        mv -Tf /opt/healthdoc/current.rollback /opt/healthdoc/current
        find /var/www/html -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
        cp -a "$previous/frontend/dist/." /var/www/html/
        chown -R root:www-data /var/www/html
        systemctl restart healthdoc.service
    fi
    echo "Health check failed; the previous release was restored." >&2
    exit 1
fi

rm -f "$archive"
rm -f "$env_backup"
echo "Released $release_id successfully."
