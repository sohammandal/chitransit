"""Benchmark reading Ingress the way the nightly does: rerun after every layout change
(partitioning, compaction) and compare.

Usage: uv run --group transform python transform/bench_ingress.py [--day 2026-09-27]
    [--days 1] [--tables bus_positions,raw_polls] [--out data/bench/ingress.jsonl]

Per table: the layout (snapshots, manifests, data files, bytes, rows; no data read) and a
timed scan of the staging models' columns over `days` Chicago days ending at `day`, with
DuckDB's profile: wall time, files read, HTTP GETs and bytes in. One JSON line per run is
appended to --out, so runs over weeks chart how cost grows with the lake.
"""

import argparse
import json
import re
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
from pyiceberg.catalog.glue import GlueCatalog

REGION = "us-east-2"
ACCOUNT = "354894954112"
DATABASE = "ghost_ledger_ingress"
# table -> (time column the nightly filters on, columns its staging models read)
TABLES = {
    "bus_positions": (
        "observed_at",
        "vid, observed_at, fetched_at, rt, pid, pdist, lat, lon, dly, stsd, stst, tablockid,"
        " psgld, __lsn",
    ),
    "raw_polls": ("fetched_at", "*"),
    "raw_responses": ("fetched_at", "*"),
    "sweeps": ("started_at", "*"),
}


def layout(catalog: GlueCatalog, name: str) -> dict:
    table = catalog.load_table(f"{DATABASE}.ghost_ops_{name}")
    snap = table.current_snapshot()
    files = table.inspect.files().select(["file_size_in_bytes", "record_count"]).to_pylist()
    total = sum(f["file_size_in_bytes"] for f in files)
    return {
        "partition_spec": str(table.spec()),
        "snapshots": len(table.snapshots()),
        "manifests": len(snap.manifests(table.io)) if snap else 0,
        "data_files": len(files),
        "bytes": total,
        "rows": sum(f["record_count"] for f in files),
        "avg_file_kb": round(total / len(files) / 1024, 1) if files else 0,
    }


def profile(con: duckdb.DuckDBPyConnection, sql: str) -> dict:
    started = time.perf_counter()
    text = con.execute(f"EXPLAIN ANALYZE {sql}").fetchall()[0][1]
    wall = time.perf_counter() - started

    def grab(pattern: str) -> str | None:
        m = re.search(pattern, text)
        return m.group(1) if m else None

    size, unit = (grab(r"in: ([\d.]+ \w+)") or "0 bytes").split()
    scale = {"bytes": 1, "KiB": 2**10, "MiB": 2**20, "GiB": 2**30}[unit]
    rows = [int(r.replace(",", "")) for r in re.findall(r"([\d,]+) rows", text)]
    return {
        "seconds": round(wall, 2),
        "files_read": int(grab(r"Total Files Read: ([\d,]+)").replace(",", "") or 0),
        "http_gets": int(grab(r"#GET: (\d+)") or 0),
        "bytes_in": int(float(size) * scale),
        "rows": max(rows) if rows else 0,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--day", type=date.fromisoformat, help="last Chicago day read; default yesterday"
    )
    p.add_argument("--days", type=int, default=1)
    p.add_argument("--tables", default=",".join(TABLES))
    p.add_argument("--out", default="../data/bench/ingress.jsonl")
    p.add_argument("--note", default="", help="what changed since the last run")
    args = p.parse_args()
    last = args.day or (datetime.now(UTC) - timedelta(hours=5)).date() - timedelta(days=1)
    first = last - timedelta(days=args.days - 1)

    catalog = GlueCatalog("glue", **{"client.region": REGION})
    con = duckdb.connect()
    con.execute(
        "INSTALL httpfs; LOAD httpfs; INSTALL aws; LOAD aws; INSTALL iceberg; LOAD iceberg;"
        f"CREATE SECRET (TYPE s3, PROVIDER credential_chain, REGION '{REGION}');"
        f"ATTACH '{ACCOUNT}' AS glue (TYPE iceberg, ENDPOINT_TYPE glue);"
        "SET TimeZone = 'America/Chicago';"
    )

    run = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "duckdb": duckdb.__version__,
        "window": [str(first), str(last)],
        "note": args.note,
        "tables": {},
    }
    print(f"window {first} .. {last} (Chicago days)")
    print(
        f"{'table':<14} {'files':>7} {'avg KB':>7} {'manif':>6} {'snaps':>6} | "
        f"{'secs':>6} {'read':>6} {'GETs':>6} {'MiB in':>7} {'rows':>11}"
    )
    for name in args.tables.split(","):
        col, cols = TABLES[name]
        shape = layout(catalog, name)
        # The same predicate shape as the staging models: a local-midnight range.
        scan = profile(
            con,
            f"SELECT {cols} FROM glue.{DATABASE}.ghost_ops_{name} "
            f"WHERE {col} >= TIMESTAMPTZ '{first} 00:00:00 America/Chicago' "
            f"AND {col} < TIMESTAMPTZ '{last + timedelta(days=1)} 00:00:00 America/Chicago'",
        )
        run["tables"][name] = {"layout": shape, "scan": scan}
        print(
            f"{name:<14} {shape['data_files']:>7,} {shape['avg_file_kb']:>7} "
            f"{shape['manifests']:>6,} {shape['snapshots']:>6,} | {scan['seconds']:>6} "
            f"{scan['files_read']:>6,} {scan['http_gets']:>6,} "
            f"{scan['bytes_in'] / 2**20:>7.1f} {scan['rows']:>11,}"
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a") as f:
        f.write(json.dumps(run) + "\n")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
