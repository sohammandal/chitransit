"""The live feed for the public site: where every bus is now, and the gap behind it.

After each committed sweep the poller hands this module the sweep's vehicle reports. It keeps
each bus's recent trail in memory and writes one JSON snapshot (docs/SITE.md, "live feed"):

  {"generated_at": ISO, "build": commit, "vehicles": [{vid, rt, pid, hdg, lat, lon, dly,
                                      des, dir, t, pdist, gap_s, ahead, at_terminal}]}
  build  the commit the running poller was built from (CI waits for it after a deploy)
  t      the position's own time (epoch seconds); the site shows its age

  gap_s  how long ago the last bus on this pattern passed the spot where this bus is now,
         interpolated from the trails. It is a measured wait: a rider at this bus's position
         has been waiting that long. The last bus may be one whose tracker has since died (it
         left the feed but still passed here). None when no trail reaches back that far (a
         bus joined recently, or the poller restarted), or when a bus went dark short of this
         spot after the last recorded pass: it may have passed since, so no wait is claimed.
  ahead  the nearest reporting bus ahead on the same pattern (None for the first).
  near   the name of the stop nearest the bus ("Clark & Belmont"), so the site can say where.

  Measured at the bus's next stop, counting every bus of the route in the same direction
  (short turns and branches too: a bus on another pattern that stopped here is service):
  stop_gap_s   how long ago the last bus passed the next stop; None as for gap_s
  last_by      the bus that made that pass
  next_stop    the stop's name; next_stpid: its CTA stop id
  A pass is interpolated between two reports of one trip (pid, stsd, stst), forward progress
  only, reports at most 5 minutes apart, as the nightly's int_stop_passes. At a pattern's
  start (its first TERMINAL_FT) a bus laying over can creep past its first stops long before
  it leaves, so there the pass is its last crossing before it leaves, and only once it has.
  (Sep 28: 16% of trips still sat at their first stop 2+ minutes after the naive pass.)

The snapshot is derived and disposable (DDIA ch. 12: a cache of the log, rebuilt from the next
sweeps), so publishing is best-effort: a failed write is logged and never fails a sweep.
Schedule-relative judgments (a gap "twice the scheduled headway") are made by the site, which
joins this feed with the nightly schedule file; the poller only reports what it saw.
"""

import gzip
import json
import logging
import os
from bisect import bisect_left
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger("ghost_ledger.live")

# Longer than any realistic gap: Sep 28 morning, J14 riders past Lake Shore Drive waited 79+ min
# in a jam, and a 75-min window could only call that unknown. ~0.7 s per snapshot at 150.
TRAIL = timedelta(minutes=150)
# gap_s (the same-pattern gap the site reads today) keeps its original 75-minute view, so this
# change leaves it exactly as it was (replayed: 27,077 bus snapshots, 0 differences).
GAP_WINDOW = timedelta(minutes=75)
# A bus this close to either end of its pattern is laying over at a terminal, not serving
# riders: it gets no gap and is nobody's leader (verified on #9, Sep 26: pairs at 0 ft and
# at the far end showed "bunched" while parked).
TERMINAL_FT = 1000
# A bus still repeating a report older than this is left off the map (a frozen dot misleads).
STALE = timedelta(minutes=3)
# A bus with no fresh report for this long counts as dark when measuring gaps (it may be
# driving with a dead tracker), even while it is still on the map.
DARK = timedelta(minutes=2)
# A dark bus only casts doubt on a stop it could have reached: buses dark for a while moved
# at most ~1,700 ft/min in 95% of Sep 28's blackouts (~4,000 on Lake Shore Drive); 3,000 ft/min
# (34 mph) is a generous ceiling. And only for so long: long blackouts are mostly parked buses
# (Sep 28: 20+ min, 68% hadn't moved), and replaying the day, doubting for 5 minutes hid the
# fewest real long waits without adding false ones.
DARK_REACH_FT_PER_MIN = 3000
# ...and in practice about its own pace: twice its last speed, at least this (the median of
# buses that moved while dark, Sep 28: ~630 ft/min). Replayed on Sep 28 against the fixed
# ceiling: the same false flags, a quarter fewer missed long waits (81 vs 105).
DARK_REACH_FLOOR = 600
DARK_DOUBT = timedelta(minutes=5)
# A report this overdue (a sweep and a half) may hide a pass: a bus last seen just short of a
# stop has likely gone past it by now, even before it counts as dark (DARK) on the map.
OVERDUE = timedelta(seconds=45)
# A trip is under way once the bus is this far past the lowest point it reported on the trip:
# a bus laying over creeps a few dozen feet at most, while a bus that left, even in downtown
# rush-hour traffic, covers more within a report or two (Sep 28 #146: 0 -> 523 ft in 9 s).
START_FT = 150
# Two reports further apart than this aren't interpolated (the bus dropped out of the feed in
# between), as in the nightly's int_stop_passes.
STEP = timedelta(minutes=5)
# Short and never stale: the file changes every 30 s and readers time their fetch to it.
CACHE_CONTROL = "public, max-age=5"


