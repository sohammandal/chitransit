# CTA Ghost Ledger

[![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/badge/uv-DE5FE9?logo=uv&logoColor=white)](https://github.com/astral-sh/uv)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Debezium](https://img.shields.io/badge/CDC-Debezium-91D443)](https://debezium.io/)
[![Apache Iceberg](https://img.shields.io/badge/Apache%20Iceberg-3C8CE7?logo=apache&logoColor=white)](https://iceberg.apache.org/)
[![DuckDB](https://img.shields.io/badge/DuckDB-FFF000?logo=duckdb&logoColor=black)](https://duckdb.org/)
[![dbt](https://img.shields.io/badge/dbt-FF694B)](https://www.getdbt.com/)
[![Terraform](https://img.shields.io/badge/Terraform-844FBA?logo=terraform&logoColor=white)](https://www.terraform.io/)
[![Ruff](https://img.shields.io/badge/Ruff-D7FF64?logo=ruff&logoColor=black)](https://github.com/astral-sh/ruff)

> A replayable record of scheduled CTA service - which bus trips **ran**, which CTA **flagged as canceled**, and which **silently never showed up** - judged against the schedule that was in force each day.

A learning project built around *Designing Data-Intensive Applications* (2nd ed.): change data capture, event time vs processing time, slowly changing dimensions, idempotent reprocessing and a lakehouse on object storage, applied to a real Chicago problem. 'L' trains follow once the Train Tracker key arrives.

*Not affiliated with the Chicago Transit Authority. Data provided by CTA.*

---

## Problem

CTA reports that buses delivered 98.8% of scheduled trips in June 2025, and since May 2025 it flags canceled trips in rider apps. But a high delivery rate can still mean two buses together and then a 25-minute gap, and a cancellation flag only helps if every missing trip actually gets one.

This project answers questions the public data can't:

- What share of missing trips does CTA **flag** as canceled, and how many vanish **silently**?
- Which routes deliver their trips but **bunch** them, leaving long gaps at rush hour?
- How does service compare across routes, neighborhoods and hours, measured against **that day's** schedule?

Others have built parts of this (see [Prior art](#prior-art)). The new piece is auditing CTA's cancellation flags against observed service, trip by trip.

---

## Architecture

The poller writes to exactly one place. Everything downstream is derived from Postgres's change log (DDIA ch. 12-13: one system of record, no dual writes).

```mermaid
flowchart LR
    BT[CTA Bus Tracker API<br/>every 30 s] --> P
    subgraph BOX[Lightsail box, 24/7]
        P[Poller] -->|one transaction per sweep| PG[(Postgres 17<br/>raw_responses, sweeps, raw_polls,<br/>bus_positions, assignments, patterns)]
        PG -->|logical replication| CDC[Debezium Server]
    end
    CDC -->|every 5 min| ING[Ingress<br/>Iceberg on S3 + Glue]
    GTFS[GTFS versions<br/>Parquet on S3] --> INT
    ING --> INT[Intermediate<br/>dbt-duckdb, nightly]
    INT --> AN[Analytics<br/>trip status, delivery, gaps, bunching]
    AN --> OUT[Parquet on S3 + dashboard]
```

| Layer | What lives there |
|-------|------------------|
| **Postgres (`ops`)** | The system of record. Every API response is stored byte for byte (`raw_responses`), next to the parsed rows built from it, in one transaction per 30 s sweep; Postgres decides their order (the LSN). Keeps about two days. |
| **Ingress** | The complete change log as Iceberg tables on S3 (Glue catalog): one row per change with its operation and log position. After two days it is the only copy, so it is protected and never rebuilt. |
| **Intermediate** | Schedule and route-pattern versions (SCD2), scheduled trips, interpolated stop passes, flagged cancellations |
| **Analytics** | `trip_status` (every scheduled trip: ran / flagged / silent / unknown), `route_hour_delivery`, `stop_hour_service` (headways, gaps, bunching), published as Parquet partitioned by service date |

Each scheduled trip lands in one of three buckets: **ran**, **flagged canceled** (CTA's `dyn=1`), or **silent ghost** (never flagged, never seen), counted only when the feed was healthy. Trips on detour patterns are counted as unknown, never as ghosts.

---

## Tech Stack

| Layer | Tool | Status |
|-------|------|--------|
| Ingestion | Python poller (`httpx`, `psycopg`) | Live |
| System of record | PostgreSQL 17, logical replication | Live |
| Change data capture | Debezium Server + Iceberg sink (no Kafka) | Live |
| Lakehouse | Apache Iceberg on Amazon S3, AWS Glue catalog | Live (Ingress) |
| Hosting | AWS Lightsail 2 GB, Docker Compose, pull-based deploys with a health gate | Live |
| Infrastructure | Terraform; GitHub Actions (tests, image, plan/apply via OIDC) | Live |
| Monitoring | CloudWatch metrics and alarms, email via SNS | Live |
| Cancellation flags | `getpredictions` sampled at ~730 stops every 10 min | Live |
| Transformation | dbt + DuckDB, reading Iceberg through Glue; unit and data tests | Live (nightly) |
| Orchestration | GitHub Actions nightly workflow (OIDC, replayable by date range) | Live |
| Dashboard | Static HTML rebuilt nightly | Live |
| Dev tools | `uv`, `ruff`, `sqlfluff`, `pre-commit`, `pytest` | Ready |

Cost: about $14-17/month (a $12 Lightsail box plus S3). Managed Postgres free tiers and Estuary were ruled out for 24/7 running at bus volume (~2.6M sightings/day).

---

## Data Sources

| Source | Used for |
|--------|----------|
| [CTA Bus Tracker API v3](https://www.transitchicago.com/developers/bustracker/) | Live positions (`getvehicles`), route patterns (`getpatterns`), cancellation flags (`getpredictions`) |
| [CTA static GTFS](https://www.transitchicago.com/developers/gtfs/) | The current schedule |
| [Transitland archive](https://www.transit.land/feeds/f-dp3-cta) | ~100 historical schedule versions since 2021 |
| [Ghost Buses / CTA StopWatch](https://github.com/mansueto-institute/cta-stop-watch) | 5-minute bus history (2022 to today) for backfill and cross-checks |

---

## Roadmap

- [x] **Week 1 - Ingest** - every route every 30 s into Postgres in one transaction per sweep; every API response kept; trips match GTFS on (route, pattern, service date, start); prod box, Terraform, CI/CD, alarms. *24-hour score pending.*
- [ ] **Week 2 - CDC to lakehouse** - Debezium → Iceberg on S3 live, reconciled with Postgres, freshness alarm on; still to do: one-hour outage drill, prune gate, Iceberg snapshot expiry and compaction
- [x] **Week 3 - Schedules, flags, stop passes** - cancellation sampler, GTFS versions, trip matching incl. detours, stop-pass interpolation
- [x] **Week 4 - Metrics and dashboard** - trip status, delivery, gaps, bunching; tested; nightly publish; dashboard
- [ ] **Later - 'L' trains** - second poller once the Train Tracker key arrives

---

## Project Structure

```
cta-ghost-ledger/
├── src/ghost_ledger/           # The one Python package
│   ├── bustracker.py           # Bus Tracker v3 client: retries, DST-safe timestamps
│   ├── config.py               # dev/prod settings (GHOST_LEDGER_ENV), quota guard
│   ├── ingest/                 # Poller: sweeps into Postgres, one transaction each
│   ├── monitor.py              # Timeliness metrics for CloudWatch alarms
│   └── cli.py                  # ghost-ledger env | routes | vehicles | poll | monitor | check-db
├── db/migrations/              # Postgres schema as dbmate migrations (make migrate)
├── cdc/debezium/               # Debezium Server config (dev: local Iceberg; prod: S3 + Glue)
├── infra/                      # Terraform (bootstrap, prod), box setup + deploy.sh; runbook in infra/README.md
├── .github/workflows/          # CI: lint, tests with Postgres, image to GHCR; Terraform plan/apply
├── transform/                  # dbt project (staging, intermediate, analytics), GTFS loader, publish, dashboard
├── scripts/                    # Checks (check_run.py, check_ingress.py) and spikes; not imported
├── tests/                      # pytest
├── data/                       # Local data (gitignored)
├── compose.yml                 # Postgres 17 (wal_level=logical, port 5433), dbmate, Debezium (profile cdc)
├── compose.prod.yml            # Prod layer: tuned Postgres on 127.0.0.1, migrate, poller, monitor, Debezium
├── Dockerfile                  # The prod image (code, migrations, dbmate, deploy files)
├── Makefile                    # Common commands
├── pyproject.toml              # Project, ruff and pytest config
├── .sqlfluff                   # SQL lint rules
├── .pre-commit-config.yaml     # Hygiene, ruff and sqlfluff hooks
├── .env.dev                    # Committed dev defaults (no secrets)
└── .env.example                # Every setting, documented
```

Folders follow the pipeline and appear when their stage starts.

---

## Local Development

1. **Clone the repo:**

   ```bash
   git clone git@github.com:sohammandal/cta-ghost-ledger.git
   cd cta-ghost-ledger
   ```

2. **Get a Bus Tracker API key** from your [Bus Tracker account](https://www.ctabustracker.com/) (My Account → Developer API) and export it, for example in `~/.secrets`:

   ```bash
   export CTA_BUS_TRACKER_API_KEY="..."
   ```

   > In dev, environment variables win over `.env`, which wins over the committed `.env.dev`. See `.env.example` for every setting. Never commit keys.

3. **Install [uv](https://github.com/astral-sh/uv):**

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

4. **Install dependencies and git hooks:**

   ```bash
   make install        # uv sync + pre-commit install
   ```

5. **Start Postgres** (Docker required):

   ```bash
   make up             # Postgres 17 on localhost:5433, then apply migrations
   make check-db       # wal_level, ops tables, CDC publication, partitions
   ```

6. **Smoke-test the API:**

   ```bash
   make routes         # lists bus routes
   make vehicles       # live buses on route 79 (or: make vehicles RT="9 79")
   ```

7. **Lint and test:**

   ```bash
   make lint           # pre-commit on all files (ruff, sqlfluff, hygiene)
   make test
   ```

Other commands: `make psql` (shell into Postgres), `make migrate-status`, `make new-migration NAME=...`, `make db-reset` (drop the local database and rebuild it from migrations), `make down`.

Schema changes always go in a new migration under `db/migrations/`; never edit an applied one.

### Dev and prod

The same code runs in two environments, chosen by `GHOST_LEDGER_ENV` (default `dev`):

| | dev | prod |
|---|---|---|
| Where | your laptop, Docker | the Lightsail box |
| Settings | environment > `.env` > `.env.dev` | environment only (the box's env file); prod never reads repo files |
| Make | `make migrate` | `make migrate ENV=prod CONFIRM=prod` (refuses without `CONFIRM`; `db-reset` always refuses) |
| Compose | `compose.yml` | `compose.yml` + `compose.prod.yml` |

`make env` prints the resolved settings with secrets redacted, including the daily API-call estimate. `make poll` runs the poller and `make monitor` prints the monitor's metrics once.

**Rehearsing prod locally:** `make rehearse` builds the image and runs the real deploy path (`infra/box/deploy.sh` + `compose.prod.yml`) as project `ghost-ledger-rehearsal`, with a throwaway env file in `data/rehearsal/`, two routes and metrics to the log. `make rehearse-logs`, `make rehearse-psql`, `make rehearse-down`.

**Deploying:** prod is pull-based. CI pushes `ghcr.io/sohammandal/ghost-ledger:main` (and a commit-SHA tag) only after tests pass on `main`; a timer on the box runs `deploy.sh` every 5 minutes, which redeploys only when the image changed. After each deploy a sweep must commit within ~2 minutes, or the box rolls back to its last good image. Migration rules and the manual rollback are in [infra/README.md](infra/README.md#deploy-safety). Dev and prod share one API key (100k calls/day), so dev polls only `79,9` (`.env.dev`).

**CDC in dev:** `docker compose --profile cdc up -d debezium` streams the dev database into Iceberg tables under `data/lake/`.

### Transform (dbt)

The batch layer is derived only from Ingress and the GTFS versions, for a window of service dates (default: the last 3), so any run can be repeated or replayed.

- **Dev:** `make transform` (optionally `VARS='{start_date: 2026-09-26, end_date: 2026-09-26}'`) builds every model into `transform/target/ghost_ledger.duckdb`, reading prod Ingress **read-only** through Glue. Nothing is published. Inspect with `dbt show` or DuckDB.
- **CI:** every push and PR runs `dbt build --empty` with a read-only role: all models compile against the real sources, and the data tests and business-rule unit tests run.
- **Prod:** `.github/workflows/nightly.yml` at 06:00 CT: load a new GTFS feed if CTA published one, check Ingress freshness, `dbt build`, publish the analytics tables to `s3://…/analytics/` (each service date's partition is replaced), rebuild the dashboard (`make dashboard` opens it).
- **Changing a rule:** edit the model and its unit test, run `make transform` on recent days and compare, push (CI), then rerun the nightly workflow by hand with a `start_date`/`end_date` range to rebuild history from Ingress.

### What Happens If Hooks Fail?

* If a hook auto-fixes files (e.g. formatting with `ruff` or `sqlfluff`), the commit will be blocked.
* Simply **`git add .` and commit again** - your files will now be fixed and pass.
* If errors remain (like lint violations), you must resolve them manually before committing.

### Adding a New Package/Dependency

```bash
uv add <package-name>               # runtime dependency
uv add --group dev <package-name>   # dev tooling
```

Commit the updated `pyproject.toml` and `uv.lock`; others run `uv sync`.

---

## Prior art

This builds on, and borrows from, earlier Chicago transit work:

- [Chi Hack Night Ghost Buses](https://github.com/chihacknight/chn-ghost-buses) and [Mansueto CTA StopWatch](https://github.com/mansueto-institute/cta-stop-watch) - scheduled vs observed buses, stop-pass interpolation, historical GTFS
- [chicago-transit-insights](https://github.com/cailinpitt/chicago-transit-insights) - live bunching, gap and ghost detection
- [Ghost Bus Index](https://github.com/dulcetberg/us-ghost-buses) - multi-city ghost rates and feed validity tests
- [CTA Reliability Tracker](https://github.com/brandonmcfadd/cta-reliability) - scheduled vs actual 'L' trains, 2022-2026
- [MBTA LAMP](https://github.com/mbta/lamp) and [TIDES](https://tides-transit.github.io/TIDES/main/tables/) - event models and table standards

---

## License

[MIT](LICENSE)
