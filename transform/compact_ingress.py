"""Compact finished days of Ingress: many small Debezium commit files -> one file per UTC day,
sorted by time, so a day reads in about a second however long the lake gets.

Usage: uv run --group transform python transform/compact_ingress.py [--tables bus_positions]
    [--cutoff 2026-09-27] [--apply]     (default: dry run, prints the plan)

Rules (Ingress is the system of record; see CLAUDE.md "Ingress protection"):
  * A pure layout change: every row is rewritten exactly as it was, duplicates included (CDC is
    at-least-once; staging deduplicates). New files are read back and must match the old
    ones per day (rows and an order-independent hash of every column) before any commit.
  * One commit per batch replaces the old files with the new ones (delete + add in one
    snapshot). Old files are only dropped from the current snapshot: they stay on S3 (object
    lock), and tagged day snapshots still reference them. Nothing is deleted.
  * Only files whose newest row is before --cutoff (default: two UTC days ago) are touched,
    and never files a compaction wrote, so reruns are no-ops and late CDC files are picked up
    by the next run. A compaction's files are known by the snapshot that added them, kept in
    the table property KEPT: the snapshots themselves expire after two days
    (lake_maintenance.py), and until Oct 7 that made every compacted day look new again, so it
    was rewritten every night (a day is written as 2-3 files, cut by size). Debezium only
    appends, so its concurrent commits never conflict with a rewrite; a lost commit race is
    retried on the refreshed table.
"""

import argparse
import hashlib
import itertools
import time
import uuid
from collections import defaultdict
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime, timedelta

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from pyiceberg.catalog.glue import GlueCatalog
from pyiceberg.conversions import from_bytes
from pyiceberg.exceptions import CommitFailedException
from pyiceberg.io.pyarrow import _dataframe_to_data_files
from pyiceberg.table import Table

REGION = "us-east-2"
DATABASE = "ghost_ledger_ingress"
MARK = "ghost-ledger.compaction"
KEPT = "ghost-ledger.compaction.snapshots"  # table property: every compaction snapshot id
# Files this script writes are named by pyiceberg "00000-<n>-<uuid>.parquet"; Debezium names its
# "<yyyymmdd>-...". Used once (--adopt-legacy) for compactions whose snapshot expired before KEPT.
LEGACY_NAME = "00000-"
# table -> time column that sorts and splits it (the column the nightly filters on)
TABLES = {
    "bus_positions": "observed_at",
    "raw_polls": "fetched_at",
    "raw_responses": "fetched_at",
    "sweeps": "started_at",
}
# Rows per Parquet row group in the rewritten files. Readers parallelize by row group and hold
# about one in flight per thread: raw_responses rows are ~22 KB of JSON, and a whole day as one
# row group (600 MB, Oct 2) gave a reader one thread for the day and the most memory per
# thread. ~22-30 MB a group there (stg_predictions also caps its threads: models/staging).
ROW_GROUP_ROWS = {"raw_responses": 1024}
DEFAULT_ROW_GROUP_ROWS = 131_072


def compaction_ids(table: Table) -> set[int]:
    """Every compaction snapshot: recorded in KEPT, or still in the history with MARK."""
    kept = {int(x) for x in table.metadata.properties.get(KEPT, "").split(",") if x}
    return kept | {s.snapshot_id for s in table.snapshots() if s.summary and s.summary.get(MARK)}


def record(table: Table, apply: bool, legacy: bool = False) -> int:
    """Writes every compaction snapshot id into KEPT before the snapshot can expire (metadata
    only). legacy: also the snapshots that added files named like this script's output, for
    compactions whose snapshot expired before KEPT existed. Returns how many ids are new."""
    kept = {int(x) for x in table.metadata.properties.get(KEPT, "").split(",") if x}
    ids = compaction_ids(table)
    if legacy:
        entries = table.inspect.entries().select(["snapshot_id", "data_file"]).to_pylist()
        ids |= {
            e["snapshot_id"]
            for e in entries
            if e["data_file"]["file_path"].rsplit("/", 1)[-1].startswith(LEGACY_NAME)
        }
    new = ids - kept
    if new and apply:
        for attempt in range(5):
            try:
                value = ",".join(str(i) for i in sorted(ids | compaction_ids(table)))
                table.transaction().set_properties({KEPT: value}).commit_transaction()
                break
            except CommitFailedException:  # Debezium committed meanwhile
                time.sleep(2 + attempt * 3)
                table.refresh()
        else:
            raise SystemExit(f"{table.name()}: couldn't record compaction snapshots")
        table.refresh()
    return len(new)


def compacted_paths(table: Table) -> set[str]:
    """Data files added by a compaction snapshot (never rewritten again)."""
    marked = compaction_ids(table)
    if not marked:
        return set()
    entries = table.inspect.entries().select(["snapshot_id", "data_file"]).to_pylist()
    return {e["data_file"]["file_path"] for e in entries if e["snapshot_id"] in marked}


