"""Toast API client for one restaurant: machine-client auth, throttle, pagination.

Verified against the live API (2026-10, Oliver's dev credential):

- Auth: POST {host}/authentication/v1/authentication/login with
  {clientId, clientSecret, userAccessType: "TOAST_MACHINE_CLIENT"}; the
  response's token.accessToken (~30 min TTL) rides as a bearer token.
- Every data request carries the Toast-Restaurant-External-ID header with
  the restaurant GUID; each venue is its own GUID under one client.
- Rate limits are per endpoint (~5-request burst, quickly resetting) —
  observed via x-toast-ratelimit-* headers; the client throttles and backs
  off on 429/5xx rather than depending on the headers' semantics.
- Pagination comes in two styles: ordersBulk uses numeric page/pageSize,
  while config endpoints use the Toast-Next-Page-Token response header.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

import requests

AUTH_PATH = "/authentication/v1/authentication/login"
ORDERS_BULK_PATH = "/orders/v2/ordersBulk"
MENUS_V2_PATH = "/menus/v2/menus"
CONFIG_MENU_ITEMS_PATH = "/config/v2/menuItems"

DEFAULT_API_HOST = "https://ws-api.toasttab.com"
DEFAULT_REQUESTS_PER_SECOND = 4.0
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 5
DEFAULT_PAGE_SIZE = 100


class ToastAuthError(RuntimeError):
    """Raised when Toast machine-client authentication fails."""


class ToastAPIError(RuntimeError):
    """Raised on a non-retryable API error. Carries the HTTP status when known."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ToastClient:
    """Client bound to one restaurant GUID (one venue)."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        restaurant_guid: str,
        api_host: str = DEFAULT_API_HOST,
        requests_per_second: float = DEFAULT_REQUESTS_PER_SECOND,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        page_size: int = DEFAULT_PAGE_SIZE,
        session: requests.Session | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        if not client_id or not client_secret:
            raise ToastAuthError(
                "Toast client credentials are required "
                "(TOAST_CLIENT_ID / TOAST_CLIENT_SECRET)"
            )
        if not restaurant_guid:
            raise ToastAuthError("A Toast restaurant GUID is required for this venue")
        self._client_id = client_id
        self._client_secret = client_secret
        self._restaurant_guid = restaurant_guid
        self._api_host = api_host.rstrip("/")
        self._min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._timeout = timeout
        self._max_retries = max_retries
        self._page_size = page_size
        self._session = session or requests.Session()
        self._sleep = sleep
        self._token: str | None = None
        self._token_deadline = 0.0
        self._last_request_at = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    def _authenticate(self) -> None:
        response = self._session.post(
            f"{self._api_host}{AUTH_PATH}",
            json={
                "clientId": self._client_id,
                "clientSecret": self._client_secret,
                "userAccessType": "TOAST_MACHINE_CLIENT",
            },
            timeout=self._timeout,
        )
        if response.status_code != 200:
            raise ToastAuthError(
                f"Toast authentication failed: HTTP {response.status_code}"
            )
        token_payload = response.json().get("token") or {}
        token = token_payload.get("accessToken")
        if not token:
            raise ToastAuthError("Toast authentication response had no token.accessToken")
        self._token = token
        expires_in = float(token_payload.get("expiresIn") or 3600)
        # Refresh slightly before the observed expiry (probe: ~1970 s).
        self._token_deadline = time.monotonic() + max(expires_in - 60.0, 60.0)

    def _get_token(self) -> str:
        if self._token and time.monotonic() < self._token_deadline:
            return self._token
        self._authenticate()
        assert self._token is not None  # _authenticate sets it or raises
        return self._token

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

    def _get(self, path: str, params: dict[str, Any] | None = None) -> requests.Response:
        """GET with auth, throttling, and retry on 401/429/5xx/reset."""
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            self._throttle()
            try:
                response = self._session.get(
                    f"{self._api_host}{path}",
                    params=params,
                    headers={
                        "Authorization": f"Bearer {self._get_token()}",
                        "Toast-Restaurant-External-ID": self._restaurant_guid,
                    },
                    timeout=self._timeout,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                # Transient transport failures (observed: connection resets
                # mid-backfill); same backoff as rate-limit responses.
                last_error = ToastAPIError(f"GET {path} transport error: {exc}")
                self._sleep(min(2.0**attempt, 30.0))
                continue
            if response.status_code < 400:
                return response
            if response.status_code == 401:
                # Force re-authentication once the token is rejected.
                self._token = None
                last_error = ToastAuthError(f"Toast returned 401 for {path}")
                continue
            if response.status_code == 429 or response.status_code >= 500:
                last_error = ToastAPIError(
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
            raise ToastAPIError(
                f"GET {path} failed: HTTP {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )
        raise last_error or ToastAPIError(f"GET {path} failed after retries")

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------
    def get_pages(self, path: str, *, params: dict[str, Any] | None = None) -> Iterator[Any]:
        """Yield one JSON payload per page (Toast-Next-Page-Token header style)."""
        request_params = dict(params or {})
        for _ in range(10_000):  # hard stop against a misbehaving paginator
            response = self._get(path, params=request_params)
            yield response.json()
            next_token = response.headers.get("Toast-Next-Page-Token")
            if not next_token:
                return
            request_params = {**request_params, "pageToken": next_token}

    def iter_orders_bulk(
        self, business_date: str, *, page_size: int | None = None
    ) -> Iterator[dict]:
        """Yield every order for one business date ("YYYYMMDD") across pages."""
        size = page_size or self._page_size
        page = 1
        while True:
            response = self._get(
                ORDERS_BULK_PATH,
                params={"businessDate": business_date, "pageSize": size, "page": page},
            )
            orders = response.json()
            if not isinstance(orders, list):
                raise ToastAPIError(
                    f"Unexpected ordersBulk payload type: {type(orders).__name__}"
                )
            yield from orders
            if len(orders) < size:
                return
            page += 1

    # ------------------------------------------------------------------
    # Endpoint helpers
    # ------------------------------------------------------------------
    def fetch_menus_v2(self) -> dict:
        """Full menus-v2 payload: {menus: [...], modifierOptionReferences: {...}, ...}."""
        pages = list(self.get_pages(MENUS_V2_PATH))
        if not pages:
            return {}
        payload = pages[0]
        if not isinstance(payload, dict):
            raise ToastAPIError(f"Unexpected menus v2 payload type: {type(payload).__name__}")
        return payload

    def iter_config_menu_items(self) -> Iterator[dict]:
        """Yield every config-v2 menu item (items and modifiers, no prices)."""
        for page in self.get_pages(CONFIG_MENU_ITEMS_PATH):
            if not isinstance(page, list):
                raise ToastAPIError(
                    f"Unexpected config menuItems payload type: {type(page).__name__}"
                )
            yield from page
