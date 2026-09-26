"""Publish the analytics models of a dbt run to the lake as Parquet, one folder per
service date, replacing the dates this run built (idempotent: a rerun rewrites them).

Usage (after `dbt build`): uv run --group transform python transform/publish.py
    --db target/ghost_ledger.duckdb --to s3://ghost-ledger-lake-.../analytics

Readers: read_parquet('<to>/<model>/*/*.parquet', hive_partitioning = true).
"""

import argparse
from pathlib import Path

import duckdb

MODELS = ("trip_status", "route_hour_delivery", "stop_hour_service")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="target/ghost_ledger.duckdb")
    p.add_argument("--to", required=True)
    args = p.parse_args()
    to = args.to.rstrip("/")

    con = duckdb.connect(args.db, read_only=True)
    if not to.startswith("s3://"):
        for model in MODELS:
            Path(to, model).mkdir(parents=True, exist_ok=True)
    else:
        con.execute("INSTALL httpfs; LOAD httpfs; INSTALL aws; LOAD aws;")
        con.execute("CREATE SECRET (TYPE s3, PROVIDER credential_chain, REGION 'us-east-2')")
    for model in MODELS:
        # Pending trips are not published: they are not over yet.
        where = "WHERE status != 'pending'" if model == "trip_status" else ""
        rows = con.execute(f"SELECT COUNT(*) FROM {model} {where}").fetchone()[0]
        days = [
            d
            for (d,) in con.execute(
                f"SELECT DISTINCT service_date FROM {model} ORDER BY 1"
            ).fetchall()
        ]
        con.execute(
            f"COPY (SELECT * FROM {model} {where}) TO '{to}/{model}' "
            "(FORMAT parquet, COMPRESSION zstd, PARTITION_BY (service_date), "
            "OVERWRITE_OR_IGNORE, FILENAME_PATTERN 'data')"
        )
        print(
            f"{model:<22} {rows:>9,} rows, service dates {days[0]} .. {days[-1]} -> {to}/{model}/"
        )


if __name__ == "__main__":
    main()
