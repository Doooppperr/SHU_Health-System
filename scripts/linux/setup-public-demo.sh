#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/health-system}"
REPO_URL="${REPO_URL:-https://github.com/Doooppperr/SHU_DB_1-health-system.git}"
REPO_REF="${REPO_REF:-main}"
SERVICE_USER="${SERVICE_USER:-health}"
BACKEND_PORT="${BACKEND_PORT:-5050}"
BACKEND_THREADS="${BACKEND_THREADS:-8}"
FRONTEND_PORT="${FRONTEND_PORT:-4173}"

DB_DRIVER="${DB_DRIVER:-opengauss+psycopg2}"
DB_HOST="${DB_HOST:-192.168.0.31}"
DB_PORT="${DB_PORT:-8000}"
DB_NAME="${DB_NAME:-health_system}"
DB_USER="${DB_USER:-health_app}"
OCR_USE_MOCK="${OCR_USE_MOCK:-1}"

BACKEND_SERVICE="health-backend.service"
FRONTEND_SERVICE="health-frontend.service"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    fail "Run with sudo or as root."
  fi
}

require_cmd() {
  local name="$1"
  command -v "$name" >/dev/null 2>&1 || fail "Missing command: $name"
}

check_node_version() {
  require_cmd node
  local major
  major="$(node -p 'Number(process.versions.node.split(".")[0])')"
  if [[ "$major" -lt 18 ]]; then
    fail "Node.js 18+ is required. Current version: $(node -v)"
  fi
}

shell_quote() {
  printf "%q" "$1"
}

sed_escape() {
  printf "%s" "$1" | sed -e 's/[&|\\]/\\&/g'
}

run_as_service_user() {
  if [[ "$SERVICE_USER" == "root" ]]; then
    "$@"
  else
    runuser -u "$SERVICE_USER" -- "$@"
  fi
}

ensure_service_user() {
  if ! getent group "$SERVICE_USER" >/dev/null 2>&1; then
    groupadd --system "$SERVICE_USER"
  fi

  if id "$SERVICE_USER" >/dev/null 2>&1; then
    return
  fi

  useradd --system --no-create-home --gid "$SERVICE_USER" --home-dir "$PROJECT_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
}

sync_repo() {
  local parent_dir
  parent_dir="$(dirname "$PROJECT_DIR")"
  mkdir -p "$parent_dir"

  if [[ -d "$PROJECT_DIR/.git" ]]; then
    echo "Updating existing repository at $PROJECT_DIR"
    chown -R "$SERVICE_USER:$SERVICE_USER" "$PROJECT_DIR"
    run_as_service_user git -C "$PROJECT_DIR" fetch --prune origin
    run_as_service_user git -C "$PROJECT_DIR" checkout "$REPO_REF"
    run_as_service_user git -C "$PROJECT_DIR" pull --ff-only origin "$REPO_REF"
  elif [[ -e "$PROJECT_DIR" ]]; then
    fail "$PROJECT_DIR exists but is not a Git repository."
  else
    echo "Cloning $REPO_URL to $PROJECT_DIR"
    git clone --branch "$REPO_REF" "$REPO_URL" "$PROJECT_DIR"
    chown -R "$SERVICE_USER:$SERVICE_USER" "$PROJECT_DIR"
  fi
}

write_env_if_missing() {
  local env_file="$PROJECT_DIR/backend/.env"
  if [[ -f "$env_file" && "${FORCE_ENV:-0}" != "1" ]]; then
    echo "Keeping existing $env_file"
    return
  fi

  local database_url="${DATABASE_URL:-}"
  if [[ -z "$database_url" ]]; then
    local db_password="${DB_PASSWORD:-}"
    if [[ -z "$db_password" ]]; then
      read -r -s -p "GaussDB password for ${DB_USER}: " db_password
      echo
    fi
    local db_password_encoded
    db_password_encoded="$(python3 - "$db_password" <<'PY'
import sys
from urllib.parse import quote

print(quote(sys.argv[1], safe=""))
PY
)"
    database_url="${DB_DRIVER}://${DB_USER}:${db_password_encoded}@${DB_HOST}:${DB_PORT}/${DB_NAME}?client_encoding=utf8"
  fi

  local jwt_secret="${JWT_SECRET_KEY:-}"
  if [[ -z "$jwt_secret" ]]; then
    jwt_secret="$(python3 - <<'PY'
import secrets

print(secrets.token_hex(32))
PY
)"
  fi

  cat > "$env_file" <<EOF
DATABASE_URL=${database_url}
TARGET_DATABASE_URL=${TARGET_DATABASE_URL:-$database_url}
JWT_SECRET_KEY=${jwt_secret}

OCR_PROVIDER=huawei
OCR_USE_MOCK=${OCR_USE_MOCK}
HUAWEI_OCR_ENDPOINT=${HUAWEI_OCR_ENDPOINT:-}
HUAWEI_OCR_AK=${HUAWEI_OCR_AK:-}
HUAWEI_OCR_SK=${HUAWEI_OCR_SK:-}
HUAWEI_PROJECT_ID=${HUAWEI_PROJECT_ID:-}
OCR_API_PATH=${OCR_API_PATH:-/v2/{project_id}/ocr/general-table}

