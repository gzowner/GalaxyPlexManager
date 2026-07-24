#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE="${1:-}"
DESTINATION="${2:-/opt/galaxyplexmanager/templates/master}"
CONTAINER="${3:-}"

[[ ${EUID} -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
[[ -n "$SOURCE" && -d "$SOURCE" ]] || { echo "Usage: $0 /path/to/master/config [destination] [container-name]" >&2; exit 1; }

was_running=0
if [[ -n "$CONTAINER" ]] && docker inspect "$CONTAINER" >/dev/null 2>&1; then
  if [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER")" == "true" ]]; then
    was_running=1
    docker stop -t 60 "$CONTAINER"
  fi
fi

cleanup() {
  if [[ $was_running -eq 1 ]]; then docker start "$CONTAINER" >/dev/null; fi
}
trap cleanup EXIT

rm -rf "${DESTINATION}.new"
mkdir -p "${DESTINATION}.new"
rsync -aHAX --numeric-ids --delete \
  --exclude='Library/Application Support/Plex Media Server/Cache/' \
  --exclude='Library/Application Support/Plex Media Server/Logs/' \
  --exclude='Library/Application Support/Plex Media Server/Crash Reports/' \
  --exclude='Library/Application Support/Plex Media Server/Diagnostics/' \
  "$SOURCE/" "${DESTINATION}.new/"

if command -v sqlite3 >/dev/null 2>&1; then
  DB="${DESTINATION}.new/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"
  if [[ -f "$DB" ]]; then
    sqlite3 "$DB" 'PRAGMA integrity_check;' | grep -qx ok || { echo "Plex database integrity check failed." >&2; exit 1; }
  fi
fi

rm -rf "${DESTINATION}.previous"
[[ -d "$DESTINATION" ]] && mv "$DESTINATION" "${DESTINATION}.previous"
mv "${DESTINATION}.new" "$DESTINATION"
echo "Master template captured at $DESTINATION"
