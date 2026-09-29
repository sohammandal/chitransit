"""Bus poller: sweep every route every 30 s into the Postgres system of record.

One sweep = up to 13 getvehicles calls (10 routes each), plus any pattern fetches. All
API calls happen first; then everything the sweep learned is written in ONE transaction
(sweeps, raw_polls, raw_responses, bus_positions, live board, patterns, assignments), so
every parsed row sits next to the exact response it came from, and CDC sees each
sweep atomically, in commit order (DDIA ch. 12-13: one system of record, no dual writes).

Run with `ghost-ledger poll` (or `make poll`). Rules: docs/PLAN.md.
"""

import hashlib
import json
import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from ghost_ledger import live
from ghost_ledger.bustracker import (
    MAX_STOPS_PER_CALL,
    ApiResult,
    BusTrackerClient,
    chunked,
    parse_tmstmp,
)
from ghost_ledger.bustracker import resolve_tmstmp as resolve
from ghost_ledger.config import ROUTES_PER_CALL, Settings
from ghost_ledger.ingest import sql
from ghost_ledger.ingest.presence import PresenceTracker, Sighting, TripKey

log = logging.getLogger("ghost_ledger.poller")

# Leave this much of each interval unused so a slow sweep can't overlap the next one.
SWEEP_SAFETY_SECONDS = 2.0
PATTERN_REFRESH_EVERY = timedelta(hours=24)
PATTERN_REFRESH_ROUTES_PER_SWEEP = 5
PARTITION_CHECK_EVERY = timedelta(hours=1)
# The nightly rewrites the live schedule once a day; hourly picks it up within the hour
SCHEDULE_LOAD_EVERY = timedelta(hours=1)
SCHEDULE_RETRY_EVERY = timedelta(minutes=10)
ROUTES_REFRESH_EVERY = timedelta(hours=24)
# Each sample stop gets one getpredictions call per cycle, spread over the cycle's sweeps.
# ~730 stops -> ~73 calls per 5 min -> ~21k calls/day (docs/PLAN.md, quota). 5 min, not 10:
# a canceled trip is missing from ~27% of looks at its stops (Sep 26), so each trip needs
# several looks before "never announced" is believable.
PREDICTION_SAMPLE_EVERY = timedelta(minutes=5)
# A report's own time must be near the fetch: one wild tmstmp would have no day partition,
# and its failed insert would roll back the whole sweep. Outside the window: skip it.
REPORT_MAX_AGE = timedelta(hours=6)
REPORT_MAX_AHEAD = timedelta(minutes=10)
# CTA Customer Alerts (reroutes): keyless, off-quota. Kept raw; dbt reads them as context.
ALERTS_SAMPLE_EVERY = timedelta(minutes=10)


def direction_of(rtdir: str) -> str:
    """'Westbound' -> 'West', matching GTFS trips.direction."""
    return rtdir.removesuffix("bound")


