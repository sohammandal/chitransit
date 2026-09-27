"""The live feed for the public site: where every bus is now, and the gap behind it.

After each committed sweep the poller hands this module the sweep's vehicle reports. It keeps
each bus's recent trail in memory and writes one JSON snapshot (docs/SITE.md, "live feed"):

  gap_s  how long ago the bus ahead (same pattern, further along) passed the spot where this
         bus is now, interpolated from the leader's trail. It is a measured wait: a rider at
         this bus's position has been waiting that long. None when the leader's trail doesn't
         reach back that far (it joined recently, or the poller restarted).

The snapshot is derived and disposable (DDIA ch. 12: a cache of the log, rebuilt from the next
sweeps), so publishing is best-effort: a failed write is logged and never fails a sweep.
Schedule-relative judgments (a gap "twice the scheduled headway") are made by the site, which
joins this feed with the nightly schedule file; the poller only reports what it saw.
"""

import gzip
import json
import logging
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
CACHE_CONTROL = "public, max-age=10, stale-while-revalidate=20"


class LiveBoard:
    def __init__(self) -> None:
        # vid -> [(observed_at, pid, pdist)], oldest first
        self.trails: dict[str, deque[tuple[datetime, int, float]]] = {}
        self.latest: dict[str, dict[str, Any]] = {}
        self.lengths: dict[int, float] = {}  # pid -> pattern length (ft), from the poller

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

    def passed_at(self, vid: str, pid: int, pdist: float) -> datetime | None:
        """When bus `vid`, on pattern `pid`, was at `pdist` (linear between its reports)."""
        before = after = None
        for t, p, d in self.trails.get(vid, ()):
            if p != pid:
                continue
            if d <= pdist:
                before = (t, d)
            elif after is None and before is not None:
                after = (t, d)
                break
        if before is None:
            return None  # its trail doesn't reach back to that spot
        if after is None or after[1] == before[1]:
            return before[0]
        frac = (pdist - before[1]) / (after[1] - before[1])
        return before[0] + (after[0] - before[0]) * frac

    def at_terminal(self, p: dict[str, Any]) -> bool:
        length = self.lengths.get(int(p["pid"]))
        d = float(p["pdist"])
        return d < TERMINAL_FT or (length is not None and d > length - TERMINAL_FT)

    def snapshot(self, now: datetime) -> dict[str, Any]:
        by_pid: dict[int, list[dict[str, Any]]] = {}
        terminal = set()
        for p in self.latest.values():
            if p.get("pid") is None or p.get("pdist") is None:
                continue
            if self.at_terminal(p):
                terminal.add(p["vid"])
            else:
                by_pid.setdefault(int(p["pid"]), []).append(p)
        gaps: dict[str, tuple[int | None, str | None]] = {}
        for pid, buses in by_pid.items():
            buses.sort(key=lambda p: p["pdist"], reverse=True)  # leader first
            gaps[buses[0]["vid"]] = (None, None)
            for leader, bus in zip(buses, buses[1:], strict=False):
                t = self.passed_at(leader["vid"], pid, float(bus["pdist"]))
                gap = None if t is None else max(0, round((now - t).total_seconds()))
                gaps[bus["vid"]] = (gap, leader["vid"])

        rows = []
        for p in sorted(self.latest.values(), key=lambda p: (p["rt"], p["vid"])):
            gap, ahead = gaps.get(p["vid"], (None, None))
            rows.append({
                "vid": p["vid"], "rt": p["rt"], "pid": p.get("pid"),
                "pdist": None if p.get("pdist") is None else round(float(p["pdist"])),
                "lat": None if p.get("lat") is None else round(float(p["lat"]), 5),
                "lon": None if p.get("lon") is None else round(float(p["lon"]), 5),
                "des": p.get("des"), "dly": bool(p.get("dly")),
                "age_s": max(0, round((now - p["observed_at"]).total_seconds())),
                "gap_s": gap, "ahead": ahead, "at_terminal": p["vid"] in terminal,
            })  # fmt: skip
        return {"version": 1, "generated_at": now.isoformat(timespec="seconds"), "buses": rows}


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

        self.bucket, self.key = url.removeprefix("s3://").split("/", 1)
        self.client = boto3.client("s3")

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
