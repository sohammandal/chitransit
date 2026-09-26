# Prod infrastructure runbook

Prod is one Lightsail box (us-east-2) running Postgres, the poller, the monitor and Debezium
(CDC to Iceberg on S3, Glue catalog), deployed by pulling `ghcr.io/sohammandal/ghost-ledger:main`. Everything in AWS is Terraform. Design and
reasons: `docs/PLAN.md` (local) → Infrastructure, CI/CD, Monitoring.

```
infra/
├── terraform/bootstrap/   # once, from the laptop, local state: state bucket, GitHub OIDC, CI roles
├── terraform/prod/        # the box, static IP, SSH firewall, box IAM user, SNS, alarms, lake bucket + Glue db
└── box/                   # setup.sh.tftpl (first boot), deploy.sh (pull-based deploy), push-secrets.sh
```

## First-time setup (in order)

1. **AWS CLI login** as an IAM admin user (not root) with `AdministratorAccess` +
   `SignInLocalDevelopmentAccess`: `aws login --profile ghost-ledger`, region `us-east-2`. Check:
   `aws sts get-caller-identity --profile ghost-ledger`. The Makefile exports these session
   credentials for Terraform, because Terraform's S3 backend can't read `aws login` sessions.
2. **SSH key** (once): `make ssh-key` → `~/.ssh/ghost_ledger`.
3. **Bootstrap**: `make tf-bootstrap` (shows the plan, asks to approve). Note the outputs:
   `state_bucket`, `ci_plan_role_arn`, `ci_apply_role_arn`.
4. **GitHub repo variables** (Settings → Secrets and variables → Actions → Variables):
   | Variable | Value |
   |---|---|
   | `TF_STATE_BUCKET` | bootstrap `state_bucket` |
   | `AWS_PLAN_ROLE_ARN` | bootstrap `ci_plan_role_arn` |
   | `AWS_APPLY_ROLE_ARN` | bootstrap `ci_apply_role_arn` |
   | `TF_ADMIN_CIDRS` | `["<your IP>/32"]` (`curl -s https://checkip.amazonaws.com`) |
   | `TF_ALERT_EMAIL` | where alarms go |
   | `TF_SSH_PUBLIC_KEY` | contents of `~/.ssh/ghost_ledger.pub` |
5. **GitHub environment** `prod`, deployments limited to the `main` branch (Settings →
   Environments). The apply role only trusts jobs in this environment. The trust uses the repo's
   immutable OIDC subject (`gh api repos/<owner>/<repo>/actions/oidc/customization/sub`); if it
   ever changes, update `github_sub_prefix` in `terraform/bootstrap/main.tf`.
6. **Plan**: push to `main` touching `infra/` (or Actions → terraform → Run workflow with the
   run ID left empty). Read the plan in that run's log; note the **run ID** (in its URL).
   From the laptop instead: copy `prod/backend.hcl.example` → `backend.hcl` and
   `prod/terraform.tfvars.example` → `terraform.tfvars`, then `make tf-plan`.
7. **Apply exactly that plan**: Actions → terraform → Run workflow → paste the run ID. The job
   checks it was a successful `main` run, shows the plan, and applies that saved file (never
   a fresh plan; Terraform refuses it if state changed since). The box boots, installs Docker,
   and its deploy timer waits for secrets.
8. **Confirm the SNS email** AWS sends to `TF_ALERT_EMAIL`. No alarm is delivered until then.
9. **GitHub token for the box**: a fine-grained or classic token with `read:packages`, saved to
   `~/.config/ghost-ledger/ghcr.token` (mode 0600). The image is private.
10. **Secrets + first deploy**: `make prod-secrets CONFIRM=prod`. Writes `/etc/ghost-ledger/prod.env`
    and `ghcr.env` over SSH (never printed), creates the box's AWS key, and deploys.
11. **Check**: `make prod-status`, `make prod-logs`, and the `GhostLedger/prod` metrics in CloudWatch.
12. **Alarm drill**: `make prod-ssh`, then pause the deploy timer first (it would restart the
    poller within 5 minutes, before the alarm fires):
    `sudo systemctl stop ghost-ledger-deploy.timer && sudo docker stop ghost-ledger-poller-1`.
    The `poller-stale` alarm emails within ~7 minutes. Then
    `sudo systemctl start ghost-ledger-deploy.timer ghost-ledger-deploy` brings the poller back;
    the OK email follows.
