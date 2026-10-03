"""Compaction of Ingress (transform/compact_ingress.py): a pure layout change of the system of
record. Every row must survive exactly, whatever the files look like and whoever commits
meanwhile; anything unexpected must stop it with nothing committed."""

import importlib.util
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

pa = pytest.importorskip("pyarrow")
pytest.importorskip("pyiceberg")
pytest.importorskip("sqlalchemy")

from pyiceberg.catalog.sql import SqlCatalog  # noqa: E402
from pyiceberg.schema import Schema  # noqa: E402
from pyiceberg.types import (  # noqa: E402
    IntegerType,
    LongType,
    NestedField,
    StringType,
    TimestamptzType,
)

SRC = Path(__file__).parents[1] / "transform/compact_ingress.py"
spec = importlib.util.spec_from_file_location("compact_ingress", SRC)
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)

DAY1 = datetime(2026, 9, 26, 22, 30, tzinfo=UTC)  # the second hourly file crosses midnight
CUTOFF = datetime(2026, 9, 28, tzinfo=UTC)


def batch(start: datetime, n: int, first_id: int, source_sequence: bool) -> pa.Table:
    """n sweeps a minute apart, as one Debezium commit would write them."""
    cols = {
        "sweep_id": pa.array(range(first_id, first_id + n), pa.int64()),
        "started_at": pa.array(
            [start + timedelta(minutes=i) for i in range(n)], pa.timestamp("us", tz="UTC")
        ),
        "calls_ok": pa.array([13] * n, pa.int32()),
    }
    if source_sequence:
        cols["__source_sequence"] = pa.array([f"seq{first_id + i}" for i in range(n)])
    return pa.table(cols)


@pytest.fixture
def table(tmp_path):
    """A sweeps-like log in a local catalog: many small files over two UTC days, the older
    ones written before __source_sequence was added to the table (as in prod)."""
    catalog = SqlCatalog("t", uri=f"sqlite:///{tmp_path}/c.db", warehouse=f"file://{tmp_path}/wh")
    catalog.create_namespace("ingress")
    t = catalog.create_table(
        "ingress.sweeps",
        Schema(
            NestedField(1, "sweep_id", LongType(), required=False),
            NestedField(2, "started_at", TimestamptzType(), required=False),
            NestedField(3, "calls_ok", IntegerType(), required=False),
        ),
    )
    for k in range(4):  # Sep 26 22:30 .. Sep 27 02:29 UTC
        t.append(batch(DAY1 + timedelta(hours=k), 60, 1000 * k, source_sequence=False))
    with t.update_schema() as u:
        u.add_column("__source_sequence", StringType())
    for k in range(4, 8):  # Sep 27 02:30 .. 06:29
        t.append(batch(DAY1 + timedelta(hours=k), 60, 1000 * k, source_sequence=True))
    return catalog.load_table("ingress.sweeps")


def rows(t) -> Counter:
    a = t.scan().to_arrow()
    names = sorted(a.column_names)
    cols = [a[n].to_pylist() for n in names]
    return Counter(tuple(c[i] for c in cols) for i in range(a.num_rows))


def compact_all(t, apply=True):
    groups = ci.plan(t, "started_at", CUTOFF)
    for day in sorted(groups):
        ci.compact(t, "started_at", day, groups[day], apply)


def test_every_row_survives_exactly_and_each_day_becomes_one_file(table):
    before = rows(table)
    assert len(list(table.scan().plan_files())) == 8
    compact_all(table)
    table.refresh()
    assert rows(table) == before  # the older files' missing column reads as null, as before
    field = table.schema().find_field("started_at")
    spans = sorted(
        tuple(
            datetime.fromtimestamp(ci.from_bytes(field.field_type, b[2]) / 1e6, UTC).date()
            for b in (t.file.lower_bounds, t.file.upper_bounds)
        )
        for t in table.scan().plan_files()
    )
    # the Sep 26 batch (two files, one crossing midnight) is split into a file per day; the
    # Sep 27 batch is one file: every file holds one UTC day
    assert spans == [(date(2026, 9, 26),) * 2, (date(2026, 9, 27),) * 2, (date(2026, 9, 27),) * 2]
    assert ci.plan(table, "started_at", CUTOFF) == {}  # a second run touches nothing
    assert table.current_snapshot().summary[ci.MARK] == "true"


