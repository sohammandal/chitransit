#!/usr/bin/env bash
# Pull-based deploy. On the box, ghost-ledger-deploy.timer runs this every 5 minutes as
# /usr/local/bin/ghost-ledger-deploy. It pulls the image, and only if the image changed
# (or a service isn't running): verifies the image was signed by ci.yml on main (keyless
# cosign; an unsigned or foreign image is refused and nothing restarts), extracts the
# compose files from it, then `docker compose up -d`. This script itself never comes from
# the image: it changes only by `make box-deploy-script` (it runs as root). The migrate job runs before the poller; the poller finishes its
# sweep on SIGTERM. CI never connects to the box.
#
# Health gate: after a deploy, a sweep must commit within ~2 minutes. If none does, the
# previous good image (local tag <project>-deploy:last-good) is redeployed and the bad
# image's id is remembered so the timer doesn't redeploy it; the next CI image replaces
# it. Migrations are not undone: they must stay compatible with the previous code
# (expand, then contract; see CLAUDE.md).
#
# Overridable for the local rehearsal: IMAGE, DIR, ENV_FILE, BIN, SKIP_PULL=1, FORCE=1,
# HEALTH_CHECKS, VERIFY=0 (no registry, nothing to verify).
set -euo pipefail

IMAGE="${IMAGE:-ghcr.io/sohammandal/ghost-ledger:main}"
DIR="${DIR:-/opt/ghost-ledger}"
ENV_FILE="${ENV_FILE:-/etc/ghost-ledger/prod.env}"
BIN="${BIN:-/usr/local/bin/ghost-ledger-deploy}"
GHCR_ENV="${GHCR_ENV:-/etc/ghost-ledger/ghcr.env}"
PROJECT="${PROJECT:-ghost-ledger}"
# compose.prod.yml interpolates these, and compose reads them from the environment.
# COMPOSE_PROFILES=cdc runs Debezium; the laptop rehearsal sets it empty (no AWS there).
COMPOSE_PROFILES="${COMPOSE_PROFILES-cdc}"
export IMAGE ENV_FILE COMPOSE_PROFILES

log() { echo "$(date -u +%FT%TZ) deploy: $*"; }

# Only images signed by the main-branch image job may run here.
VERIFY="${VERIFY:-1}"
SIGNER="https://github.com/sohammandal/cta-ghost-ledger/.github/workflows/ci.yml@refs/heads/main"
ISSUER="https://token.actions.githubusercontent.com"
COSIGN_VERSION="v3.1.3"
COSIGN_SHA256="4629c757b7618056f8ddd7e2625ae9fdd94c0372a65049520bc7d9df9efc7f71"
COSIGN="${COSIGN:-/usr/local/bin/cosign}"

ensure_cosign() {
  if [ -x "$COSIGN" ] && echo "$COSIGN_SHA256  $COSIGN" | sha256sum -c --status; then
    return 0
  fi
  local tmp
  tmp=$(mktemp)
  curl -fsSL -o "$tmp" \
    "https://github.com/sigstore/cosign/releases/download/$COSIGN_VERSION/cosign-linux-amd64"
  echo "$COSIGN_SHA256  $tmp" | sha256sum -c --status || { rm -f "$tmp"; return 1; }
  install -m 0755 "$tmp" "$COSIGN" && rm -f "$tmp"
  log "installed cosign $COSIGN_VERSION"
}

# True if the pulled image (by digest) carries a signature from ci.yml on main.
signed_by_main() {
  local digest
  digest=$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$IMAGE" \
    | grep -m1 "^${IMAGE%:*}@sha256:" || true)
  [ -n "$digest" ] || { log "no registry digest for $IMAGE"; return 1; }
  ensure_cosign || { log "cannot install cosign"; return 1; }
  "$COSIGN" verify --certificate-identity "$SIGNER" --certificate-oidc-issuer "$ISSUER" \
    "$digest" >/dev/null 2>&1
}

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

# Local tag for the newest image that committed a sweep after its deploy, and the id of an
# image that failed to: the rollback target, and an image never to redeploy.
LAST_GOOD="$PROJECT-deploy:last-good"
BAD_FILE="$DIR/.bad-image"
HEALTH_CHECKS="${HEALTH_CHECKS:-12}"  # x 10 s: a healthy poller commits a sweep every 30 s

image_id() { docker image inspect --format '{{.Id}}' "$1" 2>/dev/null || true; }

# True once the poller has committed a sweep that finished after $1 (a SQL timestamp).
healthy() {
  local n
  for _ in $(seq 1 "$HEALTH_CHECKS"); do
    n=$(compose exec -T postgres psql -U ghost -d ghost_ledger -XAt \
      -c "SELECT COUNT(*) FROM ops.sweeps WHERE finished_at > $1" 2>/dev/null || true)
    [ "${n:-0}" -gt 0 ] 2>/dev/null && return 0
    sleep 10
  done
  return 1
}

