"""Thin client for CTA Bus Tracker API v3.

Verified live on 2026-09-25: getvehicles accepts at most 10 routes per call
("Maximum number of rt identifiers exceeded" for 11); tmres=s gives seconds;
tmstmp is Chicago local time with no offset; getpredictions carries dyn,
stsd and stst; getpatterns?rt= returns only the patterns running right now,
so new ones must be fetched by pid when a bus first reports them.
"""

import logging
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

BASE_URL = "https://www.ctabustracker.com/bustime/api/v3"
MAX_ROUTES_PER_CALL = 10
MAX_STOPS_PER_CALL = 10
CHICAGO = ZoneInfo("America/Chicago")

ALERTS_URL = "https://www.transitchicago.com/api/1.0/alerts.aspx"

# A full sweep's largest reply is ~40 KB; anything near this is not a Bus Tracker answer.
MAX_BODY_BYTES = 5_000_000

# Retries must fit inside one 30 s sweep of 13 calls.
DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 2.0)

# Errors CTA returns for "nothing to report right now": a quiet route at night (verified live
# 2026-09-25), or a stop with no upcoming buses (getpredictions; texts from CTA's developer
# guide). Anything else, e.g. "Invalid API access key supplied" or a daily-quota message,
# arrives as HTTP 200 too and must count as a failure.
BENIGN_API_ERRORS = frozenset(
    {"No data found for parameter", "No arrival times", "No service scheduled"}
)

# httpx logs full URLs (including ?key=...) at INFO. Keep it quiet so the key never lands in logs.
logging.getLogger("httpx").setLevel(logging.WARNING)


class BusTrackerError(RuntimeError):
    """A call failed after all retries (only raised by the smoke-test helpers)."""