class LiveBoard:
    def __init__(self) -> None:
        # vid -> [(observed_at, pid, pdist, trip)], oldest first; trip = (stsd, stst) or None
        self.trails: dict[str, deque[tuple[datetime, int, float, tuple | None]]] = {}
        self.latest: dict[str, dict[str, Any]] = {}
        self.lengths: dict[int, float] = {}  # pid -> pattern length (ft), from the poller
        self.dirs: dict[int, str] = {}  # pid -> rtdir ("Eastbound"), from the poller
        self.stops: dict[int, list[tuple[float, str]]] = {}  # pid -> [(feet, name)], sorted
        self.stop_ids: dict[int, list[tuple[float, str, str]]] = {}  # pid -> [(feet, stpid, name)]
        self.routes: dict[int, str] = {}  # pid -> rt, from the poller and the reports

    def seed(self, rows) -> None:
        """Rebuild trails from stored positions (vid, observed_at, pid, pdist[, stsd, stst]),
        oldest first."""
        for vid, t, pid, pdist, *trip in rows:
            key = (trip[0], trip[1]) if len(trip) == 2 and None not in trip else None
            self.trails.setdefault(vid, deque()).append((t, int(pid), float(pdist), key))

    def update(self, positions: list[dict[str, Any]], now: datetime) -> None:
        """Record this sweep's reports (parsed vehicles, repeats included) and forget the rest."""
        present = {}
        for p in positions:
            present[p["vid"]] = p
            if p.get("pid") is None or p.get("pdist") is None:
                continue
            trail = self.trails.setdefault(p["vid"], deque())
            if p.get("rt"):
                self.routes.setdefault(int(p["pid"]), p["rt"])
            trip = (p["stsd"], p["stst"]) if p.get("stsd") and p.get("stst") is not None else None
            point = (p["observed_at"], int(p["pid"]), float(p["pdist"]), trip)
            if not trail or trail[-1][0] < point[0]:
                trail.append(point)
        self.latest = present
        cutoff = now - TRAIL
        for vid in list(self.trails):
            trail = self.trails[vid]
            while trail and trail[0][0] < cutoff:
                trail.popleft()
            if not trail:
                del self.trails[vid]

    def crossed_at(
        self, vid: str, pid: int, pdist: float, since: datetime | None = None
    ) -> datetime | None:
        """The latest time bus `vid` passed `pdist` on pattern `pid` (linear between two of its
        reports since `since`), or None if its trail never crosses that spot. Scans back from
        the newest report: the first crossing found is the latest."""
        later = None
        for t, p, d, _ in reversed(self.trails.get(vid, ())):
            if since is not None and t < since:
                break
            if later is not None:
                t1, p1, d1 = later
                if p == pid == p1 and d <= pdist <= d1 and d1 > d:
                    return t + (t1 - t) * ((pdist - d) / (d1 - d))
            later = (t, p, d)
        return None

    def position_at(
        self, vid: str, pid: int, when: datetime, since: datetime | None = None
    ) -> float | None:
        """Where bus `vid` was on pattern `pid` at its last report at or before `when` (and not
        before `since`)."""
        d = None
        for t, p, dist, _ in self.trails.get(vid, ()):
            if since is not None and t < since:
                continue
            if t > when:
                break
            d = dist if p == pid else None
        return d

    def passes(self) -> dict[tuple[str, str, str], list[tuple[datetime, str]]]:
        """Every stop pass in the trails: (rt, rtdir, stpid) -> [(passed_at, vid)]. Derived from
        the trails on each snapshot, so it is the same after a restart, eviction or a pattern
        that arrives late (no second copy to keep in step)."""
        out: dict[tuple[str, str, str], list[tuple[datetime, str]]] = {}
        for vid, trail in self.trails.items():
            trips: dict[tuple, list[tuple[datetime, float]]] = {}
            for t, pid, d, trip in trail:
                if trip is not None:
                    trips.setdefault((pid, trip), []).append((t, d))
            for (pid, _), points in trips.items():
                stops, rt, rtdir = self.stop_ids.get(pid), self.routes.get(pid), self.dirs.get(pid)
                if not stops or rt is None or rtdir is None:
                    continue
                for passed_at, stpid in trip_passes(points, stops):
                    out.setdefault((rt, rtdir, stpid), []).append((passed_at, vid))
        return out

    def under_way(self, vid: str) -> bool:
        """Whether the bus's current trip has left its start (departed_at). A bus without a trip
        key can't be judged here and counts as under way (the terminal zone still applies)."""
        trail = self.trails.get(vid)
        if not trail or trail[-1][3] is None:
            return True
        _, pid, _, trip = trail[-1]
        points = [(t, d) for t, p, d, k in trail if p == pid and k == trip]
        return departed_at(points) is not None

    def dark_buses(
        self, now: datetime
    ) -> dict[tuple[str, str], list[tuple[str, datetime, int, float, float]]]:
        """Buses whose report is overdue (OVERDUE) that had got under way, by (rt, rtdir) of
        their last report: [(vid, last report time, pid, pdist, reach in ft/min)]. The reach is
        how fast it could plausibly have gone on since: twice its last speed, at least
        DARK_REACH_FLOOR, at most DARK_REACH_FT_PER_MIN. Built once per snapshot."""
        out: dict[tuple[str, str], list[tuple[str, datetime, int, float, float]]] = {}
        for vid, trail in self.trails.items():
            t, pid, d, trip = trail[-1]
            if now - t < OVERDUE or d < TERMINAL_FT or not self.under_way(vid):
                continue  # reporting, never left its first stop, or still at its start
            key = (self.routes.get(pid), self.dirs.get(pid))
            if None in key:
                continue
            speed = 0.0
            if len(trail) > 1:
                t0, p0, d0, k0 = trail[-2]
                if (p0, k0) == (pid, trip) and t > t0:
                    speed = max(0.0, (d - d0) / ((t - t0).total_seconds() / 60))
            reach = min(DARK_REACH_FT_PER_MIN, max(2 * speed, DARK_REACH_FLOOR))
            out.setdefault(key, []).append((vid, t, pid, d, reach))
        return out

    def stop_gap(
        self,
        vid: str,
        pid: int,
        pdist: float,
        index: dict[tuple[str, str, str], list[tuple[datetime, str]]],
        dark: dict[tuple[str, str], list[tuple[str, datetime, int, float, float]]],
        stop_at: dict[int, dict[str, list[float]]],
        now: datetime,
    ) -> tuple[int | None, str | None, tuple[str, str] | None]:
        """(seconds since any other bus of the route and direction passed this bus's next stop,
        the bus that did, (next stpid, name)). dark: dark_buses(); stop_at: pid -> stpid ->
        [feet along], for finding the stop on another bus's pattern."""
        stops, rt, rtdir = self.stop_ids.get(pid), self.routes.get(pid), self.dirs.get(pid)
        if not stops or rt is None or rtdir is None:
            return None, None, None
        i = bisect_left(stops, (pdist,))
        if i == len(stops):
            return None, None, None  # past its last stop
        at, stpid, name = stops[i]
        last, last_by = None, None
        for t, other in index.get((rt, rtdir, stpid), ()):
            if other != vid and t <= now and (last is None or t > last):
                last, last_by = t, other
        # A bus that went dark short of the stop, after the last pass and nearer the stop than
        # this bus was then, may have passed since: claim no wait.
        for other, t_dark, p_dark, d_dark, rate in dark.get((rt, rtdir), ()):
            if other == vid or (last is not None and t_dark <= last) or now - t_dark > DARK_DOUBT:
                continue
            ahead = [a for a in stop_at.get(p_dark, {}).get(stpid, ()) if a >= d_dark]
            if not ahead:
                continue  # the stop isn't ahead of it on its pattern (or it went past)
            reach = rate * (now - t_dark).total_seconds() / 60
            if min(ahead) - d_dark > reach:
                continue  # too far to have got there yet
            mine = self.position_at(vid, pid, t_dark)
            if mine is None or at - mine >= min(ahead) - d_dark:
                return None, None, (stpid, name)
        if last is None:
            return None, None, (stpid, name)
        return max(0, round((now - last).total_seconds())), last_by, (stpid, name)

    def gap(
        self,
        vid: str,
        pid: int,
        pdist: float,
        live: dict[str, float],
        seen: list[str],
        now: datetime,
    ) -> int | None:
        """Seconds since the last bus passed this bus's spot (see the module docstring).
        live: vid -> current pdist of the reporting buses on this pattern; seen: every bus with
        trail on this pattern."""
        last = None
        since = now - GAP_WINDOW
        for other in seen:
            if other == vid or (other in live and live[other] < pdist):
                continue  # itself, or a reporting bus that is behind it now
            t = self.crossed_at(other, pid, pdist, since)
            if t is not None and t <= now and (last is None or t > last):
                last = t
        for other in seen:
            trail = self.trails[other]
            if other == vid or other in live or trail[-1][1] != pid or trail[-1][0] < since:
                continue
            t_dark, _, d_dark, _ = trail[-1]
            # Skip: never left the first stop, passed here while reporting, or went dark earlier.
            if d_dark < TERMINAL_FT or d_dark >= pdist or (last is not None and t_dark <= last):
                continue
            mine = self.position_at(vid, pid, t_dark, since)
            if mine is None or mine <= d_dark:
                return None  # it went dark ahead of this bus, short of here: it may have passed
        return None if last is None else max(0, round((now - last).total_seconds()))

    def near(self, pid: int | None, pdist: float | None) -> str | None:
        """The name of the stop on pattern `pid` nearest to `pdist` feet along it."""
        stops = self.stops.get(pid) if pid is not None else None
        if not stops or pdist is None:
            return None
        i = bisect_left(stops, (pdist, ""))
        candidates = stops[max(0, i - 1) : i + 1]
        return min(candidates, key=lambda s: abs(s[0] - pdist))[1]

    def at_terminal(self, p: dict[str, Any]) -> bool:
        length = self.lengths.get(int(p["pid"]))
        d = float(p["pdist"])
        return d < TERMINAL_FT or (length is not None and d > length - TERMINAL_FT)

    def snapshot(self, now: datetime) -> dict[str, Any]:
        by_pid: dict[int, list[dict[str, Any]]] = {}
        live: dict[int, dict[str, float]] = {}  # pid -> vid -> pdist, reporting buses only
        terminal = set()
        for p in self.latest.values():
            if p.get("pid") is None or p.get("pdist") is None:
                continue
            if now - p["observed_at"] <= DARK:
                live.setdefault(int(p["pid"]), {})[p["vid"]] = float(p["pdist"])
            if self.at_terminal(p):
                terminal.add(p["vid"])
            else:
                by_pid.setdefault(int(p["pid"]), []).append(p)
        seen: dict[int, list[str]] = {}  # pid -> buses with trail on it
        for vid, trail in self.trails.items():
            for p in {point[1] for point in trail}:
                seen.setdefault(p, []).append(vid)
        gaps: dict[str, tuple[int | None, str | None]] = {}
        for pid, buses in by_pid.items():
            buses.sort(key=lambda p: p["pdist"], reverse=True)  # leader first
            for i, bus in enumerate(buses):
                gap = self.gap(
                    bus["vid"], pid, float(bus["pdist"]), live.get(pid, {}), seen.get(pid, []), now
                )
                gaps[bus["vid"]] = (gap, buses[i - 1]["vid"] if i else None)
        index = self.passes()
        dark = self.dark_buses(now)
        stop_at: dict[int, dict[str, list[float]]] = {}
        for p_, stops in self.stop_ids.items():
            for feet, sid, _ in stops:
                stop_at.setdefault(p_, {}).setdefault(sid, []).append(feet)
        stop_gaps = {
            bus["vid"]: self.stop_gap(
                bus["vid"], pid, float(bus["pdist"]), index, dark, stop_at, now
            )
            for pid, buses in by_pid.items()
            for bus in buses
            if self.under_way(bus["vid"])  # still at its start: not waited on by anyone yet
        }

        rows = []
        for p in sorted(self.latest.values(), key=lambda p: (p["rt"], p["vid"])):
            if now - p["observed_at"] > STALE:
                continue
            gap, ahead = gaps.get(p["vid"], (None, None))
            stop_gap, last_by, next_stop = stop_gaps.get(p["vid"], (None, None, None))
            pid = None if p.get("pid") is None else int(p["pid"])
            rows.append({
                "vid": p["vid"], "rt": p["rt"], "pid": pid, "hdg": p.get("hdg"),
                "lat": None if p.get("lat") is None else round(float(p["lat"]), 5),
                "lon": None if p.get("lon") is None else round(float(p["lon"]), 5),
                "dly": bool(p.get("dly")), "des": p.get("des"), "dir": self.dirs.get(pid),
                "t": int(p["observed_at"].timestamp()),
                "pdist": None if p.get("pdist") is None else round(float(p["pdist"])),
                "gap_s": gap, "ahead": ahead, "at_terminal": p["vid"] in terminal,
                "near": self.near(pid, p.get("pdist")),
                "stop_gap_s": stop_gap, "last_by": last_by,
                "next_stpid": next_stop[0] if next_stop else None,
                "next_stop": next_stop[1] if next_stop else None,
            })  # fmt: skip
        return {
            "generated_at": now.isoformat(timespec="seconds"),
            "build": os.environ.get("GIT_SHA", "unknown"),
            "vehicles": rows,
        }


