"""The 'L' as the site draws it: site/public/l/lines.json, from CTA's GTFS zip.

Usage: uv run python scripts/l_lines.py --zip google_transit.zip [--out site/public/l/lines.json]
    [--paths src/ghost_ledger/l_paths.json]

  {"feed": "<the zip's date>", "lines": {code: {"name", "color", "text", "shapes": [[[lon, lat],
   ...]], "w": [n, ...], "stations": [[mapid, ...], ...]}}, "stations": {mapid: {"name", "lon",
   "lat", "lines": [code, ...]}}}

code is the line's Train Tracker code (red, blue, brn, g, org, p, pink, y): the live feed's
too (ghost_ledger.live_trains). A line's shapes are the paths most of its trips run (a branch,
the Loop), simplified to ~4 m; each shape's stations are its trips' stops in order, as parent
stations (the 4xxxx map ids Train Tracker uses). Where lines share track (the Loop, Lake Street)
the shapes are cut into pieces, each with its width `w` in stripes: the site draws a stretch n
lines share as n nested stripes, the first in CTA's order widest, so each line's colour shows
whichever way it runs (side-by-side offsets swap sides where lines circle the Loop opposite
ways). Static: re-run it when CTA changes the 'L' (a new station, a new path); the live feed
never waits on it. It also writes the train poller's l_paths.json, {"feed", "paths": {code:
[[[mapid, platform], ...], ...]}}: each path's stations with their platform (GTFS stop_id =
Train Tracker's stpId), in order, to time trains past each platform.
"""

import argparse
import csv
import io
import json
import math
import zipfile
from collections import Counter, defaultdict
from itertools import pairwise
from pathlib import Path

CODES = {"Red": "red", "Blue": "blue", "Brn": "brn", "G": "g", "Org": "org", "P": "p",
         "Pink": "pink", "Y": "y"}  # fmt: skip
MIN_SHARE = 0.05  # a shape fewer than 5% of a line's trips run is a rare variant: left out
EPSILON = 0.00004  # degrees, ~4 m: the simplified track never strays further from CTA's
SHARED_M = 25  # another line's track this close and parallel is the same track
STEP_M = 40  # shared track is checked every 40 m along a shape
ALONE = {"blue"}  # runs on its own track: its subway under Lake Street lies along the Loop lines'


def rows(zf: zipfile.ZipFile, name: str):
    with zf.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))


