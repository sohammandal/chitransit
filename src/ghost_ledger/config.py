"""Settings from the environment. Real keys come from ~/.secrets; .env is optional."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Environment variables (e.g. from ~/.secrets) always win over .env.
load_dotenv(override=False)


@dataclass(frozen=True)
class Settings:
    bus_api_key: str | None
    train_api_key: str | None
    database_url: str
    poll_interval_seconds: int


def get_settings() -> Settings:
    return Settings(
        bus_api_key=os.environ.get("CTA_BUS_TRACKER_API_KEY") or None,
        train_api_key=os.environ.get("CTA_TRAIN_TRACKER_API_KEY") or None,
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql://ghost:ghost@localhost:5433/ghost_ledger"
        ),
        poll_interval_seconds=int(os.environ.get("POLL_INTERVAL_SECONDS", "30")),
    )


def require_bus_key(settings: Settings) -> str:
    if not settings.bus_api_key:
        raise SystemExit(
            "CTA_BUS_TRACKER_API_KEY is not set. It lives in ~/.secrets; "
            "run `source ~/.secrets` or open a new terminal."
        )
    return settings.bus_api_key
