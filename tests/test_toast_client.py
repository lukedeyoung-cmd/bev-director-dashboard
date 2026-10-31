"""ToastClient tests against a scripted fake session (no network)."""

import pytest
import requests

from app.toast.client import (
    ORDERS_BULK_PATH,
    ToastAPIError,
    ToastAuthError,
    ToastClient,
)


class FakeResponse:
    def __init__(self, json_data=None, status_code=200, headers=None, text=""):
        self._json = json_data
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json body scripted")
        return self._json


class FakeSession:
    """Scripted request/response session capturing calls."""

    def __init__(self):
        self.post_responses = []
        self.get_responses = []  # consumed in order
        self.calls = []  # (method, url, kwargs)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.post_responses.pop(0)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        scripted = self.get_responses.pop(0)
        if isinstance(scripted, Exception):
            raise scripted
        return scripted


@pytest.fixture()
def session():
    return FakeSession()


@pytest.fixture()
def logged_in_client(session):
    session.post_responses.append(
        FakeResponse({"token": {"accessToken": "tok-1", "expiresIn": 3600}})
    )
    return ToastClient(
        client_id="cid",
        client_secret="secret",
        restaurant_guid="rest-guid",
        session=session,
        sleep=lambda _s: None,
    )


def test_missing_credentials_raise():
    with pytest.raises(ToastAuthError):
        ToastClient(client_id="", client_secret="", restaurant_guid="g")
    with pytest.raises(ToastAuthError):
        ToastClient(client_id="c", client_secret="s", restaurant_guid="")


def test_auth_posts_machine_client_payload(session):
    session.post_responses.append(
        FakeResponse({"token": {"accessToken": "tok-1", "expiresIn": 1970}})
    )
    client = ToastClient(
        client_id="cid",
        client_secret="sec",
        restaurant_guid="g",
        session=session,
        sleep=lambda _s: None,
    )
    assert client._get_token() == "tok-1"
    method, url, kwargs = session.calls[0]
    assert method == "POST" and url.endswith("/authentication/v1/authentication/login")
    assert kwargs["json"] == {
        "clientId": "cid",
        "clientSecret": "sec",
        "userAccessType": "TOAST_MACHINE_CLIENT",
    }


def test_auth_failure_raises(session):
    session.post_responses.append(FakeResponse({}, status_code=401, text="denied"))
    client = ToastClient(
        client_id="cid",
        client_secret="sec",
        restaurant_guid="g",
        session=session,
        sleep=lambda _s: None,
    )
    with pytest.raises(ToastAuthError):
        client._get_token()


def test_get_carries_auth_and_restaurant_headers(logged_in_client, session):
    session.get_responses.append(FakeResponse([{"guid": "o1"}]))
    orders = list(logged_in_client.iter_orders_bulk("20261029"))
    assert orders == [{"guid": "o1"}]
    method, url, kwargs = session.calls[-1]
    assert method == "GET" and url.endswith(ORDERS_BULK_PATH)
    assert kwargs["headers"] == {
        "Authorization": "Bearer tok-1",
        "Toast-Restaurant-External-ID": "rest-guid",
    }
    assert kwargs["params"] == {"businessDate": "20261029", "pageSize": 100, "page": 1}


def test_401_reauthenticates_once_and_retries(logged_in_client, session):
    session.get_responses.append(FakeResponse({}, status_code=401, text="stale"))
    session.post_responses.append(
        FakeResponse({"token": {"accessToken": "tok-2", "expiresIn": 3600}})
    )
    session.get_responses.append(FakeResponse([{"guid": "o1"}]))
    orders = list(logged_in_client.iter_orders_bulk("20261029"))
    assert orders == [{"guid": "o1"}]
    assert logged_in_client._get_token() == "tok-2"


def test_429_backs_off_then_succeeds(logged_in_client, session):
    session.get_responses.append(FakeResponse({}, status_code=429, text="slow down"))
    session.get_responses.append(FakeResponse([]))
    assert list(logged_in_client.iter_orders_bulk("20261029")) == []


def test_connection_reset_retries_and_succeeds(logged_in_client, session):
    # Observed live: a connection reset mid-backfill must retry, not fail the
    # business date.
    session.get_responses.append(
        requests.ConnectionError(
            "('Connection aborted.', ConnectionResetError(104, 'Connection reset by peer'))"
        )
    )
    session.get_responses.append(FakeResponse([{"guid": "o1"}]))
    assert list(logged_in_client.iter_orders_bulk("20261029")) == [{"guid": "o1"}]
    assert len([c for c in session.calls if c[0] == "GET"]) == 2


def test_4xx_raises_api_error_with_status(logged_in_client, session):
    session.get_responses.append(FakeResponse({}, status_code=400, text="bad date"))
    with pytest.raises(ToastAPIError) as excinfo:
        list(logged_in_client.iter_orders_bulk("19990101"))
    assert excinfo.value.status_code == 400


def test_orders_bulk_paginates_until_short_page(logged_in_client, session):
    full_page = [{"guid": f"o{i}"} for i in range(2)]
    for page in range(1, 4):
        # pages 1-2 full (pageSize=2), page 3 empty ends the loop
        session.get_responses.append(FakeResponse(full_page if page <= 2 else []))
    orders = list(logged_in_client.iter_orders_bulk("20261029", page_size=2))
    assert len(orders) == 4
    pages = [call[2]["params"]["page"] for call in session.calls if call[0] == "GET"]
    assert pages == [1, 2, 3]


def test_get_pages_follows_next_page_token(logged_in_client, session):
    session.get_responses.append(
        FakeResponse([{"guid": "a"}], headers={"Toast-Next-Page-Token": "tok9"})
    )
    session.get_responses.append(FakeResponse([{"guid": "b"}]))
    pages = list(logged_in_client.get_pages("/config/v2/menuItems"))
    assert pages == [[{"guid": "a"}], [{"guid": "b"}]]
    second_call = [call for call in session.calls if call[0] == "GET"][-1]
    assert second_call[2]["params"] == {"pageToken": "tok9"}


def test_throttle_sleeps_between_requests(session):
    session.post_responses.append(
        FakeResponse({"token": {"accessToken": "t", "expiresIn": 3600}})
    )
    session.get_responses.append(FakeResponse([]))
    session.get_responses.append(FakeResponse([]))
    sleeps: list[float] = []
    client = ToastClient(
        client_id="c",
        client_secret="s",
        restaurant_guid="g",
        requests_per_second=2.0,
        session=session,
        sleep=sleeps.append,
    )
    list(client.iter_orders_bulk("20261029"))
    list(client.iter_orders_bulk("20261029"))
    # First data request has no prior timestamp; the second waits ~0.5 s (the
    # exact value dips slightly below because the interval started before sleep).
    assert len(sleeps) == 1
    assert 0.4 < sleeps[0] <= 0.5
