"""Reconcile Ingress (Iceberg on S3, Glue catalog) against prod Postgres.

Usage (laptop, with `make prod-tunnel` open in another shell):
  eval "$(aws configure export-credentials --profile ghost-ledger --format env)"
  DATABASE_URL=postgresql://ghost:<pw>@localhost:15432/ghost_ledger?sslmode=disable \\
    uv run --with duckdb python scripts/check_ingress.py [--lag-minutes 10]

For the insert-only tables, rows older than the cutoff must appear in Ingress under the same
distinct keys as in Postgres, and nothing may land twice (e.g. after a CDC restart): no
repeated key, and no repeated `__lsn` among streamed changes (snapshot rows, `__op = 'r'`,
all carry the snapshot's one LSN). Then rebuild `bus_assignments` from its change log alone
and diff it against Postgres (DDIA ch. 12: state is the integral of the stream). Prototype of
the Dagster integrity check (week 4) and the prune gate.
"""

import argparse
import os
from datetime import UTC, datetime, timedelta

import boto3
import duckdb
import psycopg

GLUE_DATABASE = "ghost_ledger_ingress"
REGION = "us-east-2"
# table -> (key columns, timestamp column the cutoff applies to)
INSERT_ONLY = {
    "sweeps": ("sweep_id", "started_at"),
    "raw_polls": ("poll_id", "fetched_at"),
    "raw_responses": ("poll_id, fetched_at", "fetched_at"),
    "bus_positions": ("vid, observed_at", "fetched_at"),
}
ASSIGNMENT_FIELDS = "vid, rt, direction, stsd, stst, pid"

# Current bus_assignments = each vid's latest change: streamed changes (in LSN order) after
# the snapshot rows, which all share the snapshot's LSN; a latest change that is a delete
# means the bus has no assignment.
REBUILD_ASSIGNMENTS = f"""
SELECT {ASSIGNMENT_FIELDS} FROM (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY vid ORDER BY __op <> 'r' DESC, __lsn DESC
    ) AS newest
    FROM {{log}}
)
WHERE newest = 1 AND __op <> 'd' AND __deleted IS DISTINCT FROM 'true'
"""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--lag-minutes", type=int, default=10, help="skip rows newer than this")
    args = p.parse_args()
    cutoff = datetime.now(UTC) - timedelta(minutes=args.lag_minutes)

    glue = boto3.client("glue", region_name=REGION)
    lake = duckdb.connect()
    lake.execute("INSTALL iceberg; LOAD iceberg; INSTALL httpfs; LOAD httpfs;")
    lake.execute(f"CREATE SECRET (TYPE s3, PROVIDER credential_chain, REGION '{REGION}')")

    def scan(table: str) -> str:
        t = glue.get_table(DatabaseName=GLUE_DATABASE, Name=f"ghost_ops_{table}")["Table"]
        return f"iceberg_scan('{t['Parameters']['metadata_location']}')"

    ok = True
    print(f"cutoff {cutoff:%Y-%m-%d %H:%M:%S} UTC")
    with psycopg.connect(os.environ["DATABASE_URL"]) as pg:
        for table, (key, ts) in INSERT_ONLY.items():
            rows, keys, dup_lsn = lake.execute(
                f"SELECT COUNT(*), COUNT(DISTINCT ({key})), "
                "COUNT(*) FILTER (WHERE __op <> 'r') "
                "- COUNT(DISTINCT __lsn) FILTER (WHERE __op <> 'r') "
                f"FROM {scan(table)} WHERE {ts} < ?",
                [cutoff],
            ).fetchone()
            source = pg.execute(
                f"SELECT COUNT(*) FROM ops.{table} WHERE {ts} < %s", (cutoff,)
            ).fetchone()[0]
            good = keys == source == rows and dup_lsn == 0
            ok &= good
            print(
                f"{table:<16} postgres {source:>8}  ingress keys {keys:>8}  rows {rows:>8}  "
                f"repeated LSNs {dup_lsn:>4}  {'OK' if good else 'MISMATCH'}"
            )
        ok &= check_assignments(lake, pg, scan("bus_assignments"))
    raise SystemExit(0 if ok else 1)


def check_assignments(lake: duckdb.DuckDBPyConnection, pg: psycopg.Connection, log: str) -> bool:
    """Rebuilt state must equal Postgres, except for changes newer than the log's watermark."""
    changes = dict(lake.execute(f"SELECT __op, COUNT(*) FROM {log} GROUP BY 1").fetchall())
    watermark_ns = lake.execute(f"SELECT MAX(__source_ts_ns) FROM {log}").fetchone()[0]
    watermark = datetime.fromtimestamp(watermark_ns / 1e9, UTC)
    rebuilt = {r[0]: r for r in lake.execute(REBUILD_ASSIGNMENTS.format(log=log)).fetchall()}
    source = {
        r[0]: r
        for r in pg.execute(f"SELECT {ASSIGNMENT_FIELDS}, updated_at FROM ops.bus_assignments")
    }
    same = newer = 0
    unexplained = []
    for vid, row in source.items():
        if rebuilt.get(vid) == row[:-1]:
            same += 1
        elif row[-1] > watermark:
            newer += 1  # changed after the last change Ingress has seen
        else:
            unexplained.append((vid, row[:-1], rebuilt.get(vid)))
    # in Ingress but not Postgres: deleted after the watermark (deletes carry no time)
    deleted_since = len(rebuilt.keys() - source.keys())
    print(
        f"bus_assignments  changes {changes}; rebuilt {len(rebuilt)} vs postgres {len(source)} "
        f"as of watermark {watermark:%H:%M:%S} UTC: {same} identical, {newer} changed since, "
        f"{deleted_since} gone since, {len(unexplained)} unexplained"
    )
    for vid, want, got in unexplained[:10]:
        print(f"  {vid}: postgres {want} ingress {got}")
    return not unexplained


if __name__ == "__main__":
    main()
