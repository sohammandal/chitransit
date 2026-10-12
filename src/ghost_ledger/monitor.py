"""Timeliness metrics for alarms.

Every 60 s: read a few facts from Postgres, turn them into metrics, and send them to
CloudWatch (prod) or the log (dev, `--dry-run`). Terraform owns the alarms. The monitor
never decides anything itself; if it dies, the metrics stop and the alarms treat missing
data as breaching, so a dead box still pages you.

Timeliness only (is data arriving?). Integrity (is it complete and correct?) is checked
nightly, by the dbt tests and checks.
"""

import logging
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import psycopg

from ghost_ledger.config import Settings

log = logging.getLogger("ghost_ledger.monitor")

CHICAGO = ZoneInfo("America/Chicago")
DAYTIME_HOURS = range(5, 23)  # 05:00-22:59 Chicago: buses should be running
PUBLISH_EVERY_SECONDS = 60


@dataclass(frozen=True)
class Snapshot:
    """Facts read from Postgres at one instant. `now` is the monitor's clock (UTC)."""

    now: datetime
    last_sweep_finished_at: datetime | None
    last_sweep_vehicles: int | None
    sweeps_last_hour: int
    calls_ok_15m: int
    calls_total_15m: int
    sweeps_last_10m: int
    sweeps_with_buses_last_10m: int
    first_sweep_started_at: datetime | None = None
    # one entry per logical replication slot: (active, wal_status, lag_bytes or None)
    slots: tuple[tuple[bool, str | None, int | None], ...] = ()
    # the train poller (feed 'train'): None until it has swept once
    last_train_sweep_finished_at: datetime | None = None
    train_calls_ok_15m: int = 0
    train_calls_total_15m: int = 0


@dataclass(frozen=True)
class Metric:
    name: str
    value: float
    unit: str  # CloudWatch unit


def compute_metrics(s: Snapshot, poll_interval_seconds: int, disk_used_pct: float) -> list[Metric]:
    m: list[Metric] = []
    if s.last_sweep_finished_at is not None:
        age = (s.now - s.last_sweep_finished_at).total_seconds() / 60
        m.append(Metric("MinutesSinceLastSweep", round(max(age, 0.0), 2), "None"))
    # A fresh database has less than an hour of history: judge coverage over what exists,
    # so a first start doesn't read as an outage for an hour. (A restart doesn't qualify:
    # its gap is real missing data.)
    window = 3600.0
    if s.first_sweep_started_at is not None:
        window = min(window, (s.now - s.first_sweep_started_at).total_seconds())
    expected = max(window / poll_interval_seconds, 1.0)
    m.append(Metric("SweepCoverage1h", round(min(s.sweeps_last_hour / expected, 1.0), 4), "None"))
    if s.calls_total_15m:
        rate = s.calls_ok_15m / s.calls_total_15m
        m.append(Metric("CallSuccessRate15m", round(rate, 4), "None"))
    if s.last_sweep_vehicles is not None:
        m.append(Metric("BusesInService", s.last_sweep_vehicles, "Count"))
    daytime = s.now.astimezone(CHICAGO).hour in DAYTIME_HOURS
    empty = s.sweeps_last_10m > 0 and s.sweeps_with_buses_last_10m == 0
    m.append(Metric("ZeroBusesDaytime", 1.0 if (daytime and empty) else 0.0, "None"))
    m.append(Metric("DiskUsedPct", round(disk_used_pct, 1), "Percent"))
    # CDC. With no slot these are all 0, which keeps the alarms quiet.
    lags = [lag for _, _, lag in s.slots if lag is not None]
    m.append(Metric("ReplicationSlotLagMB", round(max(lags, default=0) / 1_048_576, 1), "None"))
    lost = sum(1 for _, status, _ in s.slots if status in ("lost", "unreserved"))
    m.append(Metric("ReplicationSlotsLost", lost, "Count"))
    inactive = sum(1 for active, _, _ in s.slots if not active)
    m.append(Metric("ReplicationSlotsInactive", inactive, "Count"))
    # Trains: published once the train poller has swept (its alarms treat missing as fine)
    if s.last_train_sweep_finished_at is not None:
        age = (s.now - s.last_train_sweep_finished_at).total_seconds() / 60
        m.append(Metric("TrainMinutesSinceLastSweep", round(max(age, 0.0), 2), "None"))
    if s.train_calls_total_15m:
        rate = s.train_calls_ok_15m / s.train_calls_total_15m
        m.append(Metric("TrainCallSuccessRate15m", round(rate, 4), "None"))
    return m


