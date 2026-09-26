"""Build the static dashboard (one self-contained HTML file) from the published analytics.

Usage: uv run --group transform python transform/dashboard.py
    --src s3://ghost-ledger-lake-.../analytics --out site/index.html [--days 14]

Reads trip_status and stop_hour_service; shows the last `days` service dates and a route
table for the latest one. Status colors carry state (ran/flagged/silent/unknown) and always
come with a text label; the route table is the accessible view of the same numbers.
"""

import argparse
import html
import os
from datetime import UTC, datetime
from pathlib import Path
from string import Template

import duckdb

STATUSES = [  # (status, label, color token); likely_ran is counted as ran
    ("ran", "Ran", "--ran"),
    ("flagged", "Announced canceled", "--flagged"),
    ("silent", "Silent ghost", "--silent"),
    ("missing", "Missing, announcement unknown", "--missing"),
    ("unknown", "Unknown", "--unknown"),
]


def load(src: str, days: int) -> dict:
    con = duckdb.connect()
    if src.startswith("s3://"):
        con.execute("INSTALL httpfs; LOAD httpfs; INSTALL aws; LOAD aws;")
        con.execute("CREATE SECRET (TYPE s3, PROVIDER credential_chain, REGION 'us-east-2')")
    con.execute("SET TimeZone = 'America/Chicago'")
    trips = f"read_parquet('{src}/trip_status/*/*.parquet', hive_partitioning = true)"
    stops = f"read_parquet('{src}/stop_hour_service/*/*.parquet', hive_partitioning = true)"
    # The service dates come from the partition folder names (service_date=YYYY-MM-DD). An
    # aggregate like max(service_date) over hive-partitioned files trips a DuckDB 1.5
    # internal error in its statistics optimizer, and the folder list is simpler anyway.
    folders = con.execute(f"SELECT file FROM glob('{src}/trip_status/*/*.parquet')").fetchall()
    dates = sorted({f.split("service_date=")[1].split("/")[0] for (f,) in folders})
    if not dates:
        return {"by_day": [], "latest": None, "routes": []}
    since = dates[-days:][0]
    by_day = con.execute(
        f"""
        select
            service_date,
            case when status = 'likely_ran' then 'ran' else status end as status,
            count(*) as n
        from {trips}
        where service_date >= DATE '{since}'
        group by all order by 1
        """
    ).fetchall()
    latest = max(d for d, _, _ in by_day)
    routes = con.execute(
        f"""
        with t as (
            select rt, count(*) as scheduled,
                count(*) filter (where status in ('ran', 'likely_ran')) as ran,
                count(*) filter (where status = 'flagged') as flagged,
                count(*) filter (where status = 'silent') as silent,
                count(*) filter (where status = 'silent' and strict_evidence) as strict_n,
                count(*) filter (where status = 'missing') as missing,
                count(*) filter (where status = 'unknown') as unknown
            from {trips} where service_date = ? group by rt
        ),
        s as (
            select rt, sum(observed_buses) as passes, sum(bunched) as bunched, sum(gaps) as gaps
            from {stops} where service_date = ? and bunched is not null group by rt
        )
        select t.*, s.passes, s.bunched, s.gaps
        from t left join s using (rt)
        order by t.silent + t.missing + t.flagged desc, t.unknown desc, try_cast(t.rt as int), t.rt
        """,
        [latest, latest],
    ).fetchall()
    # Data health per day: how much of the day we could judge at all, straight from the rules'
    # own outputs (not_watched = feed swept < 90% of the trip; missing = its stops' flags
    # weren't sampled enough to say whether CTA announced it).
    health = con.execute(
        f"""
        select
            service_date,
            count(*) as finished,
            count(*) filter (where reason = 'not_watched') as not_watched,
            count(*) filter (where reason = 'detour') as detour,
            count(*) filter (where status = 'silent') as silent,
            count(*) filter (where status = 'missing') as missing,
            count(*) filter (where status = 'likely_ran') as likely_ran
        from {trips}
        where service_date >= DATE '{since}'
        group by all order by 1 desc
        """
    ).fetchall()
    return {"by_day": by_day, "latest": latest, "routes": routes, "health": health}


def pct(a: float | None, b: float | None) -> str:
    return "–" if not b or a is None else f"{100 * a / b:.1f}%"