@dataclass
class ApiResult:
    """One API call. `error` is set when the call failed: a transport failure after all
    retries, or a reply with no data and an error other than "No data found" (a bad key or
    an exhausted quota also comes back as HTTP 200).

    Per-parameter errors next to real data (one quiet route among ten) stay in `api_errors`
    and are not failures, so one bad route can't discard the other nine.
    """

    body: dict[str, Any] = field(default_factory=dict)
    # The response exactly as received (last attempt), kept so every derived row can be
    # rebuilt by re-parsing it (DDIA ch. 12). None when no response arrived at all.
    raw: str | None = None
    http_status: int | None = None
    attempts: int = 0
    latency_ms: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def api_errors(self) -> list[dict[str, Any]]:
        return self.body.get("error", [])


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
    so resolve it with the poll's fetched_at (see resolve_tmstmp).
    """
    fmt = "%Y%m%d %H:%M:%S" if value.count(":") == 2 else "%Y%m%d %H:%M"
    return datetime.strptime(value, fmt)  # noqa: DTZ007 - intentionally naive local time


def resolve_tmstmp(
    local: datetime, fetched_at: datetime, slack: timedelta = timedelta(minutes=2)
) -> datetime:
    """Turn a naive Chicago-local tmstmp into UTC, using when we fetched it.

    In the fall-back hour (e.g. 01:30 on the first Sunday in November) the same
    local time happens twice. A position can't be reported before it happened, so
    take the latest reading that is not after fetched_at (plus a little clock slack).
    """
    candidates = sorted(local.replace(tzinfo=CHICAGO, fold=fold).astimezone(UTC) for fold in (0, 1))
    plausible = [c for c in candidates if c <= fetched_at + slack]
    return plausible[-1] if plausible else candidates[0]


class BusTrackerClient:
    def __init__(
        self,
        api_key: str,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._key = api_key
        self._sleep = sleep
        self._http = httpx.Client(base_url=BASE_URL, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "BusTrackerClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _redact(self, text: str) -> str:
        return text.replace(self._key, "***") if self._key else text

    def call(self, endpoint: str, **params: str) -> ApiResult:
        """GET an endpoint, retrying timeouts, 5xx/429 and unparseable bodies. Never raises."""
        result = ApiResult()
        started = time.monotonic()
        for attempt in range(1, MAX_ATTEMPTS + 1):
            result.attempts = attempt
            try:
                response = self._http.get(
                    f"/{endpoint}", params={"key": self._key, "format": "json", **params}
                )
                result.http_status = response.status_code
                # Postgres TEXT can't hold NUL; JSON's own escape keeps the meaning.
                result.raw = self._redact(response.text).replace("\x00", "\\u0000")
                if response.status_code >= 500 or response.status_code == 429:
                    result.error = f"HTTP {response.status_code}"
                elif len(response.content) > MAX_BODY_BYTES:
                    result.raw = None  # don't store it
                    result.error = f"response too large ({len(response.content):,} bytes)"
                    break
                else:
                    doc = response.json()
                    body = doc.get("bustime-response") if isinstance(doc, dict) else None
                    if not isinstance(body, dict):
                        raise ValueError("not a bustime-response object")
                    result.body = body
                    result.error = self._api_failure(result.body)
                    break  # an API-level refusal won't change on retry
            except (httpx.TransportError, ValueError) as exc:
                result.error = self._redact(f"{type(exc).__name__}: {exc}")
            if attempt < MAX_ATTEMPTS:
                self._sleep(BACKOFF_SECONDS[attempt - 1])
        result.latency_ms = round((time.monotonic() - started) * 1000)
        return result

    @staticmethod
    def _api_failure(body: dict[str, Any]) -> str | None:
        if any(k != "error" for k in body):
            return None  # there is data; per-route errors stay in api_errors
        serious = [e.get("msg", "") for e in body.get("error", [])]
        serious = [m for m in serious if m not in BENIGN_API_ERRORS]
        return f"API error: {'; '.join(serious)}" if serious else None

    def get_alerts(self, routes: list[str] | None = None) -> ApiResult:
        """Active CTA Customer Alerts (reroutes, disruptions): a separate, keyless API that
        doesn't count against the Bus Tracker quota. One attempt; the next sample retries."""
        result = ApiResult(attempts=1)
        started = time.monotonic()
        params = {"outputType": "JSON", "activeonly": "true"}
        if routes:
            params["routeid"] = ",".join(routes)
        try:
            response = self._http.get(ALERTS_URL, params=params)
            result.http_status = response.status_code
            result.raw = response.text.replace("\x00", "\\u0000")
            if response.status_code >= 400:
                result.error = f"HTTP {response.status_code}"
            elif len(response.content) > MAX_BODY_BYTES:
                result.raw = None
                result.error = f"response too large ({len(response.content):,} bytes)"
            else:
                doc = response.json()
                body = doc.get("CTAAlerts") if isinstance(doc, dict) else None
                if not isinstance(body, dict):
                    raise ValueError("not a CTAAlerts object")
                result.body = body
                # 0 = alerts, 50 = none match the filter; anything else is a failure
                if result.body.get("ErrorCode") not in ("0", "50"):
                    result.error = f"API error: {result.body.get('ErrorMessage')}"
        except (httpx.TransportError, ValueError) as exc:
            result.error = f"{type(exc).__name__}: {exc}"
        result.latency_ms = round((time.monotonic() - started) * 1000)
        return result

    def get_routes(self) -> ApiResult:
        return self.call("getroutes")

    def get_vehicles(self, routes: list[str]) -> ApiResult:
        if not 1 <= len(routes) <= MAX_ROUTES_PER_CALL:
            raise ValueError(f"getvehicles takes 1-{MAX_ROUTES_PER_CALL} routes, got {len(routes)}")
        return self.call("getvehicles", rt=",".join(routes), tmres="s")

    def get_patterns(self, route: str) -> ApiResult:
        """Patterns running on a route right now (not every pattern the route has)."""
        return self.call("getpatterns", rt=route)

    def get_pattern(self, pid: int | str) -> ApiResult:
        """One pattern by id, for pids first seen mid-day."""
        return self.call("getpatterns", pid=str(pid))

    def get_predictions(self, stop_ids: list[str]) -> ApiResult:
        if not 1 <= len(stop_ids) <= MAX_STOPS_PER_CALL:
            raise ValueError(f"getpredictions takes 1-{MAX_STOPS_PER_CALL} stops")
        return self.call("getpredictions", stpid=",".join(stop_ids), tmres="s")


def raise_for_error(result: ApiResult) -> ApiResult:
    """For smoke tests and one-off scripts: turn a failed call into an exception."""
    if not result.ok:
        raise BusTrackerError(result.error)
    return result
