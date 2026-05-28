"""
FHIR R4 HTTP client with automatic pagination and incremental sync.

Handles:
  - Bearer token / SMART-on-FHIR auth
  - Bundle pagination (_getpages / next links)
  - _lastUpdated-based incremental synchronisation
  - Exponential backoff retry on transient errors
  - Rate limiting via X-RateLimit-Remaining headers
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime  import datetime, timezone
from typing    import Any, AsyncIterator, Optional
from urllib    import parse as urlparse

import httpx

log = logging.getLogger("evidentrx.interop.fhir.client")

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class FHIRClient:
    """
    Async FHIR R4 client.

    Parameters
    ----------
    base_url    : FHIR server base URL, e.g. "https://fhir.hospital.org/fhir/R4"
    auth_token  : Bearer token (pre-resolved from secrets manager)
    timeout_sec : Per-request timeout
    page_size   : _count parameter passed to search requests
    max_retries : Retry attempts on transient failures
    """

    def __init__(
        self,
        base_url:    str,
        auth_token:  Optional[str] = None,
        timeout_sec: int           = 30,
        page_size:   int           = 200,
        max_retries: int           = 3,
    ) -> None:
        self._base_url   = base_url.rstrip("/")
        self._timeout    = timeout_sec
        self._page_size  = page_size
        self._max_retries= max_retries

        headers: dict[str, str] = {
            "Accept":       "application/fhir+json",
            "Content-Type": "application/fhir+json",
        }
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"

        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(timeout_sec),
            follow_redirects=True,
        )

    # ── Capability / reachability ─────────────────────────────────────────────

    async def get_capability_statement(self) -> dict[str, Any]:
        """Fetch CapabilityStatement — used for health checks."""
        resp = await self._get(f"{self._base_url}/metadata")
        return resp

    # ── Search ────────────────────────────────────────────────────────────────

    async def search(
        self,
        resource_type: str,
        params:        Optional[dict[str, str]] = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """
        Execute a paginated FHIR search.
        Yields one page (list of resource dicts) at a time.

        Example
        -------
          async for page in client.search("MedicationDispense", {"_count": "100"}):
              for resource in page:
                  process(resource)
        """
        query = dict(params or {})
        query.setdefault("_count", str(self._page_size))

        url: Optional[str] = f"{self._base_url}/{resource_type}"

        while url:
            bundle = await self._get(url, params=query if "?" not in url else None)
            query  = None   # params already encoded in pagination next-link

            resources = _extract_resources(bundle)
            if resources:
                yield resources

            url = _extract_next_link(bundle)

    async def search_since(
        self,
        resource_type: str,
        since:         datetime,
        extra_params:  Optional[dict[str, str]] = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """
        Incremental search — return only resources modified after `since`.
        Uses the _lastUpdated FHIR search parameter.
        """
        params = dict(extra_params or {})
        params["_lastUpdated"] = f"gt{since.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        params["_sort"]        = "_lastUpdated"
        async for page in self.search(resource_type, params):
            yield page

    async def get_resource(
        self,
        resource_type: str,
        resource_id:   str,
    ) -> dict[str, Any]:
        """Fetch a single resource by id."""
        return await self._get(f"{self._base_url}/{resource_type}/{resource_id}")

    # ── Internal HTTP ─────────────────────────────────────────────────────────

    async def _get(
        self,
        url:    str,
        params: Optional[dict] = None,
    ) -> dict[str, Any]:
        backoff = 1.0
        last_exc: Optional[Exception] = None

        for attempt in range(1, self._max_retries + 1):
            try:
                t0   = time.perf_counter()
                resp = await self._client.get(url, params=params)
                elapsed = (time.perf_counter() - t0) * 1000

                # Honour rate-limit back-pressure
                remaining = int(resp.headers.get("X-RateLimit-Remaining", "999"))
                if remaining < 10:
                    log.warning("Rate limit low (%d remaining) — throttling 2s", remaining)
                    await asyncio.sleep(2)

                if resp.status_code in _RETRYABLE_STATUS:
                    retry_after = int(resp.headers.get("Retry-After", str(backoff)))
                    log.warning("FHIR server returned %d — retry in %ss", resp.status_code, retry_after)
                    await asyncio.sleep(retry_after)
                    backoff = min(backoff * 2, 60)
                    continue

                resp.raise_for_status()
                log.debug("GET %s → %d (%.0fms)", url, resp.status_code, elapsed)
                return resp.json()

            except httpx.TimeoutException as e:
                last_exc = e
                log.warning("Timeout on attempt %d/%d: %s", attempt, self._max_retries, url)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

            except httpx.HTTPStatusError as e:
                raise FHIRClientError(f"HTTP {e.response.status_code} from {url}: {e.response.text[:200]}") from e

        raise FHIRClientError(f"All {self._max_retries} attempts failed for {url}: {last_exc}")

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "FHIRClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()


# ── Bundle helpers ────────────────────────────────────────────────────────────

def _extract_resources(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull resource objects out of a FHIR Bundle."""
    if bundle.get("resourceType") != "Bundle":
        # Single resource returned (non-search endpoint)
        return [bundle]
    return [
        entry["resource"]
        for entry in bundle.get("entry", [])
        if "resource" in entry
    ]


def _extract_next_link(bundle: dict[str, Any]) -> Optional[str]:
    """Return the 'next' page URL from a Bundle, or None if this is the last page."""
    for link in bundle.get("link", []):
        if link.get("relation") == "next":
            return link.get("url")
    return None


class FHIRClientError(Exception):
    """Raised on unrecoverable FHIR HTTP errors."""