def pattern_hash(pattern: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(pattern, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sample_stops(pattern: dict[str, Any]) -> set[str]:
    """The stops whose predictions we sample for a pattern: its first and middle stop.

    A canceled trip shows up as a prediction with dyn=1 at the stops it would serve, and
    the first stop covers trips before they start. Keep in step with sql.LOAD_SAMPLE_STOPS.
    """
    stops = [p["stpid"] for p in pattern.get("pt", []) if p.get("typ") == "S" and p.get("stpid")]
    return {str(stops[0]), str(stops[len(stops) // 2])} if stops else set()


def parse_vehicle(v: dict[str, Any], fetched_at: datetime) -> dict[str, Any]:
    """One getvehicles entry -> a bus_positions row (without poll_id / sweep_id)."""
    tmstmp_local = parse_tmstmp(v["tmstmp"])
    stsd = v.get("stsd") or None
    return {
        "vid": str(v["vid"]),
        "observed_at": resolve(tmstmp_local, fetched_at),
        "tmstmp_local": tmstmp_local,
        "fetched_at": fetched_at,
        "rt": str(v["rt"]),
        "pid": _int(v.get("pid")),
        "pdist": _int(v.get("pdist")),
        "lat": _float(v.get("lat")),
        "lon": _float(v.get("lon")),
        "hdg": _int(v.get("hdg")),
        "dly": bool(v.get("dly")) if v.get("dly") is not None else None,
        "des": v.get("des"),
        "tatripid": v.get("tatripid"),
        "tablockid": v.get("tablockid"),
        "origtatripno": v.get("origtatripno"),
        "stst": _int(v.get("stst")),
        "stsd": date.fromisoformat(stsd) if stsd else None,
        "psgld": v.get("psgld"),
    }


def next_tick(previous: float, interval: float, now: float) -> float:
    """The next scheduled start after `now`, skipping ticks a slow sweep overran."""
    tick = previous + interval
    if tick <= now:
        tick += math.ceil((now - tick) / interval) * interval
        if tick <= now:
            tick += interval
    return tick


@dataclass
class Call:
    endpoint: str
    params: dict[str, str]  # never contains the API key
    result: ApiResult
    fetched_at: datetime
    routes: list[str] = field(default_factory=list)


@dataclass
class SweepSummary:
    sweep_id: int | None
    calls_total: int
    calls_ok: int
    vehicles: int
    new_positions: int
    patterns_written: int
    assigned: int
    gone: int
    seconds: float


class Poller:
    def __init__(
        self,
        settings: Settings,
        client: BusTrackerClient,
        connect: Callable[[], psycopg.Connection] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.client = client
        self._connect = connect or (lambda: psycopg.connect(settings.database_url))
        self.now = now
        self.monotonic = monotonic
        self.conn: psycopg.Connection | None = None
        self.routes: list[str] = []
        self.patterns: dict[int, tuple[str, str]] = {}  # pid -> (rtdir, content_hash)
        self.stops: dict[str, str] = {}  # sample stop id -> its route
        self._stop_queue: list[str] = []
        self.presence = PresenceTracker(timedelta(minutes=settings.assignment_grace_minutes))
        self._refresh_queue: list[str] = []
        self._next_pattern_refresh = self.now()
        self._next_partition_check = self.now()
        self._next_routes_refresh = self.now()
        self._next_alerts = self.now()
        self._next_schedule_load = self.now()
        self._schedule_version: str | None = None
        self._pending_calls: list[Call] = []
        self.live = live.LiveBoard()
        self.live_sink = live.make_sink(settings.live_target)

    # ---------- startup and state ----------

    def start(self) -> None:
        self._ensure_connection()
        self._ensure_partitions(force=True)
        self._load_state()
        self._refresh_routes(force=True)
        log.info(
            "poller started: env=%s routes=%d patterns=%d assignments=%d grace=%s",
            self.settings.env, len(self.routes), len(self.patterns),
            len(self.presence.trips), self.presence.grace,
        )  # fmt: skip

    def _ensure_connection(self) -> psycopg.Connection:
        if self.conn is None or self.conn.closed or self.conn.broken:
            self.conn = self._connect()
        return self.conn

    def _load_state(self) -> None:
        """Rebuild in-memory state from the database, e.g. after a restart or a failed write."""
        conn = self._ensure_connection()
        with conn.transaction():
            self.patterns = {
                pid: (rtdir, h) for pid, rtdir, h in conn.execute(sql.LOAD_PATTERNS).fetchall()
            }
            self.stops = {stpid: rt for rt, stpid in conn.execute(sql.LOAD_SAMPLE_STOPS)}
            trips = {
                vid: TripKey(rt, direction, stsd, stst)
                for vid, rt, direction, stsd, stst in conn.execute(sql.LOAD_ASSIGNMENTS)
            }
            since = self.now() - max(self.presence.grace * 3, timedelta(hours=1))
            last_seen = {
                vid: (rt, fetched_at)
                for vid, rt, fetched_at in conn.execute(sql.LOAD_LAST_SEEN, {"since": since})
            }
            if self.live_sink is not None:
                board = live.LiveBoard()
                board.seed(conn.execute(sql.LOAD_TRAILS, {"since": self.now() - live.TRAIL}))
                for pid, rt, rtdir, length, points in conn.execute(sql.LOAD_PATTERN_SHAPES):
                    board.routes[pid] = rt
                    board.dirs[pid] = rtdir
                    board.stops[pid] = live.pattern_stops(points)
                    board.stop_ids[pid] = live.pattern_stop_ids(points)
                    if length is not None:
                        board.lengths[pid] = float(length)
                board.schedule = self.live.schedule  # survives a state reload
                self.live = board
        self.presence = PresenceTracker(self.presence.grace, trips, last_seen)
        # Every read above must sit inside the transaction block: a statement outside it opens
        # an implicit transaction that never commits, and every later sweep then becomes a
        # savepoint inside it (Sep 26 00:40 UTC: 9 sweeps logged, none committed).
        if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            raise RuntimeError("startup left a transaction open; sweeps would never commit")

    def _load_schedule(self) -> None:
        """The live schedule the feed judges waits by (live.Schedule): hourly, so a new nightly
        file is picked up; best-effort, keeping the last good one when a load fails."""
        target = self.settings.live_schedule
        if self.live_sink is None or not target or self.now() < self._next_schedule_load:
            return
        try:
            version = live.schedule_version(target)
            if version != self._schedule_version or self.live.schedule is None:
                self.live.schedule = live.load_schedule(target)
                self._schedule_version = version
                log.info("live schedule %s (built %s)", sorted(self.live.schedule.dates),
                         self.live.schedule.built_at)  # fmt: skip
            self._next_schedule_load = self.now() + SCHEDULE_LOAD_EVERY
            if not self.live.schedule.covers(self.now()):
                log.warning("live schedule doesn't cover today: no schedule fields until it does")
        except Exception:  # noqa: BLE001 - logged; the feed goes on without schedule fields
            log.exception("live schedule not loaded")
            self._next_schedule_load = self.now() + SCHEDULE_RETRY_EVERY

    def _ensure_partitions(self, force: bool = False) -> None:
        if not force and self.now() < self._next_partition_check:
            return
        conn = self._ensure_connection()
        with conn.transaction():
            conn.execute(sql.ENSURE_PARTITIONS, {"today": self.now().date()})
        self._next_partition_check = self.now() + PARTITION_CHECK_EVERY

    def _refresh_routes(self, force: bool = False) -> None:
        if not force and self.now() < self._next_routes_refresh:
            return
        if self.settings.poll_routes:
            self.routes = list(self.settings.poll_routes)
        else:
            call = self._call("getroutes", {}, self.client.get_routes)
            if call.result.ok and call.result.body.get("routes"):
                self.routes = [str(r["rt"]) for r in call.result.body["routes"]]
            elif not self.routes:
                raise RuntimeError(f"cannot list routes at startup: {call.result.error}")
        self._next_routes_refresh = self.now() + ROUTES_REFRESH_EVERY

    # ---------- one sweep ----------

    def _call(self, endpoint: str, params: dict[str, str], fn, routes=None) -> Call:
        result = fn()
        call = Call(endpoint, params, result, self.now(), list(routes or []))
        self._pending_calls.append(call)
        return call

    def sweep(self) -> SweepSummary:
        t0 = self.monotonic()
        deadline = t0 + self.settings.poll_interval_seconds - SWEEP_SAFETY_SECONDS
        started_at = self.now()
        self._ensure_partitions()
        self._load_schedule()
        self._refresh_routes()

        vehicle_calls: list[Call] = []
        for batch in chunked(self.routes, ROUTES_PER_CALL):
            params = {"rt": ",".join(batch), "tmres": "s"}
            if self.monotonic() > deadline:
                skipped = ApiResult(error="skipped: sweep deadline")
                call = Call("getvehicles", params, skipped, self.now(), batch)
                self._pending_calls.append(call)
            else:
                call = self._call(
                    "getvehicles", params, lambda b=batch: self.client.get_vehicles(b), batch
                )
            vehicle_calls.append(call)

        polled_routes = {rt for c in vehicle_calls if c.result.ok for rt in c.routes}
        positions: list[tuple[Call, dict[str, Any]]] = []
        for call in vehicle_calls:
            for v in call.result.body.get("vehicle", []):
                try:
                    p = parse_vehicle(v, call.fetched_at)
                except (KeyError, ValueError) as exc:
                    log.warning("skipping unparseable vehicle %s: %s", v.get("vid"), exc)
                    continue
                skew = p["observed_at"] - call.fetched_at
                if not -REPORT_MAX_AGE <= skew <= REPORT_MAX_AHEAD:
                    log.warning("skipping vehicle %s: report time %s is %s from the fetch",
                                p["vid"], p["observed_at"], skew)  # fmt: skip
                    continue
                positions.append((call, p))

        pattern_rows = self._fetch_patterns(positions, deadline)
        self._sample_predictions(deadline)
        self._sample_alerts(deadline)

        sightings = []
        for _, p in positions:
            trip = None
            known = self.patterns.get(p["pid"]) if p["pid"] is not None else None
            if known and p["stsd"] and p["stst"] is not None:
                trip = TripKey(p["rt"], direction_of(known[0]), p["stsd"], p["stst"])
            sightings.append(Sighting(p["vid"], p["rt"], trip, p["fetched_at"]))
        changes = self.presence.observe(sightings, polled_routes, started_at)

        calls, self._pending_calls = self._pending_calls, []
        try:
            summary = self._write(
                started_at, calls, positions, pattern_rows, changes, sightings, t0
            )
        except psycopg.Error:
            # The sweep is lost (visible as a gap in ops.sweeps); memory may now be ahead of
            # the database, so rebuild it from the database before the next sweep.
            log.exception("sweep write failed; reloading state from the database")
            if self.conn is not None and not self.conn.closed:
                self.conn.close()
            self.conn = None
            try:
                self._load_state()
            except psycopg.Error:
                log.exception("database still unreachable")
            raise
        # Only a committed sweep reaches the public feed.
        live.publish(self.live, self.live_sink, [p for _, p in positions], self.now())
        return summary

    def _fetch_patterns(self, positions, deadline: float) -> list[dict[str, Any]]:
        """Fetch unseen pids now; refresh known ones a few routes per sweep, once a day."""
        rows = []
        unseen = {}
        for _, p in positions:
            if p["pid"] is not None and p["pid"] not in self.patterns:
                unseen.setdefault(p["pid"], p["rt"])
        for pid, rt in unseen.items():
            if self.monotonic() > deadline:
                break
            call = self._call(
                "getpatterns", {"pid": str(pid)}, lambda p=pid: self.client.get_pattern(p), [rt]
            )
            rows += self._pattern_rows(call, rt)

        if self.now() >= self._next_pattern_refresh and not self._refresh_queue:
            self._refresh_queue = list(self.routes)
            self._next_pattern_refresh = self.now() + PATTERN_REFRESH_EVERY
        for _ in range(PATTERN_REFRESH_ROUTES_PER_SWEEP):
            if not self._refresh_queue or self.monotonic() > deadline:
                break
            rt = self._refresh_queue.pop(0)
            call = self._call(
                "getpatterns", {"rt": rt}, lambda r=rt: self.client.get_patterns(r), [rt]
            )
            rows += self._pattern_rows(call, rt)
        return rows

    def _sample_predictions(self, deadline: float) -> None:
        """Call getpredictions for this sweep's share of the sample stops. The responses are
        kept in raw_responses like every call; dyn=1 (canceled) flags are parsed downstream."""
        routes = set(self.routes)
        stops = sorted(s for s, rt in self.stops.items() if rt in routes)
        if not stops:
            return
        cycle = PREDICTION_SAMPLE_EVERY.total_seconds() / self.settings.poll_interval_seconds
        n_calls = math.ceil(len(stops) / MAX_STOPS_PER_CALL / max(cycle, 1))
        for _ in range(n_calls):
            if self.monotonic() > deadline:
                return  # the rest of the queue waits for the next sweep
            if not self._stop_queue:
                self._stop_queue = stops
            batch = self._stop_queue[:MAX_STOPS_PER_CALL]
            self._stop_queue = self._stop_queue[MAX_STOPS_PER_CALL:]
            self._call(
                "getpredictions", {"stpid": ",".join(batch), "tmres": "s"},
                lambda b=batch: self.client.get_predictions(b), [self.stops[s] for s in batch],
            )  # fmt: skip

    def _sample_alerts(self, deadline: float) -> None:
        if self.now() < self._next_alerts or self.monotonic() > deadline:
            return
        # All alerts in full-network mode; only the polled routes when POLL_ROUTES is set
        routes = list(self.settings.poll_routes or [])
        params = {"routeid": ",".join(routes)} if routes else {}
        self._call("alerts", params, lambda: self.client.get_alerts(routes), routes)
        self._next_alerts = self.now() + ALERTS_SAMPLE_EVERY

    def _pattern_rows(self, call: Call, rt: str) -> list[dict[str, Any]]:
        rows = []
        for ptr in call.result.body.get("ptr", []) if call.result.ok else []:
            pid, h = int(ptr["pid"]), pattern_hash(ptr)
            if self.patterns.get(pid, (None, None))[1] == h:
                continue
            self.patterns[pid] = (ptr["rtdir"], h)
            self.live.routes[pid] = rt
            self.live.dirs[pid] = ptr["rtdir"]
            self.live.stops[pid] = live.pattern_stops(ptr.get("pt", []))
            self.live.stop_ids[pid] = live.pattern_stop_ids(ptr.get("pt", []))
            if ptr.get("ln") is not None:
                self.live.lengths[pid] = float(ptr["ln"])
            self.stops.update(dict.fromkeys(sample_stops(ptr), rt))
            rows.append({
                "pid": pid, "rt": rt, "rtdir": ptr["rtdir"], "length_ft": _float(ptr.get("ln")),
                "content_hash": h, "pattern": Jsonb(ptr), "seen_at": call.fetched_at,
            })  # fmt: skip
        return rows

    def _write(self, started_at, calls, positions, pattern_rows, changes, sightings, t0):
        conn = self._ensure_connection()
        finished_at = self.now()
        vehicle_calls = [c for c in calls if c.endpoint == "getvehicles"]
        with conn.transaction(), conn.cursor() as cur:
            sweep_id = cur.execute(sql.INSERT_SWEEP, {
                "started_at": started_at, "finished_at": finished_at,
                "calls_total": len(vehicle_calls),
                "calls_ok": sum(c.result.ok for c in vehicle_calls),
                "vehicles": len(positions),
            }).fetchone()[0]  # fmt: skip

            poll_ids: dict[int, int] = {}
            raw = []
            for call in calls:
                r = call.result
                poll_ids[id(call)] = cur.execute(sql.INSERT_RAW_POLL, {
                    "sweep_id": sweep_id, "endpoint": call.endpoint,
                    "request_params": Jsonb(call.params), "fetched_at": call.fetched_at,
                    "latency_ms": r.latency_ms, "attempts": r.attempts,
                    "http_status": r.http_status, "error": r.error,
                    "api_errors": Jsonb(r.api_errors) if r.api_errors else None,
                    "vehicle_count": len(r.body.get("vehicle", [])) if r.ok else None,
                    "payload": Jsonb(r.body) if (not r.ok and r.body) else None,
                }).fetchone()[0]  # fmt: skip
                if r.raw is not None:
                    raw.append(
                        {
                            "poll_id": poll_ids[id(call)],
                            "fetched_at": call.fetched_at,
                            "body": r.raw,
                        }
                    )
            cur.executemany(sql.INSERT_RAW_RESPONSE, raw)

            rows = [
                {**p, "poll_id": poll_ids[id(call)], "sweep_id": sweep_id} for call, p in positions
            ]
            cur.executemany(sql.INSERT_POSITION, rows)
            new_positions = cur.rowcount if rows else 0
            cur.executemany(sql.UPSERT_LIVE_BOARD, rows)

            if pattern_rows:
                cur.executemany(sql.UPSERT_PATTERN, pattern_rows)

            by_vid = {r["vid"]: r for r in rows}
            assigned = []
            for s in changes.assigned:
                p = by_vid[s.vid]
                assigned.append({
                    "vid": s.vid, "rt": s.trip.rt, "direction": s.trip.direction,
                    "stsd": s.trip.stsd, "stst": s.trip.stst, "pid": p["pid"],
                    "tablockid": p["tablockid"], "tatripid": p["tatripid"],
                    "origtatripno": p["origtatripno"], "first_seen_at": p["observed_at"],
                    "sweep_id": sweep_id,
                })  # fmt: skip
            if assigned:
                cur.executemany(sql.UPSERT_ASSIGNMENT, assigned)
            if changes.gone:
                gone = [{"vid": vid} for vid in changes.gone]
                cur.executemany(sql.DELETE_ASSIGNMENT, gone)
                cur.executemany(sql.DELETE_LIVE_BOARD, gone)

        return SweepSummary(
            sweep_id=sweep_id,
            calls_total=len(vehicle_calls),
            calls_ok=sum(c.result.ok for c in vehicle_calls),
            vehicles=len(positions),
            new_positions=new_positions,
            patterns_written=len(pattern_rows),
            assigned=len(assigned),
            gone=len(changes.gone),
            seconds=round(self.monotonic() - t0, 2),
        )

    # ---------- the loop ----------

    def run(self, stop: threading.Event | None = None, max_sweeps: int | None = None) -> None:
        stop = stop or threading.Event()
        self.start()
        interval = self.settings.poll_interval_seconds
        tick = self.monotonic()
        done = 0
        while not stop.is_set():
            try:
                s = self.sweep()
                log.info(
                    "sweep %s: calls %d/%d, buses %d, new positions %d, patterns %d, "
                    "assigned %d, gone %d, %.2fs",
                    s.sweep_id, s.calls_ok, s.calls_total, s.vehicles, s.new_positions,
                    s.patterns_written, s.assigned, s.gone, s.seconds,
                )  # fmt: skip
            except psycopg.Error:
                pass  # logged in sweep(); the next tick retries with a fresh connection
            done += 1
            if max_sweeps is not None and done >= max_sweeps:
                break
            tick = next_tick(tick, interval, self.monotonic())
            stop.wait(max(0.0, tick - self.monotonic()))
        if self.conn is not None:
            self.conn.close()
        log.info("poller stopped after %d sweeps", done)
