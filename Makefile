.PHONY: install lint test env guard up down psql migrate migrate-status new-migration db-reset check-db routes vehicles poll

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
	docker exec -it ghost-ledger-postgres psql -U ghost -d ghost_ledger

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

routes:             ## smoke-test the Bus Tracker key
	$(CLI) routes

vehicles:           ## live buses on route 79 (override: make vehicles RT="9 79")
	$(CLI) vehicles --rt $(or $(RT),79)
