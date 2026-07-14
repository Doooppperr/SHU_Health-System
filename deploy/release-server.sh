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

ln -sfn "$release" /opt/healthdoc/current.new
mv -Tf /opt/healthdoc/current.new /opt/healthdoc/current

test "$(readlink -f /var/www/html)" = /var/www/html
find /var/www/html -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
cp -a "$release/frontend/dist/." /var/www/html/
chown -R root:www-data /var/www/html
find /var/www/html -type d -exec chmod 755 {} +
find /var/www/html -type f -exec chmod 644 {} +

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
echo "Released $release_id successfully."