def render(data: dict) -> str:
    days = sorted({d for d, _, _ in data["by_day"]})
    counts = {(d, s): n for d, s, n in data["by_day"]}
    latest = data["latest"]
    total = {d: sum(counts.get((d, s), 0) for s, _, _ in STATUSES) for d in days}
    tot = {s: counts.get((latest, s), 0) for s, _, _ in STATUSES}
    known = tot["ran"] + tot["flagged"] + tot["silent"] + tot["missing"]
    strict = sum(r[5] for r in data["routes"])

    tiles = [
        (
            "Trips scheduled",
            f"{total.get(latest, 0):,}",
            "finished trips, latest service day",
        ),
        ("Ran", f"{tot['ran']:,}", pct(tot["ran"], known) + " of trips we could judge"),
        ("Announced canceled", f"{tot['flagged']:,}", "CTA marked them canceled (dyn=1)"),
        (
            "Silent ghosts",
            f"{strict:,}–{tot['silent']:,}",
            "never announced; strict (its bus seen elsewhere or never left) to lenient",
        ),
        ("Missing", f"{tot['missing']:,}", "not seen; too few samples to know if CTA announced it"),
        ("Unknown", f"{tot['unknown']:,}", "not watched, or a detour on the route"),
    ]
    tile_html = "".join(
        f'<div class="tile"><span class="k">{k}</span><span class="n">{v}</span>'
        f'<span class="s">{s}</span></div>'
        for k, v, s in tiles
    )

    # stacked columns: one per service date, scaled to the busiest day
    w, h, pad = 720, 220, 10
    col = min(48, (w - pad) / max(len(days), 1) - 12)
    peak = max(total.values(), default=1) or 1
    bars = []
    for i, d in enumerate(days):
        x, y = pad + i * ((w - pad) / max(len(days), 1)) + 6, h - 20
        for s, label, token in STATUSES:
            n = counts.get((d, s), 0)
            if not n:
                continue
            bh = (h - 40) * n / peak
            y -= bh
            tip = f"{d:%a %b %d}: {label} {n:,} of {total[d]:,}"
            bars.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{col:.1f}" height="{max(bh - 2, 0.5):.1f}" '
                f'rx="3" style="fill:var({token})"><title>{html.escape(tip)}</title></rect>'
            )
        bars.append(f'<text x="{x + col / 2:.1f}" y="{h - 4}" class="ax">{d:%b %d}</text>')
        bars.append(f'<text x="{x + col / 2:.1f}" y="{y - 6:.1f}" class="ax">{total[d]:,}</text>')
    bars.append(f'<line x1="{pad}" x2="{w}" y1="{h - 20}" y2="{h - 20}" class="base"/>')
    legend = "".join(
        f'<span class="lg"><i style="background:var({t})"></i>{lab}</span>'
        for _, lab, t in STATUSES
    )

    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(rt))}</td><td>{sched:,}</td><td>{pct(ran, ran + fl + si + mi)}</td>"
        f"<td>{fl}</td><td>{st}–{si}</td><td>{mi}</td><td>{un}</td>"
        f"<td>{pct(bun, passes)}</td><td>{pct(gaps, passes)}</td>"
        "</tr>"
        for rt, sched, ran, fl, si, st, mi, un, passes, bun, gaps in data["routes"]
    )
    health = "".join(
        "<tr>"
        f"<td>{d:%a %b %d}</td><td>{fin:,}</td><td>{pct(fin - nw, fin)}</td><td>{det:,}</td>"
        f"<td>{pct(si, si + mi)}</td><td>{lr:,}</td>"
        "</tr>"
        for d, fin, nw, det, si, mi, lr in data["health"]
    )
    sha = os.environ.get("GITHUB_SHA", "")[:7] or "local"
    page = Template((Path(__file__).parent / "dashboard.html").read_text())
    return page.substitute(
        latest_long=f"{latest:%A %B %d, %Y}",
        latest_short=f"{latest:%b %d}",
        built=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        tiles=tile_html,
        legend=legend,
        w=w,
        h=h,
        bars="".join(bars),
        rows=rows,
        health=health,
        method=html.escape(sha),
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--src", required=True)
    p.add_argument("--out", default="site/index.html")
    p.add_argument("--days", type=int, default=14)
    args = p.parse_args()
    data = load(args.src.rstrip("/"), args.days)
    if data["latest"] is None:
        raise SystemExit("no analytics published yet")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data))
    print(f"dashboard for {data['latest']} ({len(data['routes'])} routes) -> {out}")


if __name__ == "__main__":
    main()