13. **Share the API quota**: set `POLL_ROUTES=79,9` in `.env.dev` so dev and prod don't both
    poll everything.

## Day to day

- **Deploy**: merge to `main`. CI tests, pushes the image; the box picks it up within 5 minutes.
- **Change infra**: edit `terraform/prod`, push, read the plan, then run the workflow with that
  plan's run ID.
- **Rotate secrets / fix the env file**: `make prod-secrets CONFIRM=prod` (keeps the Postgres
  password and a still-active AWS key, then force-redeploys so containers read the new file).
  It never deletes an active AWS key; after a rotation, delete the old one once prod is healthy.
- **Query prod**: `make prod-tunnel`, then `psql postgresql://ghost:<pw>@localhost:15432/ghost_ledger`.
- **Check CDC**: with the tunnel open, `scripts/check_ingress.py` (usage in its docstring) compares
  Ingress (Iceberg via Glue, read with DuckDB) against Postgres and rebuilds `bus_assignments`
  from the change log. Debezium runs under compose profile `cdc`, which `deploy.sh` enables; its
  config is `cdc/debezium/application.prod.properties`, and its offsets live in Iceberg, so a
  restart resumes where it stopped.
- **Stop CDC on purpose** (drill): pause the timer first, or deploy.sh restarts Debezium within
  5 minutes: `sudo systemctl stop ghost-ledger-deploy.timer && sudo docker stop ghost-ledger-debezium-1`.
  The slot keeps WAL (capped at 2 GB); `cdc-slot-inactive` emails after 10 minutes.

## Deploy safety

- **CI gate:** the prod image is built only after lint and all tests pass (the tests run every
  migration against Postgres 17). Each image is tagged `main` and with its commit SHA.
- **Health gate + auto-rollback** (`deploy.sh`): after a deploy a sweep must commit within
  ~2 minutes. If none does, the box redeploys its local `ghost-ledger-deploy:last-good` image
  (with that image's compose files), remembers the bad image id in `/opt/ghost-ledger/.bad-image`
  and won't redeploy it; the next CI image replaces it. The log line starts `deploy: UNHEALTHY`.
- **Manual rollback:** `make prod-ssh`, then
  `sudo env IMAGE=ghcr.io/sohammandal/ghost-ledger:<sha> FORCE=1 /usr/local/bin/ghost-ledger-deploy`
  (the timer goes back to `:main` on its next run, so also revert on main).
- **Migrations are never rolled back automatically.** A failed migration undoes itself (dbmate
  runs each in a transaction); a successful one stays, so every migration must work with the
  code already running:
  - **Expand, then contract.** Add tables/columns first; drop or rename only in a later deploy,
    once no running code uses the old shape.
  - **Never rewrite or long-lock a big table** (`bus_positions`, `raw_responses`): no column type
    changes, no non-concurrent index builds, no `ADD COLUMN ... DEFAULT <volatile>`.
  - **Published tables change additively** (Debezium and Iceberg add columns; drops and renames
    need a plan). A new published table ships in two deploys: the Debezium include list first,
    then the migration that adds it to the publication.
  - Never `TRUNCATE` or bulk `DELETE` a published table (both stream to Ingress); drop partitions.

## Things that are easy to break

- **Never change `user_data` expecting an effect**: it's ignored after first boot (changing it
  would otherwise replace the instance and its database). `deploy.sh` updates itself from images.
- **Lightsail user_data is a shell script, not cloud-config**: Lightsail appends it to its own
  `#!/bin/sh` launch script. `setup.sh.tftpl` keeps the outer part POSIX sh with no `exit`, and
  runs the real setup (bash) from a file; its log is `/var/log/ghost-ledger-setup.log`.
- **Template escapes**: in `setup.sh.tftpl` any `${...}` is interpolated, even in comments.
  Write `$${...}` for a literal; `tests/test_box_templates.py` enforces this.
- **The instance and the lake bucket have `prevent_destroy`**. Destroying either means removing
  that on purpose. The lake is versioned (old versions kept 30 days); the box can't delete tables.
- **Deploy order for AWS-dependent services**: apply the Terraform first, then ship the compose
  change. A Debezium that starts before its bucket/catalog exist creates a slot, crash-loops and
  holds WAL.
- **`docker logs` spans container restarts**: filter with `--since` when checking a restart.