def simplify(points: list[tuple[float, float]], eps: float = EPSILON) -> list[tuple[float, float]]:
    """Douglas-Peucker, iterative (a long shape would overflow recursion)."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = points[a], points[b]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        far, at = 0.0, -1
        for i in range(a + 1, b):
            px, py = points[i]
            # a closed shape (a Loop line's: terminal, Loop, terminal) starts where it ends:
            # with no chord, the distance is from the point itself
            if norm:
                d = abs(dy * (px - ax) - dx * (py - ay)) / norm
            else:
                d = math.hypot(px - ax, py - ay)
            if d > far:
                far, at = d, i
        if far > eps:
            keep[at] = True
            stack += [(a, at), (at, b)]
    return [p for p, k in zip(points, keep, strict=True) if k]


def bundle(lines: dict) -> None:
    """Cut each line's shapes where other lines share its track; set each piece's width `w`
    (lines sharing it, minus the lines before this one in CTA's order)."""
    order = list(CODES.values())
    lat0 = math.radians(41.88)

    def xy(p):
        return (p[0] * math.cos(lat0) * 111_320, p[1] * 111_320)

    cell = 200.0
    grid: dict[tuple[int, int], list[tuple[str, tuple, tuple]]] = defaultdict(list)

    def cells(lo: float, hi: float) -> range:
        return range(int((lo - SHARED_M) // cell), int((hi + SHARED_M) // cell) + 1)

    for rt, line in lines.items():
        for shape in line["shapes"]:
            for a, b in pairwise(shape):
                (ax, ay), (bx, by) = xy(a), xy(b)
                for i in cells(min(ax, bx), max(ax, bx)):
                    for j in cells(min(ay, by), max(ay, by)):
                        grid[(i, j)].append((rt, (ax, ay), (bx, by)))

    def sharing(rt: str, m, d) -> set[str]:
        """Other lines on the same track as the step at m heading d (a unit vector)."""
        found = set()
        for other, (ax, ay), (bx, by) in grid[(int(m[0] // cell), int(m[1] // cell))]:
            if other == rt or other in found or {rt, other} & ALONE:
                continue
            ex, ey = bx - ax, by - ay
            n = math.hypot(ex, ey)
            if not n or abs(d[0] * ey - d[1] * ex) / n > 0.42:  # more than ~25 degrees apart
                continue
            t = max(0.0, min(1.0, ((m[0] - ax) * ex + (m[1] - ay) * ey) / (n * n)))
            if math.hypot(m[0] - ax - t * ex, m[1] - ay - t * ey) <= SHARED_M:
                found.add(other)
        return found

    for rt, line in lines.items():
        pieces, widths = [], []
        for shape in line["shapes"]:
            cur, w_cur = [shape[0]], None
            for a, b in pairwise(shape):
                (ax, ay), (bx, by) = xy(a), xy(b)
                length = math.hypot(bx - ax, by - ay)
                if not length:
                    continue
                d = ((bx - ax) / length, (by - ay) / length)
                steps = max(1, math.ceil(length / STEP_M))
                for k in range(1, steps + 1):
                    f0, f1 = (k - 1) / steps, k / steps
                    m = (ax + (f0 + f1) / 2 * (bx - ax), ay + (f0 + f1) / 2 * (by - ay))
                    on = sorted(sharing(rt, m, d) | {rt}, key=order.index)
                    w = len(on) - on.index(rt)
                    end = [a[0] + f1 * (b[0] - a[0]), a[1] + f1 * (b[1] - a[1])]
                    if w_cur is not None and w != w_cur:
                        pieces.append(cur)
                        widths.append(w_cur)
                        cur = [cur[-1]]
                    w_cur = w
                    cur.append(end)
            pieces.append(cur)
            widths.append(w_cur or 1)
        line["shapes"] = [
            [[round(x, 5), round(y, 5)] for x, y in simplify([tuple(q) for q in piece])]
            for piece in pieces
        ]
        line["w"] = widths


def build(zip_path: Path) -> dict:
    zf = zipfile.ZipFile(zip_path)
    feed = max(i.date_time for i in zf.infolist())
    routes = {r["route_id"]: r for r in rows(zf, "routes.txt") if r["route_type"] == "1"}
    trips = {t["trip_id"]: t for t in rows(zf, "trips.txt") if t["route_id"] in routes}
    stops = {s["stop_id"]: s for s in rows(zf, "stops.txt")}

    # each shape's trips, and one trip per shape whose stops give the shape's stations
    shape_trips: dict[str, Counter] = defaultdict(Counter)
    for t in trips.values():
        shape_trips[t["route_id"]][t["shape_id"]] += 1
    kept: dict[str, list[str]] = {}
    sample: dict[str, str] = {}  # shape -> a trip on it
    for rid, counts in shape_trips.items():
        total = sum(counts.values())
        kept[rid] = [s for s, n in counts.most_common() if n / total >= MIN_SHARE]
    wanted = {s for shapes in kept.values() for s in shapes}
    for tid, t in trips.items():
        if t["shape_id"] in wanted and t["shape_id"] not in sample:
            sample[t["shape_id"]] = tid
    sample_trips = set(sample.values())

    stop_seq: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    served: dict[str, set[str]] = defaultdict(set)  # parent station -> lines
    for st in rows(zf, "stop_times.txt"):
        trip = trips.get(st["trip_id"])
        if trip is None:
            continue
        parent = stops.get(st["stop_id"], {}).get("parent_station") or st["stop_id"]
        served[parent].add(CODES[trip["route_id"]])
        if st["trip_id"] in sample_trips:
            stop_seq[st["trip_id"]].append((int(st["stop_sequence"]), parent, st["stop_id"]))

    points: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    for p in rows(zf, "shapes.txt"):
        if p["shape_id"] in wanted:
            seq, lon, lat = p["shape_pt_sequence"], p["shape_pt_lon"], p["shape_pt_lat"]
            points[p["shape_id"]].append((int(seq), float(lon), float(lat)))

    lines, paths = {}, {}
    for rid, r in sorted(routes.items(), key=lambda kv: list(CODES).index(kv[0])):
        shapes, stations = [], []
        for shape in kept[rid]:
            pts = [(lon, lat) for _, lon, lat in sorted(points[shape])]
            shapes.append([[round(lon, 5), round(lat, 5)] for lon, lat in simplify(pts)])
            order = [(s, platform) for _, s, platform in sorted(stop_seq[sample[shape]])]
            path = [sp for i, sp in enumerate(order) if i == 0 or sp[0] != order[i - 1][0]]
            stations.append([s for s, _ in path])
            paths.setdefault(CODES[rid], []).append(path)
        lines[CODES[rid]] = {
            "name": r["route_long_name"], "color": f"#{r['route_color'].lower()}",
            "text": f"#{r['route_text_color'].lower()}", "shapes": shapes, "stations": stations,
        }  # fmt: skip
    bundle(lines)
    order = list(CODES.values()).index
    stations = {
        sid: {"name": stops[sid]["stop_name"], "lon": round(float(stops[sid]["stop_lon"]), 5),
              "lat": round(float(stops[sid]["stop_lat"]), 5),
              "lines": sorted(served[sid], key=order)}
        for sid in sorted(served) if sid in stops
    }  # fmt: skip
    return {
        "feed": f"{feed[0]:04d}-{feed[1]:02d}-{feed[2]:02d}",
        "lines": lines,
        "stations": stations,
        "_paths": paths,  # the poller's (l_paths.json), not the site's
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--zip", required=True, type=Path)
    p.add_argument("--out", default="site/public/l/lines.json", type=Path)
    p.add_argument("--paths", default="src/ghost_ledger/l_paths.json", type=Path)
    args = p.parse_args()
    doc = build(args.zip)
    paths = {"feed": doc["feed"], "paths": doc.pop("_paths")}
    args.paths.write_text(json.dumps(paths, separators=(",", ":")) + "\n")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, separators=(",", ":"), ensure_ascii=False) + "\n")
    for code, line in doc["lines"].items():
        points = sum(map(len, line["shapes"]))
        per_shape = [len(s) for s in line["stations"]]
        shared = Counter(line["w"])
        print(f"{code:<5} {line['name']:<12} {len(line['shapes'])} pieces ({points} points), "
              f"widths {dict(sorted(shared.items()))}, stations per shape {per_shape}")  # fmt: skip
    size = args.out.stat().st_size / 1024
    print(f"{len(doc['stations'])} stations -> {args.out} ({size:.0f} KB)")


if __name__ == "__main__":
    main()