def departed_at(points: list[tuple[datetime, float]]) -> datetime | None:
    """When a trip got under way: its first report TERMINAL_FT past the lowest point it has
    reported so far, or None while it is still at its start. Measured from the trip's own
    start, not the pattern's: #146 buses lay over 2,500 ft into their pattern (Sep 28), and a
    new trip's first reports can jump ahead and back before the bus settles."""
    lowest = float("inf")
    for t, d in points:
        lowest = min(lowest, d)
        if d >= lowest + START_FT:
            return t
    return None


def trip_passes(
    points: list[tuple[datetime, float]], stops: list[tuple[float, str, str]]
) -> list[tuple[datetime, str]]:
    """When one trip passed each stop: [(passed_at, stpid)]. points: its reports (time, pdist),
    oldest first; stops: its pattern's [(feet, stpid, name)], sorted.

    Before the trip gets under way (departed_at), a bus laying over creeps back and forth: each
    stop it crosses keeps its last crossing, which is when the bus left it, and none count
    until it has left. From then on, the nightly's rule (int_stop_passes): a step from a report
    at the furthest point reached so far (counting from departure) to a report further on
    passes the stops between them, inclusive and interpolated; each stop keeps its earliest
    pass. Across a blackout (reports more than STEP apart) the time in between is unknown, so
    those stops are passed at the later report: the latest the bus can have passed them. That
    can only understate a wait, never inflate it (Sep 28: J14 buses go dark for 20+ minutes on
    Lake Shore Drive, and the stops after it would otherwise never be passed)."""
    left = departed_at(points)
    if left is None:
        return []
    # The bus sits at its start until the report before the one that shows it gone.
    waited = max((t for t, _ in points if t < left), default=left)
    starting: dict[int, datetime] = {}
    running: dict[int, datetime] = {}
    furthest = float("-inf")
    for (t0, d0), (t1, d1) in zip(points, points[1:], strict=False):
        before = t1 <= left
        if not before:
            at_furthest = d0 >= furthest
            furthest = max(furthest, d0)
        if d1 <= d0 or (not before and not at_furthest):
            continue
        blackout = t1 - t0 > STEP
        for k in range(bisect_left(stops, (d0,)), len(stops)):
            at = stops[k][0]
            if at > d1:
                break
            when = t1 if blackout else t0 + (t1 - t0) * ((at - d0) / (d1 - d0))
            if before:
                starting[k] = max(when, waited)  # still there until it left
            elif k not in running and k not in starting:
                running[k] = when
    return [(when, stops[k][1]) for k, when in sorted({**running, **starting}.items())]


