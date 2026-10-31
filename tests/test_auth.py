"""Auth behavior: redirects, login, logout, protected API."""


def test_dashboard_redirects_anonymous_to_login(client):
    response = client.get("/dashboard")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_success(client, user):
    response = client.post(
        "/login",
        data={"email": "luke.deyoung@gmail.com", "password": "test-password-123"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Group overview" in response.data


def test_login_wrong_password(client, user):
    response = client.post(
        "/login",
        data={"email": "luke.deyoung@gmail.com", "password": "not-the-password"},
    )
    assert response.status_code == 401


def test_login_unknown_email(client, user):
    response = client.post(
        "/login",
        data={"email": "stranger@example.com", "password": "whatever"},
    )
    assert response.status_code == 401


def test_logout_ends_session(client, user):
    client.post(
        "/login",
        data={"email": "luke.deyoung@gmail.com", "password": "test-password-123"},
    )
    assert client.post("/logout").status_code == 302
    assert client.get("/dashboard").status_code == 302


def test_api_venues_requires_auth(client):
    assert client.get("/api/venues").status_code == 302


def test_api_venues_empty_state(client, user):
    client.post(
        "/login",
        data={"email": "luke.deyoung@gmail.com", "password": "test-password-123"},
    )
    response = client.get("/api/venues")
    assert response.status_code == 200
    assert response.get_json() == {"venues": []}


def test_open_redirect_blocked(client, user):
    response = client.post(
        "/login?next=//evil.example.com",
        data={"email": "luke.deyoung@gmail.com", "password": "test-password-123"},
    )
    assert response.status_code == 302
    assert "evil.example.com" not in response.headers["Location"]
