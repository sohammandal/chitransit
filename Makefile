.PHONY: box-deploy-script install lint test env guard up down psql migrate migrate-status new-migration db-reset check-db routes vehicles poll monitor image rehearse rehearse-logs rehearse-psql rehearse-down ssh-key tf-bootstrap tf-plan prod-ip prod-ssh prod-secrets prod-status prod-logs prod-tunnel transform transform-ci dashboard

# Environment: dev (laptop, default) or prod (Lightsail box). Prod needs CONFIRM=prod:
#   make migrate ENV=prod CONFIRM=prod
ENV ?= dev
ifeq ($(filter $(ENV),dev prod),)
$(error ENV must be dev or prod, got '$(ENV)')
endif
export GHOST_LEDGER_ENV := $(ENV)

# Prod layers compose.prod.yml over the shared base.
COMPOSE = docker compose -f compose.yml $(if $(filter prod,$(ENV)),-f compose.prod.yml)
PYTHON = uv run python
CLI = uv run ghost-ledger

install:            ## uv sync + git hooks
	uv sync
	uv run pre-commit install

lint:               ## ruff + sqlfluff + hygiene hooks on every file
	uv run pre-commit run --all-files

test:
	uv run pytest -q

env:                ## show resolved settings for ENV (secrets redacted)
	$(CLI) env

guard:              ## stop unless ENV=dev, or ENV=prod with CONFIRM=prod
	@if [ "$(ENV)" = "prod" ] && [ "$(CONFIRM)" != "prod" ]; then \
		echo "Refusing to run against prod. Re-run with ENV=prod CONFIRM=prod."; exit 1; fi

up: guard           ## start Postgres (logical replication on) and apply migrations
	$(COMPOSE) up -d --wait
	$(MAKE) migrate

down: guard
	$(COMPOSE) down

psql: guard
	$(COMPOSE) exec postgres psql -U ghost -d ghost_ledger

migrate: guard      ## apply pending infra/db/migrations (dbmate)
	$(COMPOSE) run --rm dbmate up

migrate-status: guard
	$(COMPOSE) run --rm dbmate status

new-migration:      ## make new-migration NAME=add_patterns (creates a file; dev only)
	docker compose -f compose.yml run --rm dbmate new $(NAME)

db-reset: guard     ## drop the local volume and rebuild from migrations (never prod)
	@if [ "$(ENV)" = "prod" ]; then echo "db-reset never runs against prod."; exit 1; fi
	$(COMPOSE) down -v
	$(MAKE) up

check-db: guard
	$(CLI) check-db

poll: guard         ## run the bus poller (make poll SWEEPS=4 to stop early)
	$(CLI) poll $(if $(SWEEPS),--sweeps $(SWEEPS))

monitor:            ## print the monitor's metrics once (dry run, never sends)
	$(CLI) monitor --dry-run --once

# ---------- prod image and local rehearsal of the prod stack (step 4a) ----------
# The rehearsal runs the real deploy path (infra/box/deploy.sh + compose.prod.yml) on the
# laptop under project ghost-ledger-rehearsal, with a throwaway env file in data/rehearsal/,
# 2 routes (shared API quota), metrics to the log, and no CDC (no AWS on the laptop).
REHEARSAL = $(CURDIR)/data/rehearsal
REHEARSAL_ENV = COMPOSE_PROFILES= IMAGE=ghost-ledger:local DIR=$(REHEARSAL) ENV_FILE=$(REHEARSAL)/prod.env \
	BIN=$(REHEARSAL)/ghost-ledger-deploy PROJECT=ghost-ledger-rehearsal SKIP_PULL=1 VERIFY=0
REHEARSAL_COMPOSE = COMPOSE_PROFILES= IMAGE=ghost-ledger:local ENV_FILE=$(REHEARSAL)/prod.env \
	docker compose -p ghost-ledger-rehearsal -f $(REHEARSAL)/compose.yml \
	-f $(REHEARSAL)/compose.prod.yml --env-file $(REHEARSAL)/prod.env

image:              ## build the prod image locally as ghost-ledger:local
	docker build --build-arg GIT_SHA=$$(git rev-parse HEAD) -t ghost-ledger:local .

rehearse: image     ## run the prod stack locally through deploy.sh (FORCE=1 to redeploy)
	@mkdir -p $(REHEARSAL)
	@if [ ! -f $(REHEARSAL)/prod.env ]; then \
		[ -n "$$CTA_BUS_TRACKER_API_KEY" ] || { echo "CTA_BUS_TRACKER_API_KEY not set"; exit 1; }; \
		pw=$$(openssl rand -hex 16); umask 077; \
		printf '%s\n' "GHOST_LEDGER_ENV=prod" "POSTGRES_PASSWORD=$$pw" \
			"DATABASE_URL=postgresql://ghost:$$pw@postgres:5432/ghost_ledger?sslmode=disable" \
			"CTA_BUS_TRACKER_API_KEY=$$CTA_BUS_TRACKER_API_KEY" \
			"POLL_ROUTES=79,9" "METRICS_SINK=log" > $(REHEARSAL)/prod.env; \
		echo "wrote $(REHEARSAL)/prod.env (throwaway password, 2 routes)"; fi
	$(REHEARSAL_ENV) FORCE=$(or $(FORCE),0) infra/box/deploy.sh

