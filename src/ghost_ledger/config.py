"""Settings for dev (laptop) and prod (Lightsail box). Same code; only config differs.

GHOST_LEDGER_ENV picks the environment (default dev). Values are resolved, highest first:
  1. process environment (keys exported from ~/.secrets; on the box, prod's env file)
  2. .env          local overrides, gitignored, dev only
  3. .env.dev      committed dev defaults, no secrets
Prod reads the process environment only: no file in the repo or on the laptop can point
prod at the wrong database. Files are read, never loaded into os.environ.
"""

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENTS = ("dev", "prod")
DEV_DATABASE_URL = "postgresql://ghost:ghost@localhost:5433/ghost_ledger"

# One CTA key allows 100k calls/day, shared by dev and prod. Keep headroom for the
# predictions sampler (~21k/day) and pattern fetches.
DAILY_CALL_QUOTA = 100_000
MAX_VEHICLE_CALLS_PER_DAY = 60_000
ROUTES_PER_CALL = 10
ALL_ROUTES_ESTIMATE = 125


class ConfigError(SystemExit):
    """Bad configuration: stop at startup with a message that says how to fix it."""


@dataclass(frozen=True)
class Settings:
    env: Literal["dev", "prod"]
    bus_api_key: str | None
    train_api_key: str | None
    database_url: str
    poll_interval_seconds: int
    # None = every route from getroutes; otherwise only these (dev, once prod runs)
    poll_routes: tuple[str, ...] | None
    # A bus missing from the feed this long loses its assignment (docs/PLAN.md)
    assignment_grace_minutes: int
    # Where `ghost-ledger monitor` sends metrics: CloudWatch in prod, the log in dev
    metrics_sink: Literal["log", "cloudwatch"] = "log"
    # Glue "database.table" whose last Iceberg commit the monitor watches (prod only)
    ingress_freshness_table: str | None = None
    # Where the poller writes the public live feed after each sweep: s3://bucket/key in prod,
    # a file path in dev (the site's dev server reads it), unset = no feed
    live_target: str | None = None

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    def vehicle_calls_per_day(self, n_routes: int | None = None) -> int:
        routes = n_routes or (len(self.poll_routes) if self.poll_routes else ALL_ROUTES_ESTIMATE)
        return math.ceil(routes / ROUTES_PER_CALL) * (86_400 // self.poll_interval_seconds)


def _resolve(environ: Mapping[str, str], root: Path) -> dict[str, str]:
    env = environ.get("GHOST_LEDGER_ENV", "dev").strip() or "dev"
    if env not in ENVIRONMENTS:
        raise ConfigError(f"GHOST_LEDGER_ENV must be one of {ENVIRONMENTS}, got {env!r}.")
    values: dict[str, str] = {}
    files = (".env.dev", ".env") if env == "dev" else ()
    for name in files:
        path = root / name
        if path.is_file():
            values |= {k: v for k, v in dotenv_values(path).items() if v is not None}
    values |= dict(environ)
    values["GHOST_LEDGER_ENV"] = env
    return values


def get_settings(environ: Mapping[str, str] | None = None, root: Path = REPO_ROOT) -> Settings:
    v = _resolve(os.environ if environ is None else environ, root)
    env = v["GHOST_LEDGER_ENV"]

    database_url = v.get("DATABASE_URL", "").strip()
    if not database_url:
        if env == "prod":
            raise ConfigError(
                "Prod needs DATABASE_URL set explicitly (the prod env file on the box); "
                "it never falls back to the dev database."
            )
        database_url = DEV_DATABASE_URL

    try:
        interval = int(v.get("POLL_INTERVAL_SECONDS", "30"))
        grace = int(v.get("ASSIGNMENT_GRACE_MINUTES", "10"))
    except ValueError as exc:
        raise ConfigError(
            f"POLL_INTERVAL_SECONDS and ASSIGNMENT_GRACE_MINUTES must be integers: {exc}"
        ) from None
    if interval < 10:
        raise ConfigError(f"POLL_INTERVAL_SECONDS={interval} is too fast; the key states ~30 s.")

    routes = tuple(r.strip() for r in v.get("POLL_ROUTES", "").split(",") if r.strip())

    sink = v.get("METRICS_SINK", "cloudwatch" if env == "prod" else "log").strip()
    if sink not in ("log", "cloudwatch"):
        raise ConfigError(f"METRICS_SINK must be log or cloudwatch, got {sink!r}.")

    settings = Settings(
        env=env,  # type: ignore[arg-type]
        bus_api_key=v.get("CTA_BUS_TRACKER_API_KEY") or None,
        train_api_key=v.get("CTA_TRAIN_TRACKER_API_KEY") or None,
        database_url=database_url,
        poll_interval_seconds=interval,
        poll_routes=routes or None,
        assignment_grace_minutes=grace,
        metrics_sink=sink,  # type: ignore[arg-type]
        ingress_freshness_table=v.get("INGRESS_FRESHNESS_TABLE", "").strip() or None,
        live_target=v.get("LIVE_TARGET", "").strip() or None,
    )
    calls = settings.vehicle_calls_per_day()
    if calls > MAX_VEHICLE_CALLS_PER_DAY:
        raise ConfigError(
            f"Polling would make ~{calls:,} getvehicles calls/day, over the "
            f"{MAX_VEHICLE_CALLS_PER_DAY:,} budget (key quota {DAILY_CALL_QUOTA:,}/day, shared "
            "by dev and prod). Raise POLL_INTERVAL_SECONDS or set POLL_ROUTES."
        )
    return settings


def require_bus_key(settings: Settings) -> str:
    if not settings.bus_api_key:
        raise ConfigError(
            "CTA_BUS_TRACKER_API_KEY is not set. It lives in ~/.secrets; "
            "run `source ~/.secrets` or open a new terminal."
        )
    return settings.bus_api_key


def redacted_url(url: str) -> str:
    """postgresql://user:***@host/db, for printing."""
    scheme, sep, rest = url.partition("://")
    if "@" not in rest:
        return url
    creds, _, host = rest.rpartition("@")
    user = creds.split(":", 1)[0]
    return f"{scheme}{sep}{user}:***@{host}"
