"""R365 OData client: HTTP Basic auth, throttle, retry, $skip pagination.

Verified against the live Land & Sea Dept tenant (2026-11):

- Endpoint surface: GET {base}/<Entity> where base is
  https://odata.restaurant365.net/api/v2/views. Responses are OData v4
  JSON: {"@odata.context": ..., "value": [rows]}.
- Auth: plain HTTP Basic with the normal R365 login (the username is
  already domain-prefixed, e.g. "landandseadept\\user"). There is no
  token exchange and no session — every request carries the same
  credentials, so a 401 is terminal, not retryable.
- Query guards enforced server-side: TransactionDetail rejects any
  $filter without a date-range window covering 31 days or less (the
  window property may be createdOn or modifiedOn; the entity has no
  transaction-date column). Transaction accepts a `date` range plus
  additional predicates ($count=true also works there).
- Pagination: $top/$skip. The server honors large pages (2000 rows
  observed) and does not emit @odata.nextLink for these views, so the
  client pages by $skip until a short page comes back.
- Transaction and TransactionDetail carry a non-null rowVersion
  (Edm.Int64) that increases as rows are written/edited; combined with
  a modifiedOn window it drives the incremental sync mode.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

import requests

DEFAULT_BASE_URL = "https://odata.restaurant365.net/api/v2/views"
# R365's guidance is to keep queries modest; 1000-row pages keep request
# counts low without very large responses.
DEFAULT_REQUESTS_PER_SECOND = 2.0
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 5
DEFAULT_PAGE_SIZE = 1000
MAX_PAGES = 10_000  # hard stop against a misbehaving paginator


class R365AuthError(RuntimeError):
    """Raised when R365 OData authentication fails or is missing."""


class R365APIError(RuntimeError):
    """Raised on a non-retryable API error. Carries the HTTP status when known."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class R365ODataClient:
    """Client over one R365 OData views endpoint (all locations, one login)."""

    def __init__(
        self,
        *,
        username: str,
        password: str,
        base_url: str = DEFAULT_BASE_URL,
        requests_per_second: float = DEFAULT_REQUESTS_PER_SECOND,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        page_size: int = DEFAULT_PAGE_SIZE,
        session: requests.Session | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        if not username or not password:
            raise R365AuthError(
                "R365 OData credentials are required "
                "(R365_ODATA_USERNAME / R365_ODATA_PASSWORD)"
            )
        self._username = username
        self._password = password
        self._base_url = base_url.rstrip("/")
        self._min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._timeout = timeout
        self._max_retries = max_retries
        self._page_size = page_size
        self._session = session or requests.Session()
        self._sleep = sleep
        self._last_request_at = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Requests
    # ------------------------------------------------------------------
    def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        with self._lock:
            wait = self._min_interval - (time.monotonic() - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
            self._last_request_at = time.monotonic()

    def get(self, entity: str, params: dict[str, Any] | None = None) -> dict:
        """GET one OData view request and return the parsed JSON payload."""
        path = f"/{entity}"
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            self._throttle()
            try:
                response = self._session.get(
                    f"{self._base_url}{path}",
                    params=params,
                    auth=(self._username, self._password),
                    timeout=self._timeout,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = R365APIError(f"GET {path} transport error: {exc}")
                self._sleep(min(2.0**attempt, 30.0))
                continue
            if response.status_code < 400:
                return response.json()
            if response.status_code == 401:
                # Basic auth carries the same credentials every request —
                # retrying cannot succeed.
                raise R365AuthError(f"R365 rejected the credentials for {path}")
            if response.status_code == 429 or response.status_code >= 500:
                last_error = R365APIError(
                    f"GET {path} returned HTTP {response.status_code} "
                    f"(attempt {attempt + 1}/{self._max_retries})",
                    status_code=response.status_code,
                )
                retry_after = response.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    self._sleep(float(retry_after))
                else:
                    self._sleep(min(2.0**attempt, 30.0))
                continue
            raise R365APIError(
                f"GET {path} failed: HTTP {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )
        raise last_error or R365APIError(f"GET {path} failed after retries")

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------
    @property
    def page_size(self) -> int:
        """Rows requested per page (used by ingestion to batch commits)."""
        return self._page_size

    def iter_rows(
        self, entity: str, *, params: dict[str, Any] | None = None, page_size: int | None = None
    ) -> Iterator[dict]:
        """Yield every row of an OData view across $skip pages.

        The server returns plain value arrays with no next-link for these
        views, so paging walks $skip until a short page ends the loop.
        """
        size = page_size or self._page_size
        base_params = dict(params or {})
        for page_index in range(MAX_PAGES):
            page = self.get(
                entity,
                params={**base_params, "$top": size, "$skip": page_index * size},
            )
            rows = page.get("value")
            if not isinstance(rows, list):
                raise R365APIError(f"Unexpected {entity} payload type: {type(page).__name__}")
            yield from rows
            if len(rows) < size:
                return
        raise R365APIError(f"{entity} pagination exceeded {MAX_PAGES} pages")
