#!/usr/bin/env bash
# Pull-based deploy. On the box, ghost-ledger-deploy.timer runs this every 5 minutes as
# /usr/local/bin/ghost-ledger-deploy. It pulls the image, and only if the image changed
# (or a service isn't running): extracts compose files + this script from the image, then
# `docker compose up -d`. The migrate job runs before the poller; the poller finishes its
# sweep on SIGTERM. CI never connects to the box.
#
# Overridable for the local rehearsal: IMAGE, DIR, ENV_FILE, BIN, SKIP_PULL=1, FORCE=1.
set -euo pipefail

IMAGE="${IMAGE:-ghcr.io/sohammandal/ghost-ledger:main}"
DIR="${DIR:-/opt/ghost-ledger}"
ENV_FILE="${ENV_FILE:-/etc/ghost-ledger/prod.env}"
BIN="${BIN:-/usr/local/bin/ghost-ledger-deploy}"
GHCR_ENV="${GHCR_ENV:-/etc/ghost-ledger/ghcr.env}"
PROJECT="${PROJECT:-ghost-ledger}"
# compose.prod.yml interpolates these, and compose reads them from the environment
export IMAGE ENV_FILE

log() { echo "$(date -u +%FT%TZ) deploy: $*"; }

if [ ! -f "$ENV_FILE" ]; then
  log "no $ENV_FILE yet (run make prod-secrets from the laptop); nothing to do"
  exit 0
fi

mkdir -p "$DIR"
# One deploy at a time. flock on the box; a lock directory where flock is missing (macOS).
if command -v flock >/dev/null; then
  exec 9>"$DIR/.deploy.lock"
  flock -n 9 || { log "another deploy is running"; exit 0; }
else
  mkdir "$DIR/.deploy.lock.d" 2>/dev/null || { log "another deploy is running"; exit 0; }
  trap 'rmdir "$DIR/.deploy.lock.d"' EXIT
fi

compose() {
  docker compose -p "$PROJECT" -f "$DIR/compose.yml" -f "$DIR/compose.prod.yml" \
    --env-file "$ENV_FILE" "$@"
}

before=$(docker image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null || true)
if [ "${SKIP_PULL:-0}" != 1 ]; then
  if [ -f "$GHCR_ENV" ]; then
    # shellcheck disable=SC1090
    . "$GHCR_ENV"  # GHCR_USER, GHCR_TOKEN (read:packages)
    echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin >/dev/null
  fi
  docker pull -q "$IMAGE" >/dev/null
fi
after=$(docker image inspect --format '{{.Id}}' "$IMAGE")

running=""
if [ -f "$DIR/compose.prod.yml" ]; then
  running=$(compose ps --status running --services 2>/dev/null | sort | tr '\n' ' ')
fi
if [ "$before" = "$after" ] && [ "$running" = "monitor poller postgres " ] \
  && [ "${FORCE:-0}" != 1 ]; then
  exit 0  # nothing changed and everything is up
fi

# Take the deploy files from the image, so they always match the code that runs.
tmp=$(mktemp -d)
cid=$(docker create "$IMAGE")
docker cp -q "$cid:/app/deploy/." "$tmp/"
docker rm "$cid" >/dev/null
for f in compose.yml compose.prod.yml; do
  cp "$tmp/$f" "$DIR/$f.new" && mv -f "$DIR/$f.new" "$DIR/$f"
done
# Replace this script by rename (new inode), never in place: bash is still reading it.
if [ -w "$(dirname "$BIN")" ]; then
  cp "$tmp/deploy.sh" "$BIN.new" && chmod 0755 "$BIN.new" && mv -f "$BIN.new" "$BIN"
fi
rm -rf "$tmp"

sha=$(docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$IMAGE" \
  | sed -n 's/^GIT_SHA=//p')
log "deploying ${sha:-unknown} (image ${after:7:12}; running before: ${running:-none})"
compose up -d --remove-orphans
docker image prune -f >/dev/null
log "done: $(compose ps --status running --services | sort | tr '\n' ' ')"