def test_a_concurrent_commit_is_kept(table, monkeypatch):
    # Debezium appends every few minutes: a commit landing between our read and ours
    before = rows(table)
    late = batch(DAY1 + timedelta(hours=9), 3, 9000, source_sequence=True)
    real = ci.Table.transaction
    fired = []

    def racing(self):
        if not fired:
            fired.append(True)
            table.catalog.load_table("ingress.sweeps").append(late)
        return real(self)

    monkeypatch.setattr(ci.Table, "transaction", racing)
    compact_all(table)
    table.refresh()
    late_rows = Counter(
        tuple(late[n][i].as_py() for n in sorted(late.column_names)) for i in range(3)
    )
    assert rows(table) == before + late_rows


def test_a_row_without_a_time_stops_it_with_nothing_committed(table):
    t = table
    t.append(
        pa.table(
            {
                "sweep_id": pa.array([77], pa.int64()),
                "started_at": pa.array([None], pa.timestamp("us", tz="UTC")),
                "calls_ok": pa.array([1], pa.int32()),
                "__source_sequence": pa.array(["x"]),
            }
        )
    )
    t.append(batch(DAY1 + timedelta(hours=8), 5, 8000, source_sequence=True))
    t.refresh()
    files = [x.file for x in t.scan().plan_files()]
    snapshot = t.current_snapshot().snapshot_id
    with pytest.raises(SystemExit):
        ci.compact(t, "started_at", date(2026, 9, 27), files, True)
    t.refresh()
    assert t.current_snapshot().snapshot_id == snapshot


def test_delete_files_stop_it_before_anything_is_read():
    # Rewriting a data file would detach it from its deletes and bring deleted rows back
    class Task:
        file = type("F", (), {"file_path": "s3://x/data/a.parquet"})()
        delete_files = {"s3://x/data/a-deletes.parquet"}

    class Fake:
        def scan(self):
            return type("S", (), {"plan_files": lambda self: [Task()]})()

        def schema(self):
            return Schema(NestedField(2, "started_at", TimestamptzType(), required=False))

        def name(self):
            return ("ingress", "sweeps")

        def snapshots(self):
            return []

    with pytest.raises(SystemExit):
        ci.plan(Fake(), "started_at", CUTOFF)


def test_a_rewrite_that_does_not_read_back_identical_is_never_committed(table, monkeypatch):
    # The read-back check is the last line of defence against a writer bug: here the writer
    # silently drops a row
    real = ci._dataframe_to_data_files

    def lossy(metadata, rows, io, write_uuid, counter):
        return real(metadata, rows.slice(1), io, write_uuid, counter)

    monkeypatch.setattr(ci, "_dataframe_to_data_files", lossy)
    snapshot = table.current_snapshot().snapshot_id
    with pytest.raises(SystemExit):
        compact_all(table)
    table.refresh()
    assert table.current_snapshot().snapshot_id == snapshot


def test_rewritten_files_have_small_row_groups_and_compacted_days_can_be_rewritten(table):
    # Readers hold a row group in flight per thread: a day as one row group of raw_responses
    # (600 MB) ran the nightly out of memory (Oct 3)
    import pyarrow.parquet as pq

    before = rows(table)
    groups = ci.plan(table, "started_at", CUTOFF)
    for day in sorted(groups):
        ci.compact(table, "started_at", day, groups[day], True, row_group_rows=50)
    table.refresh()

    def largest_group():
        sizes = []
        for task in table.scan().plan_files():
            md = pq.ParquetFile(table.io.new_input(task.file.file_path).open()).metadata
            sizes += [md.row_group(i).num_rows for i in range(md.num_row_groups)]
        return max(sizes)

    assert largest_group() <= 50
    assert ci.plan(table, "started_at", CUTOFF) == {}  # compacted: left alone by default
    again = ci.plan(table, "started_at", CUTOFF, again=True)  # but can be rewritten
    for day in sorted(again):
        ci.compact(table, "started_at", day, again[day], True, row_group_rows=200)
    table.refresh()
    assert rows(table) == before and largest_group() == 200
