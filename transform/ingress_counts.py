"""Per-UTC-day counts of the day-partitioned tables in Ingress: the prune gate's evidence.

Usage: uv run --group transform python transform/ingress_counts.py
    --to s3://ghost-ledger-lake-.../analytics/ops/ingress_day_counts.json [--days 7]

For bus_positions (partitioned by observed_at) and raw_responses (by fetched_at): rows,
distinct keys and the highest LSN per UTC day, for the last `days` complete UTC days. The
box's prune gate (`python -m ghost_ledger.prune`) compares them with its Postgres partitions
and drops a day only when Ingress has every key and the slot is past the day's last LSN.
Runs nightly (it has Glue/S3 access; the box is too small to scan Iceberg).
"""

import argparse
import json
from datetime import UTC, date, datetime, timedelta

import boto3
import duckdb

GLUE_DATABASE = "ghost_ledger_ingress"
REGION = "us-east-2"
# table -> (key columns, partition column); must match db/migrations and ghost_ledger.prune
TABLES = {
    "bus_positions": ("vid, observed_at", "observed_at"),
    "raw_responses": ("poll_id, fetched_at", "fetched_at"),
}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--to", required=True, help="s3://bucket/key.json or a local path")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--until", help="first UTC day NOT counted (YYYY-MM-DD); default today")
    args = p.parse_args()
    now = datetime.now(UTC)
    today = date.fromisoformat(args.until) if args.until else now.date()
    since = today - timedelta(days=args.days)

    glue = boto3.client("glue", region_name=REGION)
    con = duckdb.connect()
    con.execute("INSTALL iceberg; LOAD iceberg; INSTALL httpfs; LOAD httpfs;")
    con.execute(f"CREATE SECRET (TYPE s3, PROVIDER credential_chain, REGION '{REGION}')")
    con.execute("SET TimeZone = 'UTC'")

    out: dict = {"computed_at": now.isoformat(timespec="seconds"), "tables": {}}
    for table, (key, part) in TABLES.items():
        meta = glue.get_table(DatabaseName=GLUE_DATABASE, Name=f"ghost_ops_{table}")
        loc = meta["Table"]["Parameters"]["metadata_location"]
        rows = con.execute(
            f"SELECT CAST({part} AS DATE) AS day, COUNT(*), COUNT(DISTINCT ({key})), MAX(__lsn) "
            f"FROM iceberg_scan('{loc}') WHERE {part} >= ? AND {part} < ? GROUP BY 1 ORDER BY 1",
            [since, today],
        ).fetchall()
        out["tables"][table] = {
            str(day): {"rows": n, "keys": k, "max_lsn": int(lsn)} for day, n, k, lsn in rows
        }
        for day, n, k, _ in rows:
            print(f"{table:<14} {day}  rows {n:>9,}  keys {k:>9,}")

    body = json.dumps(out, indent=1)
    if args.to.startswith("s3://"):
        bucket, key = args.to[5:].split("/", 1)
        boto3.client("s3", region_name=REGION).put_object(
            Bucket=bucket, Key=key, Body=body, ContentType="application/json"
        )
    else:
        with open(args.to, "w") as f:
            f.write(body)
    print(f"-> {args.to}")


if __name__ == "__main__":
    main()
