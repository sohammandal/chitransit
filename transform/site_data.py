"""Write the site's static data files from the built analytics tables (docs/SITE.md).

Usage: uv run --group transform python transform/site_data.py
    --db transform/target/ghost_ledger.duckdb --out site/public/data

Writes, under --out:
  v1/manifest.json          when and from which commit the data was built
  v1/patterns/{pid}.json    {"rt", "dir", "c": [[lon, lat], ...],
                             "s": [[lon, lat, name, pdist], ...]}

The nightly workflow syncs --out to the site bucket's data/ prefix; in dev, --out is the Astro
public folder so the dev server serves the same paths.
"""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import duckdb


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="transform/target/ghost_ledger.duckdb")
    p.add_argument("--out", default="site/public/data")
    args = p.parse_args()
    out = Path(args.out) / "v1"
    (out / "patterns").mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(args.db, read_only=True)
    rows = con.execute("SELECT pid, rt, direction, coords, stops FROM site_patterns").fetchall()
    for pid, rt, direction, coords, stops in rows:
        doc = {
            "rt": rt,
            "dir": f"{direction}bound",
            "c": coords,
            "s": [[s["lon"], s["lat"], s["name"], s["pdist"]] for s in stops or []],
        }
        (out / "patterns" / f"{pid}.json").write_text(json.dumps(doc, separators=(",", ":")))

    manifest = {
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "method_version": os.environ.get("GITHUB_SHA", "local")[:7],
        "patterns": len(rows),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"{len(rows)} patterns -> {out}")


if __name__ == "__main__":
    main()
