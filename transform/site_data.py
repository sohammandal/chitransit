"""Write the site's static data files from the built analytics tables (docs/SITE.md).

Usage: uv run --group transform python transform/site_data.py
    --db transform/target/ghost_ledger.duckdb --out site/public/data

Writes, under --out:
  v1/manifest.json          when and from which commit the data was built
  v1/patterns/{pid}.json    {"rt", "dir", "c": [[lon, lat], ...],
                             "s": [[lon, lat, name, pdist], ...]}
  v1/routes.json            {"49": "Western", ...}
  v1/headways.json          {"2026-09-27": {"49|North": {"14": 10.0, ...}}}: scheduled gap
                             (minutes) by Chicago hour, for today and tomorrow
  v1/summary.json           {"date": "2026-09-26", "routes": {"49": {"scheduled", "ran",
                             "announced", "silent_proven", "silent_possible", "not_judged"}}}

The nightly workflow syncs --out to the site bucket's data/ prefix; in dev, --out is the Astro
public folder so the dev server serves the same paths.

The files are a contract with the site (site/src/lib/types.ts). check() enforces it before
anything is written: a broken build exits non-zero, writes nothing, and the site keeps
yesterday's files.
"""

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

COUNTS = ["scheduled", "ran", "announced", "silent_proven", "silent_possible", "not_judged"]


def check(routes: dict, headways: dict, summary: dict, today: date) -> list[str]:
    """What's wrong with the files the site reads (empty when they're fine)."""
    bad = []
    if not routes or not all(isinstance(k, str) and isinstance(v, str) for k, v in routes.items()):
        bad.append("routes.json: empty or not {rt: name}")
    if str(today) not in headways:
        bad.append(f"headways.json: no schedule for today ({today})")
    for day, keys in headways.items():
        for key, hours in keys.items():
            if key.count("|") != 1 or not all(h.isdigit() and 0 <= int(h) < 24 for h in hours):
                bad.append(f"headways.json: bad entry {day} {key}")
            elif not all(isinstance(m, int | float) and m > 0 for m in hours.values()):
                bad.append(f"headways.json: non-positive gap {day} {key}")
    if not summary.get("routes"):
        bad.append("summary.json: no routes")
    elif not (today - timedelta(days=3) <= date.fromisoformat(summary["date"]) < today):
        bad.append(f"summary.json: date {summary['date']} isn't one of the last 3 days")
    for rt, c in summary.get("routes", {}).items():
        if sorted(c) != sorted(COUNTS) or any(not isinstance(v, int) or v < 0 for v in c.values()):
            bad.append(f"summary.json: bad counts for {rt}")
        elif c["silent_proven"] > c["silent_possible"]:
            bad.append(f"summary.json: {rt} proven > possible")
        elif c["ran"] + c["announced"] + c["silent_possible"] + c["not_judged"] != c["scheduled"]:
            bad.append(f"summary.json: {rt} counts don't add up to scheduled")
    return bad


def main() -> None:
    import duckdb  # transform dependency group; check() stays importable without it

    p = argparse.ArgumentParser()
    p.add_argument("--db", default="transform/target/ghost_ledger.duckdb")
    p.add_argument("--out", default="site/public/data")
    args = p.parse_args()
    out = Path(args.out) / "v1"

    con = duckdb.connect(args.db, read_only=True)
    patterns = {
        pid: {
            "rt": rt,
            "dir": f"{direction}bound",
            "c": coords,
            "s": [[s["lon"], s["lat"], s["name"], s["pdist"]] for s in stops or []],
        }
        for pid, rt, direction, coords, stops in con.execute(
            "SELECT pid, rt, direction, coords, stops FROM site_patterns"
        ).fetchall()
    }
    routes = dict(con.execute("SELECT rt, name FROM site_routes ORDER BY rt").fetchall())

    headways: dict = {}
    for day, rt, direction, hour, minutes in con.execute(
        "SELECT service_date, rt, direction, hour, headway_min FROM site_headways"
    ).fetchall():
        headways.setdefault(str(day), {}).setdefault(f"{rt}|{direction}", {})[str(hour)] = minutes

    last = con.execute("SELECT max(service_date) FROM site_route_summary").fetchone()[0]
    rows = con.execute(
        f"SELECT rt, {', '.join(COUNTS)} FROM site_route_summary WHERE service_date = ?", [last]
    ).fetchall()
    by_route = {r[0]: dict(zip(COUNTS, r[1:], strict=True)) for r in rows}
    summary = {"date": str(last), "routes": by_route}

    today = datetime.now(ZoneInfo("America/Chicago")).date()
    bad = check(routes, headways, summary, today)
    if not patterns:
        bad.append("site_patterns: no patterns")
    if bad:
        sys.exit("site data failed its checks, nothing written:\n  " + "\n  ".join(bad))

    (out / "patterns").mkdir(parents=True, exist_ok=True)
    compact = {"separators": (",", ":")}
    for pid, doc in patterns.items():
        (out / "patterns" / f"{pid}.json").write_text(json.dumps(doc, **compact))
    (out / "routes.json").write_text(json.dumps(routes, **compact))
    (out / "headways.json").write_text(json.dumps(headways, **compact))
    (out / "summary.json").write_text(json.dumps(summary, **compact))
    manifest = {
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "method_version": os.environ.get("GITHUB_SHA", "local")[:7],
        "patterns": len(patterns),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"{len(patterns)} patterns, {len(routes)} routes, summary {last} -> {out}")


if __name__ == "__main__":
    main()