SNAPSHOT_SQL = """
SELECT
    (SELECT MAX(finished_at) FROM ops.sweeps WHERE feed = 'bus'),
    (SELECT vehicles FROM ops.sweeps WHERE feed = 'bus' ORDER BY sweep_id DESC LIMIT 1),
    (SELECT COUNT(*) FROM ops.sweeps
        WHERE feed = 'bus' AND started_at > %(now)s - INTERVAL '1 hour'),
    (SELECT COALESCE(SUM(calls_ok), 0) FROM ops.sweeps
        WHERE feed = 'bus' AND started_at > %(now)s - INTERVAL '15 minutes'),
    (SELECT COALESCE(SUM(calls_total), 0) FROM ops.sweeps
        WHERE feed = 'bus' AND started_at > %(now)s - INTERVAL '15 minutes'),
    (SELECT COUNT(*) FROM ops.sweeps
        WHERE feed = 'bus' AND started_at > %(now)s - INTERVAL '10 minutes'),
    (SELECT COUNT(*) FROM ops.sweeps
        WHERE feed = 'bus' AND started_at > %(now)s - INTERVAL '10 minutes' AND vehicles > 0),
    (SELECT MIN(started_at) FROM ops.sweeps WHERE feed = 'bus'),
    (SELECT MAX(finished_at) FROM ops.sweeps WHERE feed = 'train'),
    (SELECT COALESCE(SUM(calls_ok), 0) FROM ops.sweeps
        WHERE feed = 'train' AND started_at > %(now)s - INTERVAL '15 minutes'),
    (SELECT COALESCE(SUM(calls_total), 0) FROM ops.sweeps
        WHERE feed = 'train' AND started_at > %(now)s - INTERVAL '15 minutes')
"""

SLOTS_SQL = """
SELECT active, wal_status,
       pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)::BIGINT
FROM pg_replication_slots
WHERE slot_type = 'logical'
"""


def read_snapshot(conn: psycopg.Connection, now: datetime) -> Snapshot:
    row = conn.execute(SNAPSHOT_SQL, {"now": now}).fetchone()
    slots = tuple((a, w, lag) for a, w, lag in conn.execute(SLOTS_SQL).fetchall())
    return Snapshot(
        now, *row[:7], first_sweep_started_at=row[7], slots=slots,
        last_train_sweep_finished_at=row[8], train_calls_ok_15m=row[9],
        train_calls_total_15m=row[10],
    )  # fmt: skip


def disk_used_pct(path: str = "/") -> float:
    usage = shutil.disk_usage(path)
    return 100 * usage.used / usage.total


def ingress_freshness(glue: Any, table: str, now: datetime) -> Metric:
    """Minutes since the Ingress table's last Iceberg commit. Glue's UpdateTime moves with
    every commit (verified 2026-09-26); Debezium commits every 5 minutes, so a larger value
    means CDC is running but not landing (the gap slot lag alone is slow to show)."""
    database, name = table.split(".", 1)
    updated = glue.get_table(DatabaseName=database, Name=name)["Table"]["UpdateTime"]
    minutes = max((now - updated).total_seconds() / 60, 0.0)
    return Metric("IngressFreshnessMinutes", round(minutes, 2), "None")


