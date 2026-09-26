# The prod artifact: the poller, the monitor, the migrations (+ dbmate) and the files the
# box needs to run them (compose files, deploy.sh). The box pulls this image and extracts
# /app/deploy from it, so a merge to main can change how prod runs without SSH.

FROM ghcr.io/astral-sh/uv:0.8-python3.13-bookworm-slim AS build
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
# Dependencies first, so code changes don't reinstall them
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM python:3.13-slim-bookworm
COPY --from=ghcr.io/amacneil/dbmate:2 /usr/local/bin/dbmate /usr/local/bin/dbmate
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=build --chown=app:app /app/.venv /app/.venv
COPY db ./db
COPY compose.yml compose.prod.yml infra/box/deploy.sh ./deploy/
COPY cdc/debezium/application.prod.properties ./deploy/cdc/
ARG GIT_SHA=unknown
ENV PATH="/app/.venv/bin:$PATH" \
    GHOST_LEDGER_ENV=prod \
    GIT_SHA=$GIT_SHA \
    DBMATE_MIGRATIONS_DIR=/app/db/migrations \
    DBMATE_NO_DUMP_SCHEMA=true \
    DBMATE_WAIT=true \
    PYTHONUNBUFFERED=1
USER app
ENTRYPOINT ["ghost-ledger"]
CMD ["poll"]