def pattern_stop_ids(points: list[dict[str, Any]]) -> list[tuple[float, str, str]]:
    """[(feet along, stpid, name)] for a Bus Tracker pattern's stops, sorted."""
    return sorted(
        (float(p["pdist"]), str(p["stpid"]), p.get("stpnm") or "")
        for p in points or []
        if p.get("typ") == "S" and p.get("pdist") is not None and p.get("stpid")
    )


def pattern_stops(points: list[dict[str, Any]]) -> list[tuple[float, str]]:
    """[(feet along, stop name)] for a Bus Tracker pattern's points (stops only), sorted."""
    return sorted(
        (float(p["pdist"]), p["stpnm"])
        for p in points or []
        if p.get("typ") == "S" and p.get("pdist") is not None and p.get("stpnm")
    )


class Sink(Protocol):
    def write(self, snapshot: dict[str, Any]) -> None: ...


class FileSink:
    """Dev: write the snapshot where the site's dev server can read it."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def write(self, snapshot: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(snapshot, separators=(",", ":")))
        tmp.replace(self.path)  # readers never see a half-written file


class S3Sink:
    """Prod: one object, gzip-encoded, short cache life (CloudFront serves it as is)."""

    def __init__(self, url: str) -> None:
        import boto3  # only needed in prod
        from botocore.config import Config

        self.bucket, self.key = url.removeprefix("s3://").split("/", 1)
        # Tight timeouts: a slow S3 must never hold up the next sweep (the feed is best-effort;
        # the next sweep rewrites the whole snapshot anyway).
        self.client = boto3.client(
            "s3",
            config=Config(connect_timeout=2, read_timeout=3, retries={"max_attempts": 1}),
        )

    def write(self, snapshot: dict[str, Any]) -> None:
        body = gzip.compress(json.dumps(snapshot, separators=(",", ":")).encode(), mtime=0)
        self.client.put_object(
            Bucket=self.bucket, Key=self.key, Body=body, ContentType="application/json",
            ContentEncoding="gzip", CacheControl=CACHE_CONTROL,
        )  # fmt: skip


def make_sink(target: str | None) -> Sink | None:
    if not target:
        return None
    return S3Sink(target) if target.startswith("s3://") else FileSink(target)


def publish(board: LiveBoard, sink: Sink | None, positions: list[dict], now: datetime) -> None:
    """Best-effort: the feed is a disposable view; never let it fail the sweep."""
    try:
        board.update(positions, now)
        if sink is not None:
            sink.write(board.snapshot(now))
    except Exception:  # noqa: BLE001 - logged; the next sweep rewrites the whole snapshot
        log.exception("live feed not published this sweep")
