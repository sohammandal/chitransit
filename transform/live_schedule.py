"""Write the poller's live schedule: every scheduled departure at every stop, today and
tomorrow (live_stop_departures), to analytics/ops/stop_departures.json. Only the box reads it
(ghost_ledger.live.Schedule): the site never loads it.

  {"built_at": ISO, "dates": ["2026-09-29", "2026-09-30"], "base": epoch seconds,
   "stops": {"<rt>|<stop_id>": base64 of little-endian int32 [first - base, gap, gap, ...]}}

Times are epoch seconds (UTC: no DST ambiguity), sorted and delta-encoded; binary so the poller
parses ~2.3M departures without building millions of objects (it has ~250 MB to spare).
check() is the contract with the poller: on any failure nothing is written, and the poller
keeps judging with the file it has, or without one.

Usage: uv run --group transform python transform/live_schedule.py
    --db transform/target/ghost_ledger.duckdb --to s3://.../analytics/ops/stop_departures.json
    (or a local path)
"""

import argparse
import base64
import gzip
import json
import sys
from array import array
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

MIN_STOPS = 5000  # a normal CTA day has ~13,900 route-stops with departures
MAX_GZIP_BYTES = 8_000_000


def build(rows: list[tuple[str, str, date, int]]) -> dict:
    """rows: (rt, stop_id, service_date, epoch seconds)."""
    dates = sorted({str(d) for _, _, d, _ in rows})
    base = min(t for *_, t in rows)
    stops: dict[str, list[int]] = {}
    for rt, stop, _, t in rows:
        stops.setdefault(f"{rt}|{stop}", []).append(t - base)
    encoded = {}
    for key, times in stops.items():
        times.sort()
        deltas = array("i", [times[0]] + [b - a for a, b in zip(times, times[1:], strict=False)])
        if sys.byteorder != "little":
            deltas.byteswap()
        encoded[key] = base64.b64encode(deltas.tobytes()).decode()
    stops = encoded
    return {"built_at": datetime.now(UTC).isoformat(timespec="seconds"), "dates": dates,
            "base": base, "stops": stops}  # fmt: skip


def check(doc: dict, today: date) -> list[str]:
    bad = []
    if str(today) not in doc["dates"]:
        bad.append(f"no departures for today ({today})")
    if len(doc["stops"]) < MIN_STOPS:
        bad.append(f"only {len(doc['stops'])} route-stops (expected ~13,900)")
    for times in doc["stops"].values():
        deltas = array("i", base64.b64decode(times))
        if len(deltas) == 0 or any(d < 0 for d in deltas[1:]):
            bad.append("departures missing or not sorted")
            break
    return bad


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--to", required=True)
    args = ap.parse_args()

    import duckdb

    con = duckdb.connect(args.db, read_only=True)
    rows = con.sql(
        "SELECT rt, stop_id, service_date, epoch(departs_at)::bigint FROM live_stop_departures"
    ).fetchall()
    today = datetime.now(ZoneInfo("America/Chicago")).date()
    doc = build(rows)
    body = gzip.compress(json.dumps(doc, separators=(",", ":")).encode(), mtime=0)
    bad = check(doc, today) + (
        [f"{len(body):,} bytes gzipped"] if len(body) > MAX_GZIP_BYTES else []
    )
    if bad:
        sys.exit("live schedule not written: " + "; ".join(bad))
    if args.to.startswith("s3://"):
        import boto3

        bucket, key = args.to.removeprefix("s3://").split("/", 1)
        boto3.client("s3").put_object(
            Bucket=bucket, Key=key, Body=body, ContentType="application/json",
            ContentEncoding="gzip",
        )  # fmt: skip
    else:
        Path(args.to).write_bytes(body)
    print(f"live schedule: {doc['dates']}, {len(doc['stops']):,} route-stops, {len(body):,} bytes")


if __name__ == "__main__":
    main()
