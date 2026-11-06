"""Tests for the R365 OData HTTP client (FakeSession — no network)."""

import pytest

from app.r365.client import R365APIError, R365AuthError, R365ODataClient

BASE_URL = "https://odata.test/api/v2/views"


class FakeResponse:
    def __init__(self, payload=None, status_code=200, headers=None):
        self.payload = payload or {"value": []}
        self.status_code = status_code
        self.headers = headers or {}
        self.text = "body"

    def json(self):
        return self.payload


class FakeSession:
    """Records requests; replays a queue of responses."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.requests: list[dict] = []

    def get(self, url, params=None, auth=None, timeout=None):
        self.requests.append({"url": url, "params": dict(params or {}), "auth": auth})
        item = self.responses.pop(0) if self.responses else FakeResponse()
        if isinstance(item, Exception):
            raise item
        return item


def http_error(status):
    return http_error_response(status)


def http_error_response(status):
    return FakeResponse(status_code=status)


def make_client(session, **overrides):
    sleeps: list[float] = []

    def record_sleep(seconds):
        sleeps.append(seconds)

    kwargs = {
        "username": "landandseadept\\svc",
        "password": "pw",
        "requests_per_second": 0,  # no throttle waits in tests
        "base_url": BASE_URL,
        "session": session,
        "sleep": record_sleep,
        **overrides,
    }
    return R365ODataClient(**kwargs), session, sleeps


def test_get_sends_basic_auth_and_params():
    client, session, _ = make_client(FakeSession([FakeResponse({"value": [{"a": 1}]})]))
    payload = client.get("Location", params={"$filter": "name eq 'x'"})
    assert payload == {"value": [{"a": 1}]}
    req = session.requests[0]
    assert req["url"] == f"{BASE_URL}/Location"
    assert req["auth"] == ("landandseadept\\svc", "pw")
    assert req["params"]["$filter"] == "name eq 'x'"


def test_iter_rows_paginates_until_short_page():
    full_page = [{"i": n} for n in range(3)]
    session = FakeSession(
        [FakeResponse({"value": full_page}), FakeResponse({"value": [{"i": 9}]})]
    )
    client, _, _ = make_client(session, page_size=3)
    rows = list(client.iter_rows("Item"))
    assert rows == full_page + [{"i": 9}]
    skips = [r["params"]["$skip"] for r in session.requests]
    tops = [r["params"]["$top"] for r in session.requests]
    assert skips == [0, 3]
    assert tops == [3, 3]


def test_iter_rows_single_short_page_makes_one_request():
    session = FakeSession([FakeResponse({"value": [{"i": 1}]})])
    client, _, _ = make_client(session, page_size=1000)
    rows = list(client.iter_rows("Item"))
    assert rows == [{"i": 1}]
    assert len(session.requests) == 1


def test_get_retries_on_429_then_succeeds():
    session = FakeSession(
        [http_error_response(429), FakeResponse({"value": [{"ok": 1}]})]
    )
    client, _, sleeps = make_client(session)
    assert client.get("Location") == {"value": [{"ok": 1}]}
    assert len(session.requests) == 2
    assert sleeps  # backed off between attempts


def test_get_retries_on_5xx_then_succeeds():
    session = FakeSession([http_error_response(500), FakeResponse({"value": []})])
    client, _, _ = make_client(session)
    assert client.get("Location") == {"value": []}
    assert len(session.requests) == 2


def test_get_gives_up_after_retries():
    # max_retries counts TOTAL attempts: three failures with max_retries=3
    # must exhaust the loop and raise.
    session = FakeSession([http_error_response(429)] * 3)
    client, _, _ = make_client(session, max_retries=3)
    with pytest.raises(R365APIError):
        client.get("Location")
    assert len(session.requests) == 3


def test_get_honors_retry_after_header():
    session = FakeSession(
        [
            FakeResponse(status_code=429, headers={"Retry-After": "7"}),
            FakeResponse({"value": []}),
        ]
    )
    client, _, sleeps = make_client(session)
    assert client.get("Location") == {"value": []}
    assert sleeps == [7.0]


def test_get_raises_immediately_on_401():
    session = FakeSession([http_error_response(401)])
    client, session2, _ = make_client(session)
    with pytest.raises(R365AuthError):
        client.get("Location")
    assert len(session2.requests) == 1  # no retry — Basic auth cannot recover


def test_get_raises_without_retry_on_404():
    session = FakeSession([http_error_response(404)])
    client, session2, _ = make_client(session)
    with pytest.raises(R365APIError) as excinfo:
        client.get("Location")
    assert excinfo.value.status_code == 404
    assert len(session2.requests) == 1


def test_missing_credentials_raise_auth_error():
    with pytest.raises(R365AuthError):
        R365ODataClient(username="", password="pw", session=FakeSession())
    with pytest.raises(R365AuthError):
        R365ODataClient(username="u", password="", session=FakeSession())


def test_throttle_sleeps_between_requests():
    # Two pages (full then short) means two requests; the throttle must
    # sleep between them.
    session = FakeSession(
        [FakeResponse({"value": [{"i": 1}, {"i": 2}]}), FakeResponse({"value": [{"i": 3}]})]
    )
    client, _, sleeps = make_client(session, requests_per_second=10, page_size=2)
    list(client.iter_rows("Location"))
    assert any(s > 0 for s in sleeps)


def test_iter_rows_unexpected_payload_raises():
    session = FakeSession([FakeResponse({"unexpected": True})])
    client, _, _ = make_client(session)
    with pytest.raises(R365APIError):
        list(client.iter_rows("Item"))
