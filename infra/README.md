# Prod runbook

One Lightsail box (us-east-2) runs Postgres, the poller, the monitor and Debezium, pulling `ghcr.io/sohammandal/ghost-ledger:main`. Everything in AWS is Terraform: `terraform/bootstrap` (once, local state: state bucket, GitHub OIDC, CI roles), `terraform/prod` (box, lake, site, alarms). `box/` holds first-boot setup, `deploy.sh` and `push-secrets.sh`.

## Setup

1. `aws login --profile ghost-ledger` (admin user, us-east-2); `make ssh-key`; `make tf-bootstrap`.
2. GitHub repo variables `TF_STATE_BUCKET`, `AWS_PLAN_ROLE_ARN`, `AWS_APPLY_ROLE_ARN` (bootstrap outputs); repo secrets `TF_ADMIN_CIDRS`, `TF_ALERT_EMAIL`, `TF_SSH_PUBLIC_KEY`. Environment `prod`, limited to `main`. The roles trust the repo's immutable OIDC subject (`gh api repos/<owner>/<repo>/actions/oidc/customization/sub`): see `github_sub_prefixes` in `terraform/bootstrap/main.tf`.
3. Push to `main` touching `infra/` for a plan; apply it with Actions → terraform → Run workflow → that run's ID (it applies the saved plan only). Confirm the SNS email.
4. A `read:packages` token in `~/.config/ghost-ledger/ghcr.token`, then `make prod-secrets CONFIRM=prod` (writes the box's env files, never printed, and deploys).
5. Check with `make prod-status` and `make prod-logs`.

## Day to day

- **Deploy:** merge to `main`; the box pulls the image within 5 minutes.
- **Infra:** edit `terraform/prod`, push, read the plan, apply it by run ID.
- **Secrets:** `make prod-secrets CONFIRM=prod`. Env changes: pause the deploy timer, then a `FORCE=1` deploy.
- **Query prod:** `make prod-tunnel`, then `psql` on `localhost:15432`. CDC check: `scripts/check_ingress.py`.
- **deploy.sh** never updates itself: `make box-deploy-script CONFIRM=prod`.

## Deploy safety

- Images are built only after lint and every test pass, and must be signed by `ci.yml` on `main` (cosign).
- After a deploy a sweep must commit within ~2 minutes, or the box rolls back to `ghost-ledger-deploy:last-good` and won't retry that image. Manual rollback: `sudo env IMAGE=ghcr.io/sohammandal/ghost-ledger:<sha> FORCE=1 /usr/local/bin/ghost-ledger-deploy`.
- Migrations are never rolled back: expand, then contract; never rewrite or long-lock `bus_positions` or `raw_responses`; published tables change additively, and a new one ships in two deploys (Debezium include list first); never `TRUNCATE` or bulk `DELETE` a published table, drop partitions.

## Easy to break

- `user_data` runs only at first boot; changing it would replace the box and its database. It's appended to Lightsail's own `/bin/sh` script, and `${...}` is interpolated even in comments (`$${...}` for a literal).
- The box and the lake bucket have `prevent_destroy`. Ingress data files are locked for 10 years; nothing may delete them.
- Apply AWS changes before shipping code that needs them (a Debezium without its bucket crash-loops and holds WAL).

## Nightly trigger token

EventBridge starts the nightly at 06:23 CDT through GitHub's workflow_dispatch API; the token lives only in the EventBridge connection `ghost-ledger-github`. To rotate it: a fine-grained token for this repo only with **Actions: Read and write**, then

```bash
read -rs T && aws events update-connection --profile ghost-ledger --region us-east-2 \
  --name ghost-ledger-github --authorization-type API_KEY \
  --auth-parameters "{\"ApiKeyAuthParameters\":{\"ApiKeyName\":\"Authorization\",\"ApiKeyValue\":\"Bearer $T\"}}" \
  --query ConnectionState --output text; unset T
```

If it fails, the `report-stale` alarm emails after 10:00 CT.
