.PHONY: install lint test up down psql db-reset check-db routes vehicles

PYTHON = uv run python
CLI = uv run ghost-ledger

install:            ## uv sync + git hooks
	uv sync
	uv run pre-commit install

lint:               ## ruff + sqlfluff + hygiene hooks on every file
	uv run pre-commit run --all-files

test:
	uv run pytest -q

up:                 ## start Postgres (logical replication on, port 5433)
	docker compose up -d --wait

down:
	docker compose down

psql:
	docker exec -it ghost-ledger-postgres psql -U ghost -d ghost_ledger

db-reset:           ## drop the volume and re-run sql/postgres init scripts
	docker compose down -v
	docker compose up -d --wait

check-db:
	$(CLI) check-db

routes:             ## smoke-test the Bus Tracker key
	$(CLI) routes

vehicles:           ## live buses on route 79 (override: make vehicles RT="9 79")
	$(CLI) vehicles --rt $(or $(RT),79)