UPLOAD_DIR=${PROJECT_DIR}/backend/uploads
UPLOAD_URL_BASE=/uploads

DB_POOL_SIZE=${DB_POOL_SIZE:-10}
DB_MAX_OVERFLOW=${DB_MAX_OVERFLOW:-20}
DB_POOL_TIMEOUT=${DB_POOL_TIMEOUT:-30}
DB_POOL_RECYCLE=${DB_POOL_RECYCLE:-1800}

DEFAULT_ADMIN_USERNAME=${DEFAULT_ADMIN_USERNAME:-admin}
DEFAULT_ADMIN_PASSWORD=${DEFAULT_ADMIN_PASSWORD:-admin123}
DEFAULT_ADMIN_EMAIL=${DEFAULT_ADMIN_EMAIL:-admin@example.com}
EOF

  chmod 600 "$env_file"
  chown "$SERVICE_USER:$SERVICE_USER" "$env_file"
  echo "Created $env_file"
}

install_dependencies() {
  local backend_dir_q frontend_dir_q
  backend_dir_q="$(shell_quote "$PROJECT_DIR/backend")"
  frontend_dir_q="$(shell_quote "$PROJECT_DIR/frontend")"

  run_as_service_user bash -lc "cd ${backend_dir_q} && python3 -m venv .venv && .venv/bin/python -m pip install --upgrade pip && .venv/bin/python -m pip install -r requirements.txt"
  run_as_service_user bash -lc "cd ${frontend_dir_q} && npm ci && npm run build"

  install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$PROJECT_DIR/backend/uploads"
}

install_unit_file() {
  local template="$1"
  local target="$2"

  [[ -f "$template" ]] || fail "Missing systemd template: $template"

  sed \
    -e "s|__PROJECT_DIR__|$(sed_escape "$PROJECT_DIR")|g" \
    -e "s|__SERVICE_USER__|$(sed_escape "$SERVICE_USER")|g" \
    -e "s|__BACKEND_PORT__|$(sed_escape "$BACKEND_PORT")|g" \
    -e "s|__BACKEND_THREADS__|$(sed_escape "$BACKEND_THREADS")|g" \
    -e "s|__FRONTEND_PORT__|$(sed_escape "$FRONTEND_PORT")|g" \
    "$template" > "$target"
}

install_systemd_services() {
  local template_dir="$PROJECT_DIR/scripts/linux"
  install_unit_file "$template_dir/health-backend.service.template" "/etc/systemd/system/$BACKEND_SERVICE"
  install_unit_file "$template_dir/health-frontend.service.template" "/etc/systemd/system/$FRONTEND_SERVICE"

  systemctl daemon-reload
  systemctl enable "$BACKEND_SERVICE" "$FRONTEND_SERVICE"
  systemctl restart "$BACKEND_SERVICE"
  systemctl restart "$FRONTEND_SERVICE"
}

verify_backend_import() {
  local backend_dir_q
  backend_dir_q="$(shell_quote "$PROJECT_DIR/backend")"
  run_as_service_user bash -lc "cd ${backend_dir_q} && .venv/bin/python - <<'PY'
from app import create_app

create_app('production')
print('backend app initialized')
PY"
}

verify_services() {
  local ok=0
  for _ in $(seq 1 20); do
    if curl -fsS "http://127.0.0.1:${BACKEND_PORT}/api/health" >/dev/null; then
      ok=1
      break
    fi
    sleep 1
  done
  [[ "$ok" -eq 1 ]] || fail "Backend health check failed. Run: journalctl -u $BACKEND_SERVICE -n 100 --no-pager"

  curl -fsSI "http://127.0.0.1:${FRONTEND_PORT}" >/dev/null || fail "Frontend check failed. Run: journalctl -u $FRONTEND_SERVICE -n 100 --no-pager"
}

main() {
  require_root
  require_cmd git
  require_cmd python3
  require_cmd curl
  require_cmd sed
  require_cmd getent
  require_cmd groupadd
  require_cmd install
  require_cmd runuser
  require_cmd seq
  require_cmd systemctl
  require_cmd useradd
  require_cmd npm
  check_node_version

  ensure_service_user
  sync_repo
  write_env_if_missing
  install_dependencies
  verify_backend_import
  install_systemd_services
  verify_services

  cat <<EOF

Public demo deployment is running.

Frontend: http://<SERVER_PUBLIC_IP>:${FRONTEND_PORT}
Backend health from server: curl http://127.0.0.1:${BACKEND_PORT}/api/health

Open only TCP ${FRONTEND_PORT} in the cloud security group.
Keep TCP ${BACKEND_PORT} and the database port closed to the public internet.
EOF
}

main "$@"