rehearse-logs:
	$(REHEARSAL_COMPOSE) logs --tail 20 migrate poller monitor

rehearse-psql:
	$(REHEARSAL_COMPOSE) exec postgres psql -U ghost -d ghost_ledger

rehearse-down:      ## stop the rehearsal and delete its data
	$(REHEARSAL_COMPOSE) down -v
	rm -rf $(REHEARSAL)

# ---------- prod infrastructure ----------
# AWS access from the laptop: `aws login --profile ghost-ledger` (12 h sessions). Terraform's
# S3 backend can't read `aws login` sessions yet, so export them as plain env credentials.
AWS_PROFILE_NAME ?= ghost-ledger
AWS_ENV = eval "$$(aws configure export-credentials --profile $(AWS_PROFILE_NAME) --format env)" \
	&& export AWS_REGION=us-east-2 &&
TF_PROD = $(AWS_ENV) terraform -chdir=infra/terraform/prod
PROD_IP = $$($(TF_PROD) output -raw box_ip)
PROD_SSH = ssh -i ~/.ssh/ghost_ledger ubuntu@$(PROD_IP)

ssh-key:            ## create the dedicated SSH key for the box (once)
	@[ -f ~/.ssh/ghost_ledger ] && echo "~/.ssh/ghost_ledger already exists" || \
		ssh-keygen -t ed25519 -f ~/.ssh/ghost_ledger -C ghost-ledger-lightsail -N ""

tf-bootstrap:       ## one-time: state bucket, GitHub OIDC, CI roles (local state, asks to approve)
	$(AWS_ENV) cd infra/terraform/bootstrap && terraform init && terraform apply

tf-plan:            ## plan prod from the laptop (needs backend.hcl + terraform.tfvars)
	$(TF_PROD) init -backend-config=backend.hcl && $(TF_PROD) plan

prod-ip:
	@$(TF_PROD) output -raw box_ip; echo

prod-ssh:
	$(PROD_SSH)

prod-secrets:       ## write prod.env + ghcr.env on the box, then deploy (CONFIRM=prod; safe to re-run)
	@[ "$(CONFIRM)" = prod ] || { echo "This writes secrets to the prod box. Re-run with CONFIRM=prod."; exit 1; }
	AWS_PROFILE=$(AWS_PROFILE_NAME) infra/box/push-secrets.sh $(PROD_IP)

box-deploy-script:  ## install infra/box/deploy.sh on the box (CONFIRM=prod; it never updates itself)
	@[ "$(CONFIRM)" = prod ] || { echo "This replaces the root-run deploy script on the prod box. Re-run with CONFIRM=prod."; exit 1; }
	$(PROD_SSH) 'sudo install -m 0755 /dev/stdin /usr/local/bin/ghost-ledger-deploy' < infra/box/deploy.sh

prod-status:        ## services, last deploy, and the monitor's latest metrics
	$(PROD_SSH) 'cd /opt/ghost-ledger && sudo docker compose -p ghost-ledger ps --format "table {{.Service}}\t{{.Status}}"; sudo journalctl -u ghost-ledger-deploy -n 3 --no-pager -o cat; sudo docker logs --tail 1 ghost-ledger-monitor-1'

prod-logs:          ## follow the poller on the box
	$(PROD_SSH) 'sudo docker logs -f --tail 20 ghost-ledger-poller-1'

prod-tunnel:        ## Postgres on the box at localhost:15432 (Ctrl-C to close)
	ssh -i ~/.ssh/ghost_ledger -N -L 15432:127.0.0.1:5432 ubuntu@$(PROD_IP)

# ---------- transform (dbt) ----------
# Dev: reads prod Ingress read-only through Glue, builds into transform/target (never
# publishes). Prod: the nightly workflow. VARS='{start_date: 2026-09-26, end_date: 2026-09-26}'
LAKE = s3://ghost-ledger-lake-354894954112

transform:          ## dbt build locally on prod Ingress (read-only), e.g. VARS='{start_date: ...}'
	$(AWS_ENV) cd transform && uv run --group transform dbt build --profiles-dir . $(if $(VARS),--vars "$(VARS)")

transform-ci:       ## what CI runs: every model with zero rows + all data and unit tests
	$(AWS_ENV) cd transform && GTFS_ROOT=$(LAKE)/gtfs DBT_DUCKDB_PATH=target/ci.duckdb uv run --group transform dbt build --profiles-dir . --empty

dashboard:          ## open the latest dashboard the nightly job published
	$(AWS_ENV) aws s3 cp $(LAKE)/analytics/site/index.html data/dashboard.html --only-show-errors && open data/dashboard.html

routes:             ## smoke-test the Bus Tracker key
	$(CLI) routes

vehicles:           ## live buses on route 79 (override: make vehicles RT="9 79")
	$(CLI) vehicles --rt $(or $(RT),79)
