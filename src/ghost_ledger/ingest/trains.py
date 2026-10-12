"""Train poller: the 'L' (CTA Train Tracker) into the same system of record as the buses.

Runs beside the bus poller on its own thread, database connection and clock, so a slow or
failing Train Tracker can't delay or break a bus sweep, nor the other way round. CTA builds a
new snapshot every 20 s and publishes it 4-5 s later (measured Oct 11: new data at :07, :27
and :47 of each minute), so a sweep runs every 20 s just after each publish (:09, :29, :49)
and makes:
  ttpositions  every in-service train on all eight lines (one call, always first);
  ttarrivals   CTA's predictions at the stations, 4 per call, right after it so they read
               the same snapshot, as many as the day's call budget allows (all ~143 stations
               about every 30 s in service hours, every minute at night): the only source of
               isSch, CTA's timetable-only arrivals with no train behind them;
  routes       CTA's status for each line, every 2 minutes (keyless, off the quota).
Everything the sweep fetched is written in ONE transaction: ops.sweeps, ops.raw_polls and
ops.raw_responses with feed 'train', each reply exactly as received. A sweep whose calls
all failed is written too: the gap is on the record, never silent.

Nothing is parsed into tables here. The raw log is the record (DDIA: one log, derived views
downstream): every train table will be built from these bodies in dbt, so a field we'd
parse wrong today, or haven't thought of yet, is never lost.

CTA cuts a key off for the rest of the day at 100,000 calls (errCd 102). CallBudget paces
the calls to train_calls_per_day and holds a 24-hour backstop under the limit, seeded from
ops.raw_polls at startup so no restart can reset it.
"""

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from ghost_ledger.config import Settings
from ghost_ledger.ingest import sql
from ghost_ledger.ingest.poller import Call
from ghost_ledger.l_stations import STATIONS
from ghost_ledger.traintracker import (
    LINES,
    MAX_STATIONS_PER_CALL,
    TrainTrackerClient,
    as_list,
    count_etas,
    count_trains,
)

log = logging.getLogger("ghost_ledger.trains")

CHICAGO = ZoneInfo("America/Chicago")
# Leave this much of each interval unused so a slow sweep can't overlap the next one.
SWEEP_SAFETY_SECONDS = 2.0
PARTITION_CHECK_EVERY = timedelta(hours=1)
LINE_STATUS_EVERY = timedelta(minutes=2)
# Sweeps start this many seconds past each 20 s mark of the clock: 2-4 s after CTA publishes.
PUBLISH_PHASE_SECONDS = 9
# Arrivals calls follow the positions call at this spacing: a round of 36 in ~13 s, inside
# one snapshot, and no burst (CTA may time out an address that sends many at once).
ARRIVALS_PACE_SECONDS = 0.35
# The budget's full rate in service hours (Chicago), half of it at night.
DAY_HOURS = range(6, 22)
NIGHT_SHARE = 0.5
# Backstops on Train Tracker calls (attempts) in any 24 hours; CTA's limit is 100,000 a day
# ("reset at midnight"). Past the first, no arrivals; past the second, no positions either.
ARRIVALS_CAP_24H = 93_000
ALL_CAP_24H = 99_000


def is_station(mapid: Any) -> bool:
    """A parent station id (4xxxx), the kind ttarrivals takes as mapid."""
    text = str(mapid or "").strip()
    return text.isdigit() and 40000 <= int(text) <= 49999