def plan(
    table: Table, column: str, cutoff: datetime, again: bool = False, legacy: bool = False
) -> dict[date, list]:
    """Candidate files grouped by the UTC day of their oldest row. again: compacted files too
    (to rewrite them with other row groups). legacy: files named like a compaction's count as
    compacted (a dry run of --adopt-legacy)."""
    field = table.schema().find_field(column)
    done = set() if again else compacted_paths(table)
    groups: dict[date, list] = defaultdict(list)
    for task in table.scan().plan_files():
        f = task.file
        if task.delete_files:
            # Rewriting a data file would detach it from its deletes (they apply by sequence
            # number): rows deleted in the log would come back. Ingress is append-only
            # (upsert=false), so this must never happen; stop if it does.
            raise SystemExit(f"{table.name()}: {f.file_path} has delete files; nothing done")
        if f.file_path in done or field.field_id not in f.upper_bounds:
            continue
        if legacy and not again and f.file_path.rsplit("/", 1)[-1].startswith(LEGACY_NAME):
            continue
        low = from_bytes(field.field_type, f.lower_bounds[field.field_id])
        high = from_bytes(field.field_type, f.upper_bounds[field.field_id])
        if datetime.fromtimestamp(high / 1e6, UTC) >= cutoff:
            continue
        groups[datetime.fromtimestamp(low / 1e6, UTC).date()].append(f)
    # A lone file already covering one day is as compact as it gets.
    return {d: fs for d, fs in groups.items() if again or len(fs) > 1}


def fingerprint(tables: Iterable[pa.Table], column: str) -> dict[str, tuple[int, str]]:
    """Per UTC day: row count and an order-independent digest of every row, over one or more
    tables (a few thousand rows at a time: a day of raw_responses as Python objects all at
    once peaked at 10 GB)."""
    acc: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for rows in tables:
        names = sorted(rows.column_names)
        for batch in rows.select(names).to_batches(max_chunksize=4096):
            days = pc.strftime(batch[column], format="%Y-%m-%d", locale="C").to_pylist()
            cols = [batch[n].to_pylist() for n in names]
            for i, day in enumerate(days):
                h = hashlib.blake2b(repr(tuple(c[i] for c in cols)).encode(), digest_size=16)
                a = acc[day]
                a[0] += 1
                a[1] = (a[1] + int.from_bytes(h.digest())) % 2**128
    return {d: (n, f"{s:032x}") for d, (n, s) in acc.items()}


def conform(part: pa.Table, schema: pa.Schema, field_ids: dict[str, int]) -> pa.Table:
    """One file's rows in the table's current schema, as Iceberg reads them: columns matched
    by field id (never by name alone), a column the file predates as nulls (Debezium's older
    files have no __source_sequence), in the schema's order and types."""
    by_id = {}
    for field in part.schema:
        meta = field.metadata or {}
        fid = meta.get(b"PARQUET:field_id")
        if fid is None or field.name not in field_ids or int(fid) != field_ids[field.name]:
            raise SystemExit(f"column {field.name} (field id {fid!r}) doesn't match the table")
        by_id[int(fid)] = part[field.name]
    cols = [
        by_id[field_ids[f.name]] if field_ids[f.name] in by_id else pa.nulls(part.num_rows, f.type)
        for f in schema
    ]
    return pa.Table.from_arrays(cols, schema=pa.schema([f.remove_metadata() for f in schema]))


def read_each(table: Table, files: list) -> Iterator[pa.Table]:
    """Each file's rows in the table's schema (conform), one file at a time."""
    schema = table.schema().as_arrow()
    field_ids = {f.name: f.field_id for f in table.schema().fields}
    for f in files:
        yield conform(pq.read_table(table.io.new_input(f.file_path).open()), schema, field_ids)


def read(table: Table, files: list) -> pa.Table:
    return pa.concat_tables(read_each(table, files))  # chunks, not copies