# Take the deploy files from the image, so they always match the code that runs; then up.
deploy() {
  local cdc_conf="$DIR/cdc/application.prod.properties" cdc_before tmp cid sha
  cdc_before=$({ cat "$cdc_conf" 2>/dev/null || true; } | cksum)  # the first deploy has none
  tmp=$(mktemp -d)
  cid=$(docker create "$IMAGE")
  docker cp -q "$cid:/app/deploy/." "$tmp/"
  docker rm "$cid" >/dev/null
  mkdir -p "$DIR/cdc"
  for f in compose.yml compose.prod.yml cdc/application.prod.properties; do
    cp "$tmp/$f" "$DIR/$f.new" && mv -f "$DIR/$f.new" "$DIR/$f"
  done
  if [ -f "$tmp/deploy.sh" ] && ! cmp -s "$tmp/deploy.sh" "$BIN"; then
    log "note: the image carries a newer deploy.sh; install it with make box-deploy-script"
  fi
  rm -rf "$tmp"
  sha=$(docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$IMAGE" \
    | sed -n 's/^GIT_SHA=//p')
  log "deploying ${sha:-unknown} (image $(image_id "$IMAGE" | cut -c8-19); running before: ${running:-none})"
  # A failed migration exits non-zero and the poller never starts: the health check decides.
  compose up -d --remove-orphans || log "compose up failed"
  # A changed CDC config file doesn't change the compose definition, so compose keeps the
  # old container (and its bind mount of the old file): recreate Debezium to load it.
  if [ "$(cksum < "$cdc_conf")" != "$cdc_before" ] && compose ps --services | grep -qx debezium; then
    log "CDC config changed; recreating debezium"
    compose up -d --force-recreate debezium
  fi
}

before=$(image_id "$IMAGE")
if [ "${SKIP_PULL:-0}" != 1 ]; then
  if [ -f "$GHCR_ENV" ]; then
    # shellcheck disable=SC1090
    . "$GHCR_ENV"  # GHCR_USER, GHCR_TOKEN (read:packages)
    echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin >/dev/null
  fi
  docker pull -q "$IMAGE" >/dev/null
fi
after=$(image_id "$IMAGE")
if [ "$after" != "$before" ] && [ "$VERIFY" = 1 ] && ! signed_by_main; then
  # Not built and signed by ci.yml on main: put the tag back and change nothing.
  log "REFUSED ${after:7:12}: not signed by ci.yml on main; staying on the current image"
  [ -n "$before" ] && docker tag "$before" "$IMAGE"
  exit 0
fi
if [ -f "$BAD_FILE" ] && [ "$after" = "$(cat "$BAD_FILE")" ] && [ -n "$(image_id "$LAST_GOOD")" ]; then
  # This image already failed its health check here: stay on the last good one.
  docker tag "$LAST_GOOD" "$IMAGE"
  after=$(image_id "$IMAGE")
fi

running="" expected="unknown"
if [ -f "$DIR/compose.prod.yml" ]; then
  running=$(compose ps --status running --services 2>/dev/null | sort | tr '\n' ' ')
  # every long-running service of the active profiles (migrate is a one-shot job)
  expected=$(compose config --services 2>/dev/null | grep -vx migrate | sort | tr '\n' ' ')
fi
if [ "$before" = "$after" ] && [ "$running" = "$expected" ] && [ "${FORCE:-0}" != 1 ]; then
  # Nothing changed and everything is up. Remember this image as good if nothing is yet.
  if [ -z "$(image_id "$LAST_GOOD")" ] && HEALTH_CHECKS=1 healthy "NOW() - INTERVAL '2 minutes'"; then
    docker tag "$after" "$LAST_GOOD"
    log "marked ${after:7:12} as last good"
  fi
  exit 0
fi

started=$(date -u +%FT%TZ)
deploy
if healthy "'$started'"; then
  docker tag "$after" "$LAST_GOOD"
  rm -f "$BAD_FILE"
  log "healthy: a sweep committed after the deploy"
else
  good=$(image_id "$LAST_GOOD")
  if [ -n "$good" ] && [ "$good" != "$after" ]; then
    log "UNHEALTHY: no sweep committed after deploying ${after:7:12}; rolling back to ${good:7:12}"
    echo "$after" > "$BAD_FILE"
    docker tag "$LAST_GOOD" "$IMAGE"
    running=$(compose ps --status running --services 2>/dev/null | sort | tr '\n' ' ')
    deploy
    if healthy "'$started'"; then log "rollback healthy"; else log "rollback UNHEALTHY too"; fi
  else
    log "UNHEALTHY and no other known-good image to roll back to (poller-stale will alarm)"
  fi
fi
docker image prune -f >/dev/null
log "done: $(compose ps --status running --services | sort | tr '\n' ' ')"
