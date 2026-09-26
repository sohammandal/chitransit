.PHONY: install lint test env guard up down psql migrate migrate-status new-migration db-reset check-db routes vehicles poll monitor image rehearse rehearse-logs rehearse-psql rehearse-down

# Environment: dev (laptop, default) or prod (Lightsail box). Prod needs CONFIRM=prod:
#   make migrate ENV=prod CONFIRM=prod
ENV ?= dev
ifeq ($(filter $(ENV),dev prod),)
$(error ENV must be dev or prod, got '$(ENV)')
endif
export GHOST_LEDGER_ENV := $(ENV)

# Prod layers compose.prod.yml (week 2) over the shared base.
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

migrate: guard      ## apply pending db/migrations (dbmate)
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
# 2 routes (shared API quota) and metrics to the log.
REHEARSAL = $(CURDIR)/data/rehearsal
REHEARSAL_ENV = IMAGE=ghost-ledger:local DIR=$(REHEARSAL) ENV_FILE=$(REHEARSAL)/prod.env \
	BIN=$(REHEARSAL)/ghost-ledger-deploy PROJECT=ghost-ledger-rehearsal SKIP_PULL=1
REHEARSAL_COMPOSE = IMAGE=ghost-ledger:local ENV_FILE=$(REHEARSAL)/prod.env \
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

routes:             ## smoke-test the Bus Tracker key
	$(CLI) routes

vehicles:           ## live buses on route 79 (override: make vehicles RT="9 79")
	$(CLI) vehicles --rt $(or $(RT),79)
