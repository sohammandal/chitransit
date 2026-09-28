"""Nightly upkeep of Ingress, the Iceberg change log on S3 (audit 2026-09-28, docs/PLAN.md).

Ingress is append-only, so every row ever written is in the current snapshot; an old snapshot
is only "the log up to LSN x". The rows live in the data files, which must never be lost; the
snapshot history is metadata, and letting it grow without bound makes every commit rewrite an
ever larger metadata file. So, per table:

  lock   every data-file version written in the last --days days gets a governance
         retention of 10 years from when it was written (idempotent; the overlap covers a
         missed night; --all locks everything, once). The bucket policy forbids bypassing it.
  tag    the last snapshot of each finished Chicago day gets a permanent tag day-YYYY-MM-DD,
         so any day can still be read as of its end, forever.
  trim   untagged snapshots older than --keep-days are expired: metadata only, no file is
         deleted (the maintenance role can't delete anything), tags and the current
         snapshot are always kept.
  tidy   Iceberg keeps only the last 50 metadata.json files (older ones are hidden, and
         purged by the bucket's 30-day rule: metadata is never locked).

Dry run by default; --apply makes the changes.
Usage: uv run --group transform python transform/lake_maintenance.py [--apply] [--all]
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

BUCKET = "ghost-ledger-lake-354894954112"
DATABASE = "ghost_ledger_ingress"
PREFIX = "warehouse/"
CHICAGO = ZoneInfo("America/Chicago")
RETENTION_YEARS = 10
TAG_PREFIX = "day-"
# The change-log tables get daily tags; Debezium's own offset table is rewritten on every
# flush and has no history worth keeping.
LOG_TABLE_PREFIX = "ghost_ops_"
PROPERTIES = {
    "write.metadata.delete-after-commit.enabled": "true",
    "write.metadata.previous-versions-max": "50",
}


def is_data_file(key: str) -> bool:
    """warehouse/<db>.db/<table>/data/... : the rows (metadata/ holds the index)."""
    return key.startswith(PREFIX) and "/data/" in key


def retain_until(written: datetime) -> datetime:
    """Deterministic, so re-locking a version never shortens or moves its retention."""
    try:
        return written.replace(year=written.year + RETENTION_YEARS)
    except ValueError:  # 29 February
        return written.replace(year=written.year + RETENTION_YEARS, day=28)


def versions_to_lock(versions: list[dict], now: datetime, days: int | None) -> list[dict]:
    """Data-file versions (current or hidden) written in the last `days` days, or all."""
    since = None if days is None else now - timedelta(days=days)
    return [
        v
        for v in versions
        if is_data_file(v["Key"]) and (since is None or v["LastModified"] >= since)
    ]


def day_end_utc_ms(day: date) -> int:
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=CHICAGO)
    return int(end.timestamp() * 1000)


def tags_to_create(snapshots: list[tuple[int, int]], tags: set[str], today: date) -> dict[str, int]:
    """{tag: snapshot id} for each finished Chicago day without a tag yet: the day's last
    snapshot. snapshots: [(snapshot_id, timestamp_ms)]."""
    if not snapshots:
        return {}
    first = datetime.fromtimestamp(min(t for _, t in snapshots) / 1000, CHICAGO).date()
    out = {}
    day = first
    while day < today:
        name = f"{TAG_PREFIX}{day.isoformat()}"
        end = day_end_utc_ms(day)
        last = max(((t, sid) for sid, t in snapshots if t < end), default=None)
        if name not in tags and last is not None:
            out[name] = last[1]
        day += timedelta(days=1)
    return out


def snapshots_to_expire(
    snapshots: list[tuple[int, int]],
    protected: set[int],
    current: int | None,
    now: datetime,
    keep_days: int,
) -> list[int]:
    """Untagged snapshots older than keep_days, never the current one."""
    cutoff = int((now - timedelta(days=keep_days)).timestamp() * 1000)
    return [sid for sid, t in snapshots if t < cutoff and sid not in protected and sid != current]


def lock(s3, versions: list[dict], apply: bool) -> int:
    def one(v: dict) -> None:
        s3.put_object_retention(
            Bucket=BUCKET,
            Key=v["Key"],
            VersionId=v["VersionId"],
            Retention={"Mode": "GOVERNANCE", "RetainUntilDate": retain_until(v["LastModified"])},
        )

    if apply:
        with ThreadPoolExecutor(16) as pool:
            list(pool.map(one, versions))
    return len(versions)


def list_versions(s3) -> list[dict]:
    out = []
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=BUCKET, Prefix=PREFIX):
        out += page.get("Versions", [])
    return out


def maintain_table(catalog, name: str, now: datetime, keep_days: int, apply: bool) -> str:
    from pyiceberg.exceptions import CommitFailedException
    from pyiceberg.table.refs import SnapshotRefType

    for attempt in range(3):  # Debezium commits every few minutes: a clash is retried
        table = catalog.load_table((DATABASE, name))
        md = table.metadata
        snaps = [(s.snapshot_id, s.timestamp_ms) for s in md.snapshots]
        refs = md.refs
        tags = {n for n, r in refs.items() if r.snapshot_ref_type == SnapshotRefType.TAG}
        today = now.astimezone(CHICAGO).date()
        new_tags = tags_to_create(snaps, tags, today) if name.startswith(LOG_TABLE_PREFIX) else {}
        protected = {r.snapshot_id for r in refs.values()} | set(new_tags.values())
        expire = snapshots_to_expire(snaps, protected, md.current_snapshot_id, now, keep_days)
        props = {k: v for k, v in PROPERTIES.items() if md.properties.get(k) != v}
        summary = (
            f"{name}: {len(snaps)} snapshots, +{len(new_tags)} tags, "
            f"expire {len(expire)}, set {len(props)} props"
        )
        if not apply or not (new_tags or expire or props):
            return summary
        try:
            if props:
                table.transaction().set_properties(**props).commit_transaction()
                table = catalog.load_table((DATABASE, name))
            if new_tags:
                with table.manage_snapshots() as ms:
                    for tag, sid in new_tags.items():
                        ms.create_tag(snapshot_id=sid, tag_name=tag)
                table = catalog.load_table((DATABASE, name))
            if expire:
                es = table.maintenance.expire_snapshots()
                # pyiceberg 0.10 declares the id set on the class, so it's shared by every
                # ExpireSnapshots: ids from the previous table leak in ("snapshot does not
                # exist", 2026-09-28). A fresh set per table.
                es._snapshot_ids_to_expire = set()
                es.by_ids(expire).commit()
            return summary
        except CommitFailedException:
            if attempt == 2:
                raise
    return summary


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="make the changes (default: dry run)")
    p.add_argument(
        "--all", action="store_true", help="lock every data-file version, not only recent ones"
    )
    p.add_argument("--days", type=int, default=3, help="lock versions written in the last N days")
    p.add_argument("--keep-days", type=int, default=2, help="keep untagged snapshots this long")
    args = p.parse_args()

    import boto3
    from pyiceberg.catalog.glue import GlueCatalog

    now = datetime.now(UTC)
    s3 = boto3.client("s3")
    catalog = GlueCatalog("glue", **{"client.region": "us-east-2"})
    mode = "APPLY" if args.apply else "dry run"

    versions = versions_to_lock(list_versions(s3), now, None if args.all else args.days)
    n = lock(s3, versions, args.apply)
    print(f"[{mode}] lock: {n} data-file versions → {RETENTION_YEARS}-year governance retention")

    failed = False
    for _, name in sorted(catalog.list_tables(DATABASE)):
        try:
            print(f"[{mode}] {maintain_table(catalog, name, now, args.keep_days, args.apply)}")
        except Exception as e:  # one table's clash shouldn't stop the others
            failed = True
            # pyiceberg errors can embed the whole snapshot list (megabytes): keep the gist
            print(f"[{mode}] {name}: FAILED {type(e).__name__}: {str(e)[:300]}", file=sys.stderr)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
