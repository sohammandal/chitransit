"""Thin client for CTA Bus Tracker API v3.

Verified live on 2026-09-25: getvehicles accepts at most 10 routes per call
("Maximum number of rt identifiers exceeded" for 11); tmres=s gives seconds;
tmstmp is Chicago local time with no offset; getpredictions carries dyn,
stsd and stst.
"""

import logging
from collections.abc import Iterable, Iterator
from datetime import datetime
from typing import Any

import httpx

BASE_URL = "https://www.ctabustracker.com/bustime/api/v3"
MAX_ROUTES_PER_CALL = 10
MAX_STOPS_PER_CALL = 10

# httpx logs full URLs (including ?key=...) at INFO. Keep it quiet so the key never lands in logs.
logging.getLogger("httpx").setLevel(logging.WARNING)


class BusTrackerError(RuntimeError):
    """The API answered, but with an error list in bustime-response."""


def chunked(items: Iterable[str], size: int) -> Iterator[list[str]]:
    batch: list[str] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def parse_tmstmp(value: str) -> datetime:
    """Parse CTA's 'YYYYMMDD HH:MM[:SS]' into a naive Chicago-local datetime.

    Deliberately naive: converting to UTC is ambiguous in the DST fall-back hour,
    so resolve it with the poll's fetched_at (see CLAUDE.md, "three clocks").
    """
    fmt = "%Y%m%d %H:%M:%S" if value.count(":") == 2 else "%Y%m%d %H:%M"
    return datetime.strptime(value, fmt)  # noqa: DTZ007 - intentionally naive local time


class BusTrackerClient:
    def __init__(self, api_key: str, timeout: float = 20.0) -> None:
        self._key = api_key
        self._http = httpx.Client(base_url=BASE_URL, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "BusTrackerClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _get(self, endpoint: str, **params: str) -> tuple[dict[str, Any], int]:
        """Return (bustime-response body, HTTP status). Raise BusTrackerError on API errors."""
        response = self._http.get(
            f"/{endpoint}", params={"key": self._key, "format": "json", **params}
        )
        body = response.json().get("bustime-response", {})
        if "error" in body and not any(k for k in body if k != "error"):
            raise BusTrackerError(body["error"])
        return body, response.status_code

    def get_routes(self) -> tuple[dict[str, Any], int]:
        return self._get("getroutes")

    def get_vehicles(self, routes: list[str]) -> tuple[dict[str, Any], int]:
        if not 1 <= len(routes) <= MAX_ROUTES_PER_CALL:
            raise ValueError(f"getvehicles takes 1-{MAX_ROUTES_PER_CALL} routes, got {len(routes)}")
        return self._get("getvehicles", rt=",".join(routes), tmres="s")

    def get_patterns(self, route: str) -> tuple[dict[str, Any], int]:
        return self._get("getpatterns", rt=route)

    def get_predictions(self, stop_ids: list[str]) -> tuple[dict[str, Any], int]:
        if not 1 <= len(stop_ids) <= MAX_STOPS_PER_CALL:
            raise ValueError(f"getpredictions takes 1-{MAX_STOPS_PER_CALL} stops")
        return self._get("getpredictions", stpid=",".join(stop_ids), tmres="s")