def compact(
    table: Table,
    column: str,
    day: date,
    files: list,
    apply: bool,
    row_group_rows: int = DEFAULT_ROW_GROUP_ROWS,
) -> None:
    started = time.perf_counter()
    # In commit order (oldest rows first): the day's rows come out clustered by time without a
    # sorted copy, and a reader's row-group statistics still skip what it doesn't need
    field = table.schema().find_field(column)

    def oldest(f) -> int:  # a file of rows without a time has no bound: first, then refused
        bound = f.lower_bounds.get(field.field_id)
        return -1 if bound is None else from_bytes(field.field_type, bound)

    files = sorted(files, key=oldest)
    old = read(table, files)
    if old[column].null_count:
        # A row without a time has no day: the split below would drop it
        raise SystemExit(f"{day}: {old[column].null_count} rows without {column}; nothing done")
    before = fingerprint([old], column)
    in_bytes = sum(f.file_size_in_bytes for f in files)
    print(
        f"  {day}: {len(files):>4} files {in_bytes / 2**20:7.1f} MiB {old.num_rows:>10,} rows"
        f" -> days {sorted(before)}",
        end="",
    )
    if not apply:
        print("  (dry run)")
        return

    # read() put every file in the table's schema; written with its field ids, no copy. One
    # copy of the day at most (a batch spanning two days is split): the runner has 7 GB, and a
    # sorted copy plus a filtered one peaked at 8 GB on a day of raw_responses.
    rows = pa.Table.from_arrays(old.columns, schema=table.schema().as_arrow())
    del old
    days = pc.strftime(rows[column], format="%Y-%m-%d", locale="C")
    new_files = []
    counter = itertools.count(0)
    write_uuid = uuid.uuid4()
    # the row-group size for this write only: the table's own properties (Debezium's) unchanged
    props = {**table.metadata.properties, "write.parquet.row-group-limit": str(row_group_rows)}
    meta = table.metadata.model_copy(update={"properties": props})
    for d in sorted(before):
        part = rows if len(before) == 1 else rows.filter(pc.equal(days, d))
        new_files += list(_dataframe_to_data_files(meta, part, table.io, write_uuid, counter))
        del part
    del rows, days
    pa.default_memory_pool().release_unused()
    after = fingerprint(read_each(table, new_files), column)  # read back one file at a time
    if after != before:
        raise SystemExit(f"{day}: rewritten files differ from the originals; nothing committed")

    for attempt in range(5):
        try:
            with table.transaction() as tx:
                snap = tx.update_snapshot(
                    snapshot_properties={MARK: "true", f"{MARK}.day": str(day)}
                ).overwrite()
                with snap:
                    for f in files:
                        snap.delete_data_file(f)
                    for f in new_files:
                        snap.append_data_file(f)
            break
        except CommitFailedException:
            # Debezium committed meanwhile: its appends don't touch these files; retry.
            time.sleep(2 + attempt * 3)
            table.refresh()
    else:
        raise SystemExit(f"{day}: lost the commit race 5 times; new files left unreferenced")
    out_bytes = sum(f.file_size_in_bytes for f in new_files)
    print(
        f"  -> {len(new_files)} files {out_bytes / 2**20:.1f} MiB, verified, committed"
        f" ({time.perf_counter() - started:.0f} s)"
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tables", default=",".join(TABLES))
    p.add_argument("--cutoff", type=date.fromisoformat, help="UTC day; default two days ago")
    p.add_argument("--day", type=date.fromisoformat, help="only this batch (UTC day)")
    p.add_argument("--apply", action="store_true", help="rewrite and commit (default: plan only)")
    p.add_argument("--again", action="store_true", help="rewrite compacted files too")
    p.add_argument(
        "--adopt-legacy",
        action="store_true",
        help="also record compactions whose snapshot expired before they were recorded (once)",
    )
    p.add_argument("--catalog", default="glue", help=argparse.SUPPRESS)  # tests: a local catalog
    args = p.parse_args()
    cutoff_day = args.cutoff or datetime.now(UTC).date() - timedelta(days=2)
    cutoff = datetime.combine(cutoff_day, datetime.min.time(), UTC)

    if args.catalog == "glue":
        catalog = GlueCatalog("glue", **{"client.region": REGION})
    else:  # a rehearsal on a local copy: --catalog sqlite:///path/catalog.db
        from pyiceberg.catalog.sql import SqlCatalog

        catalog = SqlCatalog("local", uri=args.catalog)
    print(f"files entirely before {cutoff:%Y-%m-%d} UTC; {'APPLY' if args.apply else 'dry run'}")
    for name in args.tables.split(","):
        table = catalog.load_table(f"{DATABASE}.ghost_ops_{name}")
        # Known compactions first (a dry run counts them as if recorded), so none is redone
        new = record(table, args.apply, args.adopt_legacy)
        print(f"{name}: {new} compaction snapshots {'recorded' if args.apply else 'to record'}")
        groups = plan(table, TABLES[name], cutoff, args.again, legacy=args.adopt_legacy)
        print(f"{name}: {sum(map(len, groups.values()))} files in {len(groups)} batches")
        for day in sorted(groups):
            if args.day is None or day == args.day:
                rows = ROW_GROUP_ROWS.get(name, DEFAULT_ROW_GROUP_ROWS)
                compact(table, TABLES[name], day, groups[day], args.apply, rows)
        record(table, args.apply)  # this run's compactions, before their snapshots can expire


if __name__ == "__main__":
    main()
