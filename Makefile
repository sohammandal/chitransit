.PHONY: install lint test up down psql migrate migrate-status new-migration db-reset check-db routes vehicles

PYTHON = uv run python
CLI = uv run ghost-ledger

install:            ## uv sync + git hooks
	uv sync
	uv run pre-commit install

lint:               ## ruff + sqlfluff + hygiene hooks on every file
	uv run pre-commit run --all-files

test:
	uv run pytest -q

up:                 ## start Postgres (logical replication on, port 5433) and apply migrations
	docker compose up -d --wait
	$(MAKE) migrate

down:
	docker compose down

psql:
	docker exec -it ghost-ledger-postgres psql -U ghost -d ghost_ledger

migrate:            ## apply pending db/migrations (dbmate)
	docker compose run --rm dbmate up

migrate-status:
	docker compose run --rm dbmate status

new-migration:      ## make new-migration NAME=add_patterns
	docker compose run --rm dbmate new $(NAME)

db-reset:           ## drop the local volume and rebuild the schema from migrations
	docker compose down -v
	$(MAKE) up

check-db:
	$(CLI) check-db

routes:             ## smoke-test the Bus Tracker key
	$(CLI) routes

vehicles:           ## live buses on route 79 (override: make vehicles RT="9 79")
	$(CLI) vehicles --rt $(or $(RT),79)