class CallBudget:
    """Paces Train Tracker calls to a daily budget, weighted to service hours.

    A token bucket refills at the day's rate (DAY_HOURS: full; night: NIGHT_SHARE of it) so
    the budget is spread over the day, never spent in a burst; retries spend tokens like any
    call. Positions go first and don't wait for a token. A 24-hour count of calls, per minute
    and seeded from the log (ops.raw_polls) at startup, is the backstop under CTA's limit.
    """

    def __init__(
        self, per_day: int, now: Callable[[], datetime], used: dict[int, int] | None = None
    ) -> None:
        self.per_day = per_day
        self.now = now
        night_hours = 24 - len(DAY_HOURS)
        self.day_rate = per_day / ((len(DAY_HOURS) + night_hours * NIGHT_SHARE) * 3600)
        self.capacity = max(self.day_rate * 60, 1.0)  # at most a minute's calls saved up
        self.tokens = self.capacity
        self._last = now()
        self.minutes: dict[int, int] = dict(used or {})  # epoch minute -> calls made

    def rate(self, at: datetime) -> float:
        day = at.astimezone(CHICAGO).hour in DAY_HOURS
        return self.day_rate if day else self.day_rate * NIGHT_SHARE

    def _refill(self) -> None:
        at = self.now()
        seconds = (at - self._last).total_seconds()
        if seconds > 0:
            self.tokens = min(self.capacity, self.tokens + seconds * self.rate(at))
        self._last = at

    def used_24h(self) -> int:
        cutoff = int(self.now().timestamp() // 60) - 24 * 60
        for minute in [m for m in self.minutes if m <= cutoff]:
            del self.minutes[minute]
        return sum(self.minutes.values())

    def allow_positions(self) -> bool:
        return self.used_24h() < ALL_CAP_24H

    def allow_arrivals(self) -> bool:
        self._refill()
        return self.tokens >= 1 and self.used_24h() < ARRIVALS_CAP_24H

    def spend(self, calls: int) -> None:
        self._refill()
        self.tokens -= calls
        minute = int(self.now().timestamp() // 60)
        self.minutes[minute] = self.minutes.get(minute, 0) + calls


@dataclass
class TrainSweepSummary:
    sweep_id: int
    calls_total: int
    calls_ok: int
    trains: int
    stations_sampled: int
    seconds: float


class TrainPoller:
    def __init__(
        self,
        settings: Settings,
        client: TrainTrackerClient,
        connect: Callable[[], psycopg.Connection] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Any] = time.sleep,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.wall = wall
        self.client = client
        self._connect = connect or (lambda: psycopg.connect(settings.database_url))
        self.now = now
        self.monotonic = monotonic
        self.sleep = sleep
        self.conn: psycopg.Connection | None = None
        # every station whose arrivals we sample: CTA's list, plus any a train reports next
        self.stations: set[str] = set(STATIONS)
        self._queue: list[str] = []
        self._next_partition_check = self.now()
        self._next_line_status = self.now()
        self.budget: CallBudget | None = None  # built from the log on the first sweep
        self._paused_until: datetime | None = None  # after CTA's "daily limit" (errCd 102)
        self._stop = threading.Event()  # run()'s: a shutdown ends a sweep's arrivals at once

    def _ensure_connection(self) -> psycopg.Connection:
        if self.conn is None or self.conn.closed or self.conn.broken:
            self.conn = self._connect()
        return self.conn

    def _ensure_partitions(self) -> None:
        """The day partitions this sweep writes into (the bus poller makes them too: both
        are idempotent, and losing a race to create one just means it exists)."""
        if self.now() < self._next_partition_check:
            return
        conn = self._ensure_connection()
        try:
            with conn.transaction():
                conn.execute(sql.ENSURE_PARTITIONS, {"today": self.now().date()})
        except (psycopg.errors.UniqueViolation, psycopg.errors.DuplicateTable):
            log.info("partition created concurrently by the bus poller")
        self._next_partition_check = self.now() + PARTITION_CHECK_EVERY

    def _ensure_budget(self) -> CallBudget:
        """The call budget, its 24-hour count read back from the log (so a restart can't
        reset it)."""
        if self.budget is None:
            conn = self._ensure_connection()
            with conn.transaction():
                rows = conn.execute(
                    sql.LOAD_TRAIN_CALLS, {"since": self.now() - timedelta(hours=24)}
                ).fetchall()
            self.budget = CallBudget(
                self.settings.train_calls_per_day, self.now, {int(m): int(n) for m, n in rows}
            )
            log.info("train call budget %d/day; %d calls in the last 24 h",
                     self.budget.per_day, self.budget.used_24h())  # fmt: skip
        return self.budget

    # ---------- one sweep ----------

    def _call(self, calls: list[Call], endpoint: str, params: dict[str, str], fn) -> Call:
        call = Call(endpoint, params, fn(), self.now())
        calls.append(call)
        return call

    def sweep(self) -> TrainSweepSummary:
        t0 = self.monotonic()
        deadline = t0 + self.settings.train_poll_interval_seconds - SWEEP_SAFETY_SECONDS
        started_at = self.now()
        self._ensure_partitions()
        budget = self._ensure_budget()
        calls: list[Call] = []
        trains = 0
        sampled = 0
        if self._paused():
            pass  # CTA's daily limit was hit: nothing but the keyless line status until midnight
        elif budget.allow_positions():
            positions = self._call(
                calls, "ttpositions", {"rt": ",".join(LINES)}, self.client.positions
            )
            budget.spend(positions.result.attempts)
            if positions.result.ok:
                trains = count_trains(positions.result.body)
                self._learn_stations(positions.result.body)
            sampled = self._sample_arrivals(calls, budget, deadline)
        else:
            log.warning("train calls in the last 24 h at %d: positions paused", budget.used_24h())
        self._sample_line_status(calls)
        self._note_daily_limit(calls)
        return self._write(started_at, calls, trains, sampled, t0)

    def _paused(self) -> bool:
        if self._paused_until is not None and self.now() >= self._paused_until:
            log.info("train calls resume after CTA's daily limit")
            self._paused_until = None
        return self._paused_until is not None

    def _note_daily_limit(self, calls: list[Call]) -> None:
        """errCd 102: the key's calls for the day are used up (our budget should never let it
        happen). Calls until CTA's reset, "at midnight" (Chicago), would only be refused."""
        if any(str(c.result.body.get("errCd")) == "102" for c in calls):
            local = self.now().astimezone(CHICAGO)
            midnight = datetime.combine(
                local.date() + timedelta(days=1), datetime.min.time(), CHICAGO
            )
            self._paused_until = midnight.astimezone(UTC) + timedelta(minutes=1)
            log.error(
                "CTA's daily Train Tracker limit reached: pausing until %s", self._paused_until
            )

    def _learn_stations(self, body: dict[str, Any]) -> None:
        for route in as_list(body.get("route")):
            for train in as_list(route.get("train") if isinstance(route, dict) else None):
                mapid = str(train.get("nextStaId") or "").strip() if isinstance(train, dict) else ""
                if is_station(mapid) and mapid not in self.stations:
                    self.stations.add(mapid)
                    log.info("sampling new station %s (%s): a train's next stop", mapid,
                             train.get("nextStaNm"))  # fmt: skip

    def _sample_line_status(self, calls: list[Call]) -> None:
        if self.now() < self._next_line_status:
            return
        self._call(calls, "routes", {"type": "rail"}, self.client.line_status)
        self._next_line_status = self.now() + LINE_STATUS_EVERY

    def _sample_arrivals(self, calls: list[Call], budget: CallBudget, deadline: float) -> int:
        """Stations' arrivals, 4 per call, round-robin over every station, as many as the
        budget allows (at most one round) before the sweep's deadline, ARRIVALS_PACE_SECONDS
        apart; a round cut short goes on where it stopped next sweep."""
        per_round = math.ceil(len(self.stations) / MAX_STATIONS_PER_CALL)
        start = self.monotonic()
        sampled = 0
        for i in range(per_round):
            self.sleep(max(0.0, start + (i + 1) * ARRIVALS_PACE_SECONDS - self.monotonic()))
            if self._stop.is_set() or self.monotonic() > deadline or not budget.allow_arrivals():
                break
            if not self._queue:
                self._queue = sorted(self.stations)
            batch = self._queue[:MAX_STATIONS_PER_CALL]
            del self._queue[:MAX_STATIONS_PER_CALL]
            call = self._call(
                calls, "ttarrivals", {"mapid": ",".join(batch)},
                lambda b=batch: self.client.arrivals(b),
            )  # fmt: skip
            budget.spend(call.result.attempts)
            sampled += len(batch)
        return sampled

    def _write(
        self, started_at: datetime, calls: list[Call], trains: int, sampled: int, t0: float
    ) -> TrainSweepSummary:
        counters = {
            "ttpositions": count_trains,
            "ttarrivals": count_etas,
            "routes": lambda body: len(as_list(body.get("RouteInfo"))),
        }
        conn = self._ensure_connection()
        with conn.transaction(), conn.cursor() as cur:
            sweep_id = cur.execute(sql.INSERT_TRAIN_SWEEP, {
                "started_at": started_at, "finished_at": self.now(),
                "calls_total": len(calls), "calls_ok": sum(c.result.ok for c in calls),
                "vehicles": trains,
            }).fetchone()[0]  # fmt: skip
            raw = []
            for call in calls:
                r = call.result
                count = counters.get(call.endpoint)
                poll_id = cur.execute(sql.INSERT_TRAIN_RAW_POLL, {
                    "sweep_id": sweep_id, "endpoint": call.endpoint,
                    "request_params": Jsonb(call.params), "fetched_at": call.fetched_at,
                    "latency_ms": r.latency_ms, "attempts": r.attempts,
                    "http_status": r.http_status, "error": r.error,
                    "vehicle_count": count(r.body) if (r.ok and count) else None,
                    "payload": Jsonb(r.body) if (not r.ok and r.body) else None,
                }).fetchone()[0]  # fmt: skip
                if r.raw is not None:
                    raw.append({"poll_id": poll_id, "fetched_at": call.fetched_at, "body": r.raw})
            cur.executemany(sql.INSERT_RAW_RESPONSE, raw)
        return TrainSweepSummary(
            sweep_id=sweep_id,
            calls_total=len(calls),
            calls_ok=sum(c.result.ok for c in calls),
            trains=trains,
            stations_sampled=sampled,
            seconds=round(self.monotonic() - t0, 2),
        )

    # ---------- the loop ----------

    def run(self, stop: threading.Event | None = None, max_sweeps: int | None = None) -> None:
        """Sweep until stopped. Nothing ends the loop but `stop`: a failed sweep is logged and
        the next tick tries again (with a fresh connection after a database error)."""
        stop = stop or threading.Event()
        self._stop = stop
        self.sleep = stop.wait  # pacing between calls ends at once on shutdown
        interval = self.settings.train_poll_interval_seconds
        log.info("train poller started: every %d s, %d stations, budget %d calls/day",
                 interval, len(self.stations), self.settings.train_calls_per_day)  # fmt: skip
        done = 0
        stop.wait(self._until_next_tick(interval))
        while not stop.is_set():
            try:
                s = self.sweep()
                log.info(
                    "train sweep %s: calls %d/%d, trains %d, stations %d, %.2fs",
                    s.sweep_id, s.calls_ok, s.calls_total, s.trains, s.stations_sampled, s.seconds,
                )  # fmt: skip
            except psycopg.Error:
                log.exception("train sweep not written; reconnecting for the next one")
                if self.conn is not None and not self.conn.closed:
                    self.conn.close()
                self.conn = None
            except Exception:  # noqa: BLE001 - logged; the thread must outlive any one sweep
                log.exception("train sweep failed")
            done += 1
            if max_sweeps is not None and done >= max_sweeps:
                break
            stop.wait(self._until_next_tick(interval))
        if self.conn is not None:
            self.conn.close()
        log.info("train poller stopped after %d sweeps", done)

    def _until_next_tick(self, interval: int) -> float:
        """Seconds to the next PUBLISH_PHASE_SECONDS past a multiple of `interval` on the wall
        clock (CTA's publishing follows the clock, so ticks do too: no drift, and a slow sweep
        skips to the next mark)."""
        return (PUBLISH_PHASE_SECONDS - self.wall()) % interval


def start(settings: Settings, stop: threading.Event) -> threading.Thread | None:
    """The train poller on its own thread, if it's on and has a key; None otherwise."""
    if not settings.train_poll:
        log.info("train collection off (TRAIN_POLL)")
        return None
    if not settings.train_api_key:
        log.warning("train collection off: CTA_TRAIN_TRACKER_API_KEY is not set")
        return None
    key = settings.train_api_key

    def work() -> None:
        with TrainTrackerClient(key) as client:
            TrainPoller(settings, client).run(stop)

    thread = threading.Thread(target=work, name="trains", daemon=True)
    thread.start()
    return thread