# The public site's report card must include yesterday by 10:00 CT (the nightly runs at
# 06:23 CT, with a backup start): checked from here, so it doesn't depend on GitHub.
REPORT_URL = "https://www.chitransit.fyi/data/v1/report.json"
REPORT_CHECK_EVERY = timedelta(minutes=10)
REPORT_DUE_HOUR = 10


def report_stale(days: list[str], now: datetime) -> Metric:
    """1 when it's past 10:00 CT and the public report still lacks yesterday (Chicago)."""
    local = now.astimezone(CHICAGO)
    yesterday = (local.date() - timedelta(days=1)).isoformat()
    stale = local.hour >= REPORT_DUE_HOUR and max(days, default="") < yesterday
    return Metric("SiteReportStale", 1.0 if stale else 0.0, "None")


def fetch_report_days() -> list[str]:
    return httpx.get(REPORT_URL, timeout=10).raise_for_status().json()["days"]


class CloudWatchSink:
    def __init__(self, env: str) -> None:
        import boto3  # only needed in prod

        self.namespace = f"GhostLedger/{env}"
        self.client = boto3.client("cloudwatch")

    def send(self, metrics: list[Metric], at: datetime) -> None:
        self.client.put_metric_data(
            Namespace=self.namespace,
            MetricData=[
                {"MetricName": m.name, "Value": m.value, "Unit": m.unit, "Timestamp": at}
                for m in metrics
            ],
        )


class LogSink:
    def send(self, metrics: list[Metric], at: datetime) -> None:
        log.info("metrics %s", " ".join(f"{m.name}={m.value:g}" for m in metrics))


def run(
    settings: Settings,
    dry_run: bool = False,
    stop: threading.Event | None = None,
    once: bool = False,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> list[Metric]:
    stop = stop or threading.Event()
    use_cloudwatch = settings.metrics_sink == "cloudwatch" and not dry_run
    sink = CloudWatchSink(settings.env) if use_cloudwatch else LogSink()
    glue = None
    if settings.ingress_freshness_table:
        import boto3  # only needed in prod

        glue = boto3.client("glue")
    report_days: list[str] | None = None  # the public report's days, refreshed every 10 min
    report_checked: datetime | None = None
    log.info("monitor started: env=%s sink=%s", settings.env, type(sink).__name__)
    conn: psycopg.Connection | None = None
    metrics: list[Metric] = []
    while not stop.is_set():
        at = now()
        try:
            if conn is None or conn.closed or conn.broken:
                conn = psycopg.connect(settings.database_url, autocommit=True)
            metrics = compute_metrics(
                read_snapshot(conn, at), settings.poll_interval_seconds, disk_used_pct()
            )
            if glue is not None:
                try:
                    metrics.append(ingress_freshness(glue, settings.ingress_freshness_table, at))
                except Exception:
                    # Publish nothing for it: the alarm treats missing data as breaching.
                    log.exception("could not read Ingress freshness from Glue")
            if settings.env == "prod":
                if report_checked is None or at - report_checked >= REPORT_CHECK_EVERY:
                    report_checked = at
                    try:
                        report_days = fetch_report_days()
                    except Exception:
                        report_days = None  # publish nothing: a site outage isn't this alarm
                        log.exception("could not read the public report")
                if report_days is not None:  # re-judged every minute: 10:00 CT can pass between
                    metrics.append(report_stale(report_days, at))
            sink.send(metrics, at)
        except psycopg.Error:
            # Publish nothing: missing data is what trips the poller-alive alarm.
            log.exception("could not read the database; skipping this minute")
            if conn is not None:
                conn.close()
            conn = None
        except Exception:
            log.exception("could not publish metrics; skipping this minute")
        if once:
            break
        stop.wait(PUBLISH_EVERY_SECONDS - (now() - at).total_seconds() % PUBLISH_EVERY_SECONDS)
    if conn is not None:
        conn.close()
    return metrics
