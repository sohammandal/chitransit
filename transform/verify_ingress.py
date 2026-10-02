"""Fingerprint Ingress the way its readers see it (DuckDB through the Glue catalog), per UTC day:
rows, distinct keys and an order-independent hash of every column of every row. Run before and
after a layout change (compact_ingress.py) and compare: a finished day must not change.

Usage: uv run --group transform python transform/verify_ingress.py --tables sweeps
    --until 2026-09-28 --out before.json [--compare after.json]
"""

import argparse
import json
import sys
from datetime import date

import duckdb

# table -> (time column, key columns), as compact_ingress.TABLES and ingress_counts.TABLES
TABLES = {
    "bus_positions": ("observed_at", "vid, observed_at"),
    "raw_polls": ("fetched_at", "poll_id"),
    "raw_responses": ("fetched_at", "poll_id, fetched_at"),
    "sweeps": ("started_at", "sweep_id"),
}


def fingerprint(tables: list[str], until: date) -> dict:
    con = duckdb.connect()
    for ext in ("httpfs", "aws", "iceberg"):
        con.execute(f"INSTALL {ext}; LOAD {ext}")
    con.execute("CREATE SECRET (TYPE s3, PROVIDER credential_chain, REGION 'us-east-2')")
    con.execute("ATTACH '354894954112' AS glue (TYPE iceberg, ENDPOINT_TYPE glue)")
    con.execute("SET TimeZone = 'UTC'; SET threads = 32")
    out: dict = {}
    for name in tables:
        col, key = TABLES[name]
        rows = con.execute(
            f"""SELECT CAST({col} AS DATE)::VARCHAR, count(*), count(DISTINCT ({key})),
                       sum(hash(t)::HUGEINT)::VARCHAR
                FROM glue.ghost_ledger_ingress.ghost_ops_{name} AS t
                WHERE {col} < ?::DATE OR {col} IS NULL
                GROUP BY 1 ORDER BY 1""",
            [until],
        ).fetchall()
        out[name] = {d or "null": [n, k, h] for d, n, k, h in rows}
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tables", default=",".join(TABLES))
    p.add_argument("--until", type=date.fromisoformat, required=True, help="first UTC day not read")
    p.add_argument("--out", required=True)
    p.add_argument("--compare", help="an earlier --out: exit 1 if any day differs")
    args = p.parse_args()
    now = fingerprint(args.tables.split(","), args.until)
    with open(args.out, "w") as f:
        json.dump(now, f, indent=1)
    for name, days in now.items():
        print(f"{name}: {len(days)} days, {sum(v[0] for v in days.values()):,} rows")
    if args.compare:
        with open(args.compare) as f:
            then = json.load(f)
        bad = [
            (name, d, then[name].get(d), now[name].get(d))
            for name in now
            for d in sorted(set(then[name]) | set(now[name]))
            if then[name].get(d) != now[name].get(d)
        ]
        for b in bad:
            print("DIFFERS:", *b)
        print("identical" if not bad else f"{len(bad)} day(s) differ")
        sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
