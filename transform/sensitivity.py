"""Sensitivity report: how the headline moves when one business-rule threshold moves.

Usage (after `make transform`, same AWS env):
    cd transform && uv run --group transform python sensitivity.py --date 2026-09-26

Copies the dev DuckDB, then for each variant reruns only the models the rule vars feed
(int_trip_matches, int_missing_trip_evidence, trip_status and what follows) with one
threshold changed and everything else at its default, all with the same as_of. Prints a
markdown table of trip_status counts for the date. A rule whose headline swings wildly on a
reasonable change of its threshold isn't publishable as is.
"""

import argparse
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import duckdb

HERE = Path(__file__).parent
DEFAULTS = {  # must match dbt_project.yml
    "rule_coverage_min": 0.9,
    "rule_start_tolerance_s": 120,
    "rule_reappear_m": 500,
    "rule_flag_window_min": 30,
    "rule_min_flag_looks": 2,
}
VARIANTS = [
    ("rule_coverage_min", [0.8, 0.95]),
    ("rule_start_tolerance_s", [60, 180]),
    ("rule_reappear_m", [250, 1000]),
    ("rule_flag_window_min", [15, 60]),
    ("rule_min_flag_looks", [1, 3]),
]
MODELS = "int_trip_matches+ int_missing_trip_evidence+ trip_status+ stop_hour_service"
COLUMNS = ["ran", "likely_ran", "flagged", "silent", "strict", "missing", "unknown"]


def run(db: Path, day: str, as_of: str, **rule) -> dict:
    vars_ = {"start_date": day, "end_date": day, "as_of": as_of, **rule}
    subprocess.run(
        ["dbt", "run", "--profiles-dir", ".", "--quiet", "--select", *MODELS.split(),
         "--vars", json.dumps(vars_)],
        cwd=HERE, env={**os.environ, "DBT_DUCKDB_PATH": str(db)}, check=True,
    )  # fmt: skip
    con = duckdb.connect(str(db), read_only=True)
    counts = dict(
        con.execute(
            "SELECT status, count(*) FROM trip_status WHERE service_date = ? GROUP BY 1", [day]
        ).fetchall()
    )
    counts["strict"] = con.execute(
        "SELECT count(*) FROM trip_status WHERE service_date = ? AND status = 'silent' "
        "AND strict_evidence",
        [day],
    ).fetchone()[0]
    con.close()
    return counts


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True, help="service date (YYYY-MM-DD)")
    p.add_argument("--db", default="target/ghost_ledger.duckdb", help="built dev database")
    args = p.parse_args()
    as_of = datetime.now(UTC).isoformat(timespec="seconds")
    # same file name: DuckDB names the catalog after it, and the models refer to it
    db = HERE / "target" / "sensitivity" / "ghost_ledger.duckdb"
    db.parent.mkdir(exist_ok=True)
    shutil.copy(HERE / args.db, db)

    rows = [("defaults", "", run(db, args.date, as_of))]
    for name, values in VARIANTS:
        for v in values:
            rows.append((name, f"{DEFAULTS[name]} → {v}", run(db, args.date, as_of, **{name: v})))

    base = rows[0][2]
    lines = [
        f"Sensitivity, service date {args.date}, as_of {as_of} (Δ vs defaults)",
        "",
        "| rule | change | " + " | ".join(COLUMNS) + " |",
        "|---|---|" + "---:|" * len(COLUMNS),
    ]
    for name, change, c in rows:
        cells = []
        for col in COLUMNS:
            n, d = c.get(col, 0), c.get(col, 0) - base.get(col, 0)
            cells.append(f"{n:,}" if not change or not d else f"{n:,} ({d:+,})")
        lines.append(f"| {name} | {change} | " + " | ".join(cells) + " |")
    report = "\n".join(lines)
    (HERE / "target" / "sensitivity.md").write_text(report + "\n")
    print(report)


if __name__ == "__main__":
    main()
