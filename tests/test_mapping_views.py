"""Review UI flows: auth gating, approve/reject, manual mapping, unmatched."""

import uuid

import sqlalchemy as sa

from app.extensions import db
from app.models.catalog import PosItem
from app.models.mapping import ToastItemMap
from app.models.purchasing import R365Item
from app.models.venue import Venue


def _login(client, user):
    return client.post(
        "/login",
        data={"email": user.email, "password": "test-password-123"},
        follow_redirects=False,
    )


def _seed(app):
    """Venue + two Toast items + two R365 items; one suggested mapping.

    Returns dict of ids.
    """
    with app.app_context():
        venue = Venue(name="Test Venue", code="TV")
        db.session.add(venue)
        db.session.flush()
        mapped = PosItem(
            venue_id=venue.id, pos_guid="g-mapped", name="Deep Eddy Grapefruit",
            item_type="modifier", category="spirit",
        )
        unmapped = PosItem(
            venue_id=venue.id, pos_guid="g-unmapped", name="Tecate", item_type="menu_item",
            category="beer",
        )
        r365 = R365Item(item_id=uuid.uuid4(), name="VODKA Deep Eddy Grapefruit", category1="Bar")
        r365_other = R365Item(
            item_id=uuid.uuid4(), name="VODKA Deep Eddy Lime", category1="Bar"
        )
        db.session.add_all([mapped, unmapped, r365, r365_other])
        db.session.flush()
        suggestion = ToastItemMap(
            pos_item_id=mapped.id, r365_item_id=r365.item_id, status="suggested", source="auto",
            confidence=0.82,
        )
        db.session.add(suggestion)
        db.session.commit()
        return {
            "venue": venue.id,
            "mapped": mapped.id,
            "unmapped": unmapped.id,
            "r365": r365.item_id,
            "r365_other": r365_other.item_id,
            "suggestion": suggestion.id,
        }


def test_review_requires_login(client):
    assert client.get("/mapping").status_code == 302


def test_unmatched_requires_login(client):
    assert client.get("/mapping/unmatched").status_code == 302


def test_review_lists_suggestions_with_actions(client, user, app):
    _seed(app)
    assert _login(client, user).status_code == 302
    response = client.get("/mapping")
    assert response.status_code == 200
    assert "Deep Eddy Grapefruit" in response.text
    assert "VODKA Deep Eddy Grapefruit" in response.text
    assert "82%" in response.text
    assert "Approve" in response.text and "Reject" in response.text


def test_review_empty_state_when_no_suggestions(client, user, app):
    with app.app_context():
        db.session.add(Venue(name="Empty Venue", code="EV"))
        db.session.commit()
    assert _login(client, user).status_code == 302
    response = client.get("/mapping")
    assert response.status_code == 200
    assert "No pending suggestions." in response.text


def test_approve_records_reviewer(client, user, app):
    ids = _seed(app)
    assert _login(client, user).status_code == 302
    response = client.post(f"/mapping/{ids['suggestion']}/approve")
    assert response.status_code == 302
    with app.app_context():
        row = db.session.get(ToastItemMap, ids["suggestion"])
        assert row.status == "approved"
        assert row.decided_by_user_id == user.id
        assert row.decided_at is not None


def test_reject_then_reapprove_roundtrip_and_bad_id_404s(client, user, app):
    ids = _seed(app)
    assert _login(client, user).status_code == 302
    assert client.post(f"/mapping/{ids['suggestion']}/reject").status_code == 302
    assert client.post(f"/mapping/{ids['suggestion']}/approve").status_code == 302
    with app.app_context():
        row = db.session.get(ToastItemMap, ids["suggestion"])
        assert row.status == "approved"
    # A fabricated review id is a genuine 404.
    assert client.post(f"/mapping/{uuid.uuid4()}/approve").status_code == 404


def test_unmatched_shows_only_items_without_active_mapping(client, user, app):
    _seed(app)
    assert _login(client, user).status_code == 302
    response = client.get("/mapping/unmatched")
    assert response.status_code == 200
    assert "Tecate" in response.text
    assert "Deep Eddy Grapefruit" not in response.text


def test_unmatched_excludes_approved_too(client, user, app):
    ids = _seed(app)
    with app.app_context():
        suggestion = db.session.get(ToastItemMap, ids["suggestion"])
        suggestion.transition("approved")
        db.session.commit()
    assert _login(client, user).status_code == 302
    response = client.get("/mapping/unmatched")
    assert "Deep Eddy Grapefruit" not in response.text
    assert "Tecate" in response.text  # the other item is still unmatched


def test_manual_set_mapping_creates_approved_manual_row(client, user, app):
    ids = _seed(app)
    assert _login(client, user).status_code == 302
    response = client.post(
        "/mapping/set",
        data={"pos_item_id": str(ids["unmapped"]), "r365_item_id": str(ids["r365"])},
    )
    assert response.status_code == 302
    with app.app_context():
        rows = db.session.scalars(
            sa.select(ToastItemMap).where(ToastItemMap.pos_item_id == ids["unmapped"])
        ).all()
        assert len(rows) == 1
        assert rows[0].status == "approved"
        assert rows[0].source == "manual"
        assert rows[0].decided_by_user_id == user.id


def test_manual_set_to_new_target_replaces_suggestion_with_manual_row(client, user, app):
    ids = _seed(app)
    assert _login(client, user).status_code == 302
    response = client.post(
        "/mapping/set",
        data={"pos_item_id": str(ids["mapped"]), "r365_item_id": str(ids["r365_other"])},
    )
    assert response.status_code == 302
    with app.app_context():
        rows = db.session.scalars(
            sa.select(ToastItemMap).where(ToastItemMap.pos_item_id == ids["mapped"])
        ).all()
        assert len(rows) == 1
        assert rows[0].status == "approved"
        assert rows[0].source == "manual"
        assert rows[0].r365_item_id == ids["r365_other"]


def test_manual_set_same_target_approves_suggestion_in_place(client, user, app):
    ids = _seed(app)
    assert _login(client, user).status_code == 302
    response = client.post(
        "/mapping/set",
        data={"pos_item_id": str(ids["mapped"]), "r365_item_id": str(ids["r365"])},
    )
    assert response.status_code == 302
    with app.app_context():
        rows = db.session.scalars(
            sa.select(ToastItemMap).where(ToastItemMap.pos_item_id == ids["mapped"])
        ).all()
        assert len(rows) == 1
        assert rows[0].status == "approved"
        assert rows[0].source == "auto"  # engine proposed it; reviewer confirmed


def test_manual_set_unknown_r365_item_creates_nothing(client, user, app):
    ids = _seed(app)
    assert _login(client, user).status_code == 302
    response = client.post(
        "/mapping/set",
        data={"pos_item_id": str(ids["unmapped"]), "r365_item_id": str(uuid.uuid4())},
    )
    assert response.status_code == 302  # flash + redirect, nothing saved
    with app.app_context():
        assert (
            db.session.scalar(
                sa.select(sa.func.count(ToastItemMap.id)).where(
                    ToastItemMap.pos_item_id == ids["unmapped"]
                )
            )
            == 0
        )


def test_coverage_endpoint_reports_group_and_venues(client, user, app):
    _seed(app)
    assert _login(client, user).status_code == 302
    response = client.get("/api/mapping/coverage")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["venues"] == []  # no sales rows yet
    assert payload["group"]["total_net_sales"] == 0.0
    assert payload["group"]["coverage_pct"] is None
