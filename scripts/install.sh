#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/GalaxyPlexManager}"
DATA_DIR="${DATA_DIR:-/opt/galaxyplexmanager}"
REPO_URL="${REPO_URL:-https://github.com/gzowner/GalaxyPlexManager.git}"
BRANCH="${BRANCH:-main}"

log() { printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || fail "Run this installer as root."

log "Installing required packages"
apt-get update
apt-get install -y ca-certificates curl git openssl

if ! command -v docker >/dev/null 2>&1; then
  log "Installing Docker Engine"
  curl -fsSL https://get.docker.com | sh
fi

docker compose version >/dev/null 2>&1 || fail "Docker Compose plugin is unavailable."
systemctl enable --now docker

log "Preparing application directories"
mkdir -p "$APP_DIR" "$DATA_DIR/deployments" "$DATA_DIR/backups" "$DATA_DIR/templates/master"

if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" fetch --all --prune
  git -C "$APP_DIR" checkout "$BRANCH"
  git -C "$APP_DIR" pull --ff-only
else
  rm -rf "$APP_DIR"
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"
if [[ ! -f .env ]]; then
  cp .env.example .env
  SECRET_KEY="$(openssl rand -hex 32)"
  ADMIN_PASSWORD="$(openssl rand -base64 18 | tr -d '/+=' | head -c 20)"
  POSTGRES_PASSWORD="$(openssl rand -base64 24 | tr -d '/+=' | head -c 28)"
  sed -i "s|SECRET_KEY=.*|SECRET_KEY=${SECRET_KEY}|" .env
  sed -i "s|ADMIN_PASSWORD=.*|ADMIN_PASSWORD=${ADMIN_PASSWORD}|" .env
  sed -i "s|change-me@db|${POSTGRES_PASSWORD}@db|" .env
  printf '\nPOSTGRES_PASSWORD=%s\n' "$POSTGRES_PASSWORD" >> .env
  chmod 600 .env
  echo "$ADMIN_PASSWORD" > /root/galaxyplexmanager-admin-password.txt
  chmod 600 /root/galaxyplexmanager-admin-password.txt
fi

log "Building and starting Galaxy Plex Manager"
docker compose up -d --build

log "Waiting for the web panel"
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1; then
    echo
    echo "Galaxy Plex Manager is running at: http://$(hostname -I | awk '{print $1}'):8080"
    echo "Admin username: $(grep '^ADMIN_USERNAME=' .env | cut -d= -f2-)"
    echo "Admin password file: /root/galaxyplexmanager-admin-password.txt"
    echo "Next: place a stopped/snapshotted master Plex config in $DATA_DIR/templates/master"
    exit 0
  fi
  sleep 2
done

docker compose logs --tail=100 manager
fail "The panel did not become healthy."
