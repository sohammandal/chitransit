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

The snapshot is derived and disposable (DDIA ch. 12: a cache of the log, rebuilt from the next
sweeps), so publishing is best-effort: a failed write is logged and never fails a sweep.
Schedule-relative judgments (a gap "twice the scheduled headway") are made by the site, which
joins this feed with the nightly schedule file; the poller only reports what it saw.
"""

import gzip
import json
import logging
import os
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger("ghost_ledger.live")

TRAIL = timedelta(minutes=75)  # longer than any realistic gap
# A bus this close to either end of its pattern is laying over at a terminal, not serving
# riders: it gets no gap and is nobody's leader (verified on #9, Sep 26: pairs at 0 ft and
# at the far end showed "bunched" while parked).
TERMINAL_FT = 1000
# A bus still repeating a report older than this is left off the map (a frozen dot misleads).
STALE = timedelta(minutes=3)
# A bus with no fresh report for this long counts as dark when measuring gaps (it may be
# driving with a dead tracker), even while it is still on the map.
DARK = timedelta(minutes=2)
# Short and never stale: the file changes every 30 s and readers time their fetch to it.
CACHE_CONTROL = "public, max-age=5"


class LiveBoard:
    def __init__(self) -> None:
        # vid -> [(observed_at, pid, pdist)], oldest first
        self.trails: dict[str, deque[tuple[datetime, int, float]]] = {}
        self.latest: dict[str, dict[str, Any]] = {}
        self.lengths: dict[int, float] = {}  # pid -> pattern length (ft), from the poller
        self.dirs: dict[int, str] = {}  # pid -> rtdir ("Eastbound"), from the poller

    def seed(self, rows: list[tuple[str, datetime, int, float]]) -> None:
        """Rebuild trails from stored positions (vid, observed_at, pid, pdist), oldest first."""
        for vid, t, pid, pdist in rows:
            self.trails.setdefault(vid, deque()).append((t, int(pid), float(pdist)))

    def update(self, positions: list[dict[str, Any]], now: datetime) -> None:
        """Record this sweep's reports (parsed vehicles, repeats included) and forget the rest."""
        present = {}
        for p in positions:
            present[p["vid"]] = p
            if p.get("pid") is None or p.get("pdist") is None:
                continue
            trail = self.trails.setdefault(p["vid"], deque())
            point = (p["observed_at"], int(p["pid"]), float(p["pdist"]))
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

    def crossed_at(self, vid: str, pid: int, pdist: float) -> datetime | None:
        """The latest time bus `vid` passed `pdist` on pattern `pid` (linear between two of its
        reports), or None if its trail never crosses that spot."""
        last = None
        prev = None
        for t, p, d in self.trails.get(vid, ()):
            if prev is not None and p == pid == prev[1] and prev[2] <= pdist <= d and d > prev[2]:
                t0, _, d0 = prev
                last = t0 + (t - t0) * ((pdist - d0) / (d - d0))
            prev = (t, p, d)
        return last

    def position_at(self, vid: str, pid: int, when: datetime) -> float | None:
        """Where bus `vid` was on pattern `pid` at its last report at or before `when`."""
        d = None
        for t, p, dist in self.trails.get(vid, ()):
            if t > when:
                break
            d = dist if p == pid else None
        return d

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
        for other in seen:
            if other == vid or (other in live and live[other] < pdist):
                continue  # itself, or a reporting bus that is behind it now
            t = self.crossed_at(other, pid, pdist)
            if t is not None and t <= now and (last is None or t > last):
                last = t
        for other in seen:
            trail = self.trails[other]
            if other == vid or other in live or trail[-1][1] != pid:
                continue
            t_dark, _, d_dark = trail[-1]
            # Skip: never left the first stop, passed here while reporting, or went dark earlier.
            if d_dark < TERMINAL_FT or d_dark >= pdist or (last is not None and t_dark <= last):
                continue
            mine = self.position_at(vid, pid, t_dark)
            if mine is None or mine <= d_dark:
                return None  # it went dark ahead of this bus, short of here: it may have passed
        return None if last is None else max(0, round((now - last).total_seconds()))

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

        rows = []
        for p in sorted(self.latest.values(), key=lambda p: (p["rt"], p["vid"])):
            if now - p["observed_at"] > STALE:
                continue
            gap, ahead = gaps.get(p["vid"], (None, None))
            pid = None if p.get("pid") is None else int(p["pid"])
            rows.append({
                "vid": p["vid"], "rt": p["rt"], "pid": pid, "hdg": p.get("hdg"),
                "lat": None if p.get("lat") is None else round(float(p["lat"]), 5),
                "lon": None if p.get("lon") is None else round(float(p["lon"]), 5),
                "dly": bool(p.get("dly")), "des": p.get("des"), "dir": self.dirs.get(pid),
                "t": int(p["observed_at"].timestamp()),
                "pdist": None if p.get("pdist") is None else round(float(p["pdist"])),
                "gap_s": gap, "ahead": ahead, "at_terminal": p["vid"] in terminal,
            })  # fmt: skip
        return {
            "generated_at": now.isoformat(timespec="seconds"),
            "build": os.environ.get("GIT_SHA", "unknown"),
            "vehicles": rows,
        }


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
