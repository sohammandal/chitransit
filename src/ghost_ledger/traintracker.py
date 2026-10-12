"""Thin client for CTA Train Tracker API 1.0 (the 'L'), plus the keyless line status.

From CTA's guide (v1.46, 2024-08-04) and verified live on 2026-10-11: one ttpositions call
returns every in-service train on all eight lines (rt=red,blue,brn,g,org,p,pink,y; at most
8 routes); replies sit under "ctatt" with errCd ("0" = OK) and errNm, and a refusal comes as
HTTP 200 too (102 = the key's 100,000 calls a day are used up, "reset at midnight").
Positions come from track circuits, not GPS. Each train carries its run number (rn),
destination, operational direction (trDr 1/5), next station and stop, prdt/arrT (Chicago
local, no offset) and lat/lon. ttarrivals gives the predictions (eta) at up to 4 stations
per call, each with isSch (from the timetable: no train behind it), isFlt, isDly and isApp;
only arrivals carry isSch and isFlt. CTA refreshes the data two or three times a minute.

One item can come back as an object rather than a list (XML-to-JSON): parsing belongs
downstream, from the raw body, which every call keeps exactly as received.
"""

import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from ghost_ledger.bustracker import MAX_BODY_BYTES, ApiResult

BASE_URL = "https://lapi.transitchicago.com/api/1.0"
LINES = ("red", "blue", "brn", "g", "org", "p", "pink", "y")
MAX_STATIONS_PER_CALL = 4  # errCd 105: "a maximum of 4 values"
# CTA's own word on each line (Customer Alerts API: keyless, not counted against the key)
LINE_STATUS_URL = "https://www.transitchicago.com/api/1.0/routes.aspx"

DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 2.0)


def as_list(value: Any) -> list[Any]:
    """CTA's JSON gives one item as an object and several as a list; none as null/absent."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def count_trains(body: dict[str, Any]) -> int:
    """Trains in a ttpositions reply."""
    return sum(
        len(as_list(r.get("train"))) for r in as_list(body.get("route")) if isinstance(r, dict)
    )


def count_etas(body: dict[str, Any]) -> int:
    """Predictions in a ttarrivals reply."""
    return len(as_list(body.get("eta")))


class TrainTrackerClient:
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

    def __enter__(self) -> "TrainTrackerClient":
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
                    f"/{endpoint}.aspx", params={"key": self._key, "outputType": "JSON", **params}
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
                    body = doc.get("ctatt") if isinstance(doc, dict) else None
                    if not isinstance(body, dict):
                        raise ValueError("not a ctatt object")
                    result.body = body
                    result.error = self._api_failure(body)
                    break  # an API-level refusal won't change on retry
            except (httpx.TransportError, ValueError) as exc:
                result.error = self._redact(f"{type(exc).__name__}: {exc}")
            if attempt < MAX_ATTEMPTS:
                self._sleep(BACKOFF_SECONDS[attempt - 1])
        result.latency_ms = round((time.monotonic() - started) * 1000)
        return result

    @staticmethod
    def _api_failure(body: dict[str, Any]) -> str | None:
        code = str(body.get("errCd", "")).strip()
        if code == "0":
            return None
        return f"API error {code or '?'}: {body.get('errNm') or 'no message'}"

    def positions(self, lines: Sequence[str] = LINES) -> ApiResult:
        """Every train on these lines (all eight by default) in one call."""
        return self.call("ttpositions", rt=",".join(lines))

    def arrivals(self, mapids: Sequence[str]) -> ApiResult:
        """Predictions at 1-4 stations (every line and direction serving them)."""
        if not 1 <= len(mapids) <= MAX_STATIONS_PER_CALL:
            raise ValueError(f"ttarrivals takes 1-{MAX_STATIONS_PER_CALL} stations")
        return self.call("ttarrivals", mapid=",".join(str(m) for m in mapids))

    def line_status(self) -> ApiResult:
        """CTA's status for each 'L' line ("Normal Service", "Planned Work w/Station(s)
        Bypassed"...). Keyless and off the quota; one attempt, the next sample retries."""
        result = ApiResult(attempts=1)
        started = time.monotonic()
        try:
            response = self._http.get(
                LINE_STATUS_URL, params={"type": "rail", "outputType": "JSON"}
            )
            result.http_status = response.status_code
            result.raw = response.text.replace("\x00", "\\u0000")
            if response.status_code >= 400:
                result.error = f"HTTP {response.status_code}"
            elif len(response.content) > MAX_BODY_BYTES:
                result.raw = None
                result.error = f"response too large ({len(response.content):,} bytes)"
            else:
                doc = response.json()
                body = doc.get("CTARoutes") if isinstance(doc, dict) else None
                if not isinstance(body, dict):
                    raise ValueError("not a CTARoutes object")
                result.body = body
                if str(body.get("ErrorCode")) != "0":
                    result.error = f"API error: {body.get('ErrorMessage')}"
        except (httpx.TransportError, ValueError) as exc:
            result.error = f"{type(exc).__name__}: {exc}"
        result.latency_ms = round((time.monotonic() - started) * 1000)
        return result
