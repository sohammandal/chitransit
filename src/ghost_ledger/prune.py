"""Prune gate: drop a day's Postgres partition only when Ingress provably has all of it.

Usage (on the box): docker exec ghost-ledger-poller-1 python -m ghost_ledger.prune [--apply]

Postgres keeps ~2 days; Ingress (Iceberg) holds the full log and is never rebuilt. A UTC day
of bus_positions / raw_responses may be dropped only when all of these hold (fail closed:
anything missing or unreadable means keep):
  1. the day is at least KEEP_DAYS old;
  2. the nightly Ingress manifest (transform/ingress_counts.py) was computed after the day
     ended and lists the day;
  3. Ingress has exactly as many distinct keys for the day as the Postgres partition has
     rows (the primary key makes Postgres rows distinct);
  4. the replication slot's confirmed_flush_lsn is at or past the day's highest LSN in
     Ingress (Debezium acknowledged it only after the Iceberg commit; kill drill, Sep 26).
Drops are whole partitions (DETACH + DROP: no per-row deletes reach CDC, and the
publication never sees a TRUNCATE). Dry-run by default: it prints what it would do.
"""

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg

from ghost_ledger.config import get_settings

log = logging.getLogger("ghost_ledger.prune")

KEEP_DAYS = 2
TABLES = ("bus_positions", "raw_responses")  # must match transform/ingress_counts.py
SLOT = "ghost_ledger_prod"
MANIFEST = "s3://ghost-ledger-lake-354894954112/analytics/ops/ingress_day_counts.json"

# Daily partitions are named <table>_YYYYMMDD by ops.ensure_daily_partitions.
PARTITIONS = """
SELECT c.relname
FROM pg_inherits AS i
INNER JOIN pg_class AS c ON i.inhrelid = c.oid
INNER JOIN pg_class AS p ON i.inhparent = p.oid
INNER JOIN pg_namespace AS n ON p.relnamespace = n.oid
WHERE n.nspname = 'ops' AND p.relname = %s
ORDER BY c.relname
"""
SLOT_LSN = """
SELECT (confirmed_flush_lsn - '0/0'::PG_LSN)::BIGINT
FROM pg_replication_slots WHERE slot_name = %s
"""


@dataclass(frozen=True)
class Verdict:
    table: str
    day: date
    partition: str
    drop: bool
    reason: str


def decide(
    table: str,
    day: date,
    partition: str,
    pg_rows: int,
    manifest: dict[str, Any] | None,
    slot_lsn: int | None,
    today: date,
) -> Verdict:
    """Pure: the gate's decision for one partition from the facts gathered about it."""

    def keep(reason: str) -> Verdict:
        return Verdict(table, day, partition, False, reason)

    if (today - day).days < KEEP_DAYS:
        return keep(f"younger than {KEEP_DAYS} days")
    if manifest is None:
        return keep("no Ingress manifest")
    computed = datetime.fromisoformat(manifest["computed_at"])
    if computed < datetime.combine(day + timedelta(days=1), datetime.min.time(), UTC):
        return keep("manifest computed before the day ended")
    counts = manifest.get("tables", {}).get(table, {}).get(day.isoformat())
    if counts is None:
        return keep("day not in the Ingress manifest")
    if counts["keys"] != pg_rows:
        return keep(f"Ingress has {counts['keys']:,} keys, Postgres {pg_rows:,} rows")
    if slot_lsn is None:
        return keep(f"slot {SLOT} not found")
    if slot_lsn < counts["max_lsn"]:
        return keep("slot not yet past the day's last LSN")
    return Verdict(table, day, partition, True, f"{pg_rows:,} rows safely in Ingress")


def load_manifest(url: str) -> dict[str, Any] | None:
    try:
        if url.startswith("s3://"):
            import boto3  # only needed in prod

            bucket, key = url[5:].split("/", 1)
            obj = boto3.client("s3").get_object(Bucket=bucket, Key=key)
            return json.loads(obj["Body"].read())
        with open(url) as f:
            return json.load(f)
    except Exception as exc:  # noqa: BLE001 - any failure means keep everything
        log.warning("cannot read the Ingress manifest %s: %s", url, exc)
        return None


def gate(conn: psycopg.Connection, manifest: dict[str, Any] | None, today: date) -> list[Verdict]:
    row = conn.execute(SLOT_LSN, (SLOT,)).fetchone()
    slot_lsn = row[0] if row else None
    verdicts = []
    for table in TABLES:
        for (partition,) in conn.execute(PARTITIONS, (table,)).fetchall():
            day = date.fromisoformat(partition.rsplit("_", 1)[1])  # YYYYMMDD
            if day >= today:
                continue
            pg_rows = conn.execute(f"SELECT COUNT(*) FROM ops.{partition}").fetchone()[0]
            verdicts.append(decide(table, day, partition, pg_rows, manifest, slot_lsn, today))
    return verdicts


def drop(conn: psycopg.Connection, v: Verdict) -> None:
    with conn.transaction():
        conn.execute(f"ALTER TABLE ops.{v.table} DETACH PARTITION ops.{v.partition}")
        conn.execute(f"DROP TABLE ops.{v.partition}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="drop the partitions that pass")
    p.add_argument("--manifest", default=MANIFEST)
    args = p.parse_args()
    settings = get_settings()
    manifest = load_manifest(args.manifest)
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        for v in gate(conn, manifest, datetime.now(UTC).date()):
            action = ("DROP" if args.apply else "would drop") if v.drop else "keep"
            log.info("%s %s %s: %s", action, v.table, v.day, v.reason)
            if v.drop and args.apply:
                drop(conn, v)


if __name__ == "__main__":
    main()
