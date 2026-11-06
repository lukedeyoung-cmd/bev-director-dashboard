"""Mapping review-state transitions."""

import uuid

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models.catalog import PosItem
from app.models.mapping import ToastItemMap
from app.models.purchasing import R365Item
from app.models.venue import Venue


def _seed(app, *, pos_name="Titos Soda"):
    """Create one venue + Toast item + two R365 items; return their ids."""
    with app.app_context():
        venue = Venue(name="Test Venue", code="TV")
        db.session.add(venue)
        db.session.flush()
        pos_item = PosItem(
            venue_id=venue.id, pos_guid="toast-1", name=pos_name, item_type="modifier"
        )
        r365_a = R365Item(item_id=uuid.uuid4(), name="VODKA Titos 1L")
        r365_b = R365Item(item_id=uuid.uuid4(), name="VODKA Titos 750ml")
        db.session.add_all([pos_item, r365_a, r365_b])
        db.session.commit()
        return venue.id, pos_item.id, r365_a.item_id, r365_b.item_id


def test_new_mapping_defaults_to_suggested_auto(app):
    _, pos_id, r365_id, _ = _seed(app)
    with app.app_context():
        mapping = ToastItemMap(pos_item_id=pos_id, r365_item_id=r365_id)
        db.session.add(mapping)
        db.session.commit()
        assert mapping.status == "suggested"
        assert mapping.source == "auto"
        assert mapping.decided_at is None
        assert mapping.decided_by_user_id is None


def test_suggested_to_approved_records_decision(app, user):
    _, pos_id, r365_id, _ = _seed(app)
    with app.app_context():
        mapping = ToastItemMap(pos_item_id=pos_id, r365_item_id=r365_id)
        db.session.add(mapping)
        db.session.commit()

        mapping.transition("approved", user_id=user.id)
        db.session.commit()

        assert mapping.status == "approved"
        assert mapping.decided_at is not None
        assert mapping.decided_by_user_id == user.id


def test_approved_to_rejected_allowed(app):
    _, pos_id, r365_id, _ = _seed(app)
    with app.app_context():
        mapping = ToastItemMap(pos_item_id=pos_id, r365_item_id=r365_id, status="approved")
        db.session.add(mapping)
        db.session.commit()
        mapping.transition("rejected")
        assert mapping.status == "rejected"


def test_unknown_status_raises(app):
    _, pos_id, r365_id, _ = _seed(app)
    with app.app_context():
        mapping = ToastItemMap(pos_item_id=pos_id, r365_item_id=r365_id)
        db.session.add(mapping)
        db.session.commit()
        with pytest.raises(ValueError, match="Unknown mapping status"):
            mapping.transition("removed")


def test_rejected_back_to_suggested_allowed(app):
    # Re-asking the engine question after a reject must stay possible —
    # a full block would freeze stale proposals in the table.
    _, pos_id, r365_id, _ = _seed(app)
    with app.app_context():
        mapping = ToastItemMap(pos_item_id=pos_id, r365_item_id=r365_id, status="rejected")
        db.session.add(mapping)
        db.session.commit()
        mapping.transition("suggested")
        assert mapping.status == "suggested"


def test_second_active_mapping_for_same_item_rejected_by_index(app):
    # The partial unique index backs the "one active mapping per Toast item"
    # invariant the routes and engine rely on.
    _, pos_id, r365_id, r365_b = _seed(app)
    with app.app_context():
        db.session.add(ToastItemMap(pos_item_id=pos_id, r365_item_id=r365_id, status="approved"))
        db.session.flush()
        db.session.add(ToastItemMap(pos_item_id=pos_id, r365_item_id=r365_b, status="suggested"))
        with pytest.raises(Exception):  # noqa: B017 — sqlite IntegrityError shape varies
            db.session.commit()


def test_rejected_pair_does_not_block_new_active_mapping(app):
    _, pos_id, r365_id, r365_b = _seed(app)
    with app.app_context():
        db.session.add(ToastItemMap(pos_item_id=pos_id, r365_item_id=r365_id, status="rejected"))
        db.session.flush()
        db.session.add(ToastItemMap(pos_item_id=pos_id, r365_item_id=r365_b, status="suggested"))
        db.session.commit()
        rows = db.session.scalars(
            sa.select(ToastItemMap).where(ToastItemMap.pos_item_id == pos_id)
        ).all()
        assert len(rows) == 2
        assert {row.status for row in rows} == {"rejected", "suggested"}
