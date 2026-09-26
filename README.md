# CTA Ghost Ledger

A replayable record of scheduled CTA service: which bus trips **ran**, which CTA **flagged as canceled**, and which **silently never showed up**, plus gaps and bunching. It's judged against the schedule version in force each day. 'L' trains follow once the Train Tracker key arrives.

Personal learning project built around *Designing Data-Intensive Applications* (2nd ed.). Not affiliated with CTA.

## Stack

Poller (Python) → Postgres (single system of record) → CDC (Debezium / Estuary) → Iceberg on S3 → dbt-duckdb → Dagster.
Lakehouse layers: **Ingress → Intermediate → Reporting**.

## Quickstart

```bash
make install     # uv sync + pre-commit hooks
make up          # Postgres 17 on :5433 with wal_level=logical
make check-db    # wal_level, ops tables, CDC publication
make routes      # smoke-test the Bus Tracker key (from ~/.secrets)
make vehicles    # live buses on route 79
make lint test
```

## Briefs and research

- Bus brief: `docs/briefs/bus-ghost-ledger.html` · [artifact](https://claude.ai/artifact/S4fbRE5PzLiqxX3NwJxFeY)
- L brief: `docs/briefs/l-ghost-ledger.html` · [artifact](https://claude.ai/artifact/EM673oAthRYV9DSXmc6hwd)
- Prior art and data research: `docs/research/`
