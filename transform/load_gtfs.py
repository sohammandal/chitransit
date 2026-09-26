"""Load the current CTA GTFS feed as Parquet, one folder per feed version.

Usage: uv run --group transform python transform/load_gtfs.py [--root data/gtfs_parquet]
       [--zip data/gtfs/google_transit.zip]

Writes <root>/<table>/feed_id=<id>/data.parquet for the GTFS files the models use, plus
<root>/feeds/feed_id=<id>/data.parquet (feed_id, published_at, sha256). feed_id is the
feed's publish date (HTTP Last-Modified) + a content hash, e.g. 20260914-8d1f2a9c. A feed
already loaded is skipped, so the nightly job can run this every day (idempotent). The
root can be local or s3://... (DuckDB writes S3 with the AWS credential chain).

Every column stays text: GTFS times pass 24:00 (25:30:00 = 1:30 am on the next day), so
the models parse them.
"""

import argparse
import hashlib
import tempfile
import zipfile
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import duckdb
import httpx

URL = "https://www.transitchicago.com/downloads/sch_data/google_transit.zip"
TABLES = ("trips", "stop_times", "calendar", "calendar_dates", "stops", "routes")


def connect(root: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    if root.startswith("s3://"):
        con.execute("INSTALL httpfs; LOAD httpfs; INSTALL aws; LOAD aws;")
        con.execute("CREATE SECRET (TYPE s3, PROVIDER credential_chain, REGION 'us-east-2')")
    return con


def already_loaded(con: duckdb.DuckDBPyConnection, root: str, feed_id: str) -> bool:
    try:
        return bool(
            con.execute(f"SELECT 1 FROM glob('{root}/feeds/feed_id={feed_id}/*')").fetchall()
        )
    except duckdb.Error:
        return False


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--root", default="data/gtfs_parquet")
    p.add_argument("--zip", help="use this zip instead of downloading (published_at = its mtime)")
    args = p.parse_args()
    root = args.root.rstrip("/")

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "gtfs.zip"
        if args.zip:
            zip_path.write_bytes(Path(args.zip).read_bytes())
            published = datetime.fromtimestamp(Path(args.zip).stat().st_mtime, UTC)
        else:
            with httpx.stream("GET", URL, timeout=120, follow_redirects=True) as r:
                r.raise_for_status()
                published = parsedate_to_datetime(r.headers["last-modified"]).astimezone(UTC)
                with zip_path.open("wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
        sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        feed_id = f"{published:%Y%m%d}-{sha[:8]}"

        con = connect(root)
        if already_loaded(con, root, feed_id):
            print(f"feed {feed_id} already loaded; nothing to do")
            return
        if not root.startswith("s3://"):
            Path(root).mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp, members=[f"{t}.txt" for t in TABLES])
        for table in TABLES:
            out = f"{root}/{table}/feed_id={feed_id}"
            if not root.startswith("s3://"):
                Path(out).mkdir(parents=True, exist_ok=True)
            con.execute(
                f"COPY (SELECT * FROM read_csv('{tmp}/{table}.txt', all_varchar = true, "
                f"header = true)) TO '{out}/data.parquet' (FORMAT parquet, COMPRESSION zstd)"
            )
            rows = con.execute(f"SELECT COUNT(*) FROM '{out}/data.parquet'").fetchone()[0]
            print(f"{table:<15} {rows:>10,} rows -> {out}")
        # Written last: a feed without this row is incomplete and gets reloaded next time.
        out = f"{root}/feeds/feed_id={feed_id}"
        if not root.startswith("s3://"):
            Path(out).mkdir(parents=True, exist_ok=True)
        con.execute(
            f"COPY (SELECT '{feed_id}' AS feed_id, TIMESTAMPTZ '{published.isoformat()}' AS "
            f"published_at, '{sha}' AS sha256, NOW() AS loaded_at) TO '{out}/data.parquet' "
            "(FORMAT parquet)"
        )
        print(f"feed {feed_id} loaded (published {published:%Y-%m-%d %H:%M} UTC)")


if __name__ == "__main__":
    main()
