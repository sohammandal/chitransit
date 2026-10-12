#!/usr/bin/env bash
# Run on the laptop (`make prod-secrets`): write the box's secrets over SSH, then deploy.
#   /etc/ghost-ledger/prod.env  GHOST_LEDGER_ENV, POSTGRES_PASSWORD, DATABASE_URL, CTA keys,
#                               the box IAM user's access key, AWS_REGION   (mode 0600)
#   /etc/ghost-ledger/ghcr.env  GHCR_USER, GHCR_TOKEN (read:packages) for pulling the image
# Safe to re-run: it keeps the existing Postgres password (Postgres only reads it on first
# start) and the existing AWS key while it is still active. Never prints a secret.
#
# Needs: CTA_BUS_TRACKER_API_KEY in the environment (~/.secrets; CTA_TRAIN_TRACKER_API_KEY
# too, or the box keeps the one it has: without any, train collection stays off), a GitHub
# token with read:packages in $GHCR_TOKEN_FILE (default ~/.config/ghost-ledger/ghcr.token),
# AWS CLI credentials for this account, and the box IP (terraform output box_ip).
set -euo pipefail

BOX_IP="${1:?usage: push-secrets.sh <box-ip>}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/ghost_ledger}"
GHCR_USER="${GHCR_USER:-sohammandal}"
GHCR_TOKEN_FILE="${GHCR_TOKEN_FILE:-$HOME/.config/ghost-ledger/ghcr.token}"
BOX_USER_IAM="${BOX_USER_IAM:-ghost-ledger-box-prod}"
REGION="${AWS_REGION:-us-east-2}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ssh_box() { ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "ubuntu@$BOX_IP" "$@"; }

[ -n "${CTA_BUS_TRACKER_API_KEY:-}" ] || { echo "CTA_BUS_TRACKER_API_KEY not set"; exit 1; }
[ -s "$GHCR_TOKEN_FILE" ] || { echo "no GitHub token at $GHCR_TOKEN_FILE"; exit 1; }

# Current values on the box, if any (held in variables, never echoed)
existing=$(ssh_box "sudo cat /etc/ghost-ledger/prod.env 2>/dev/null || true")
get() { printf '%s\n' "$existing" | sed -n "s/^$1=//p" | head -1; }

train_key="${CTA_TRAIN_TRACKER_API_KEY:-$(get CTA_TRAIN_TRACKER_API_KEY)}"
[ -n "$train_key" ] || echo "no CTA_TRAIN_TRACKER_API_KEY: train collection stays off"

pw=$(get POSTGRES_PASSWORD)
if [ -z "$pw" ]; then
  pw=$(openssl rand -hex 24)
  echo "new Postgres password generated"
else
  echo "keeping the existing Postgres password"
fi

key_id=$(get AWS_ACCESS_KEY_ID)
secret=$(get AWS_SECRET_ACCESS_KEY)
keys() {  # this user's keys with a given status
  aws iam list-access-keys --user-name "$BOX_USER_IAM" \
    --query "AccessKeyMetadata[?Status=='$1'].AccessKeyId" --output text
}
if [ -n "$key_id" ] && printf '%s' "$(keys Active)" | grep -qw "$key_id"; then
  echo "keeping the existing AWS access key (${key_id:0:8}...)"
else
  # Inactive keys are safe to delete. Active ones never are (the box may be using one):
  # if two are active and neither is on the box, stop and let a human choose.
  for old in $(keys Inactive); do
    aws iam delete-access-key --user-name "$BOX_USER_IAM" --access-key-id "$old"
  done
  if [ "$(keys Active | wc -w)" -ge 2 ]; then
    echo "$BOX_USER_IAM already has 2 active keys and neither is on the box."
    echo "Delete one: aws iam delete-access-key --user-name $BOX_USER_IAM --access-key-id <id>"
    exit 1
  fi
  read -r key_id secret < <(aws iam create-access-key --user-name "$BOX_USER_IAM" \
    --query 'AccessKey.[AccessKeyId,SecretAccessKey]' --output text)
  echo "new AWS access key created for $BOX_USER_IAM (${key_id:0:8}...)"
  echo "once prod is healthy, delete any older active key: aws iam list-access-keys --user-name $BOX_USER_IAM"
fi

umask 077
printf '%s\n' \
  "GHOST_LEDGER_ENV=prod" \
  "POSTGRES_PASSWORD=$pw" \
  "DATABASE_URL=postgresql://ghost:$pw@postgres:5432/ghost_ledger?sslmode=disable" \
  "CTA_BUS_TRACKER_API_KEY=$CTA_BUS_TRACKER_API_KEY" \
  ${train_key:+"CTA_TRAIN_TRACKER_API_KEY=$train_key"} \
  "AWS_ACCESS_KEY_ID=$key_id" \
  "AWS_SECRET_ACCESS_KEY=$secret" \
  "AWS_REGION=$REGION" \
  "AWS_DEFAULT_REGION=$REGION" \
  "LIVE_TARGET=s3://ghost-ledger-site-$ACCOUNT_ID/live/vehicles.json" \
  | ssh_box "sudo install -m 0600 -o root -g root /dev/stdin /etc/ghost-ledger/prod.env"

printf '%s\n' "GHCR_USER=$GHCR_USER" "GHCR_TOKEN=$(cat "$GHCR_TOKEN_FILE")" \
  | ssh_box "sudo install -m 0600 -o root -g root /dev/stdin /etc/ghost-ledger/ghcr.env"

# FORCE=1: the image may be unchanged, but containers must restart to read the new env file
echo "secrets written; redeploying so the containers pick them up"
ssh_box "sudo env FORCE=1 /usr/local/bin/ghost-ledger-deploy"
