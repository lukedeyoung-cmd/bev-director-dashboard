"""Suggestion engine behavior against a seeded sqlite catalog."""

import uuid

import sqlalchemy as sa

from app.extensions import db
from app.mapping.engine import suggest_mappings
from app.models.catalog import PosItem
from app.models.mapping import ToastItemMap
from app.models.purchasing import R365Item
from app.models.venue import Venue


def _seed(app, pos_items, r365_items):
    """Seed one active venue with the given (name, category) Toast items and
    R365 (name, category1) items; returns (pos_ids, r365_ids) keyed by name."""
    with app.app_context():
        venue = Venue(name="Test Venue", code="TV")
        db.session.add(venue)
        db.session.flush()
        pos_ids = {}
        for name, category in pos_items:
            item = PosItem(
                venue_id=venue.id, pos_guid=f"guid-{name}", name=name, item_type="menu_item",
                category=category,
            )
            db.session.add(item)
            pos_ids[name] = None  # id assigned on flush below
        r365_ids = {}
        for name, category1 in r365_items:
            row = R365Item(item_id=uuid.uuid4(), name=name, category1=category1)
            db.session.add(row)
            r365_ids[name] = row.item_id
        db.session.flush()
        for name in pos_ids:
            pos_ids[name] = db.session.scalar(
                sa.select(PosItem.id).where(PosItem.name == name)
            )
        db.session.commit()
        return pos_ids, r365_ids


def _mappings():
    return {
        row.pos_item_id: row for row in db.session.scalars(sa.select(ToastItemMap)).all()
    }


def test_engine_writes_suggested_not_approved(app):
    pos_ids, r365_ids = _seed(
        app,
        pos_items=[("Deep Eddy Grapefruit", "spirit")],
        r365_items=[("VODKA Deep Eddy Grapefruit", "Bar")],
    )
    with app.app_context():
        stats = suggest_mappings()
        assert stats.suggestions_created == 1
        mapping = _mappings()[pos_ids["Deep Eddy Grapefruit"]]
        assert mapping.status == "suggested"
        assert mapping.source == "auto"
        assert mapping.r365_item_id == r365_ids["VODKA Deep Eddy Grapefruit"]
        assert mapping.decided_at is None
        assert mapping.confidence is not None and mapping.confidence >= 0.6


def test_engine_leaves_weak_matches_unmatched(app):
    _seed(
        app,
        pos_items=[("Tecate", "beer")],
        r365_items=[("WINE - Scarpetta Prosecco", "Bar")],
    )
    with app.app_context():
        stats = suggest_mappings()
        assert stats.suggestions_created == 0
        assert stats.unmatched == 1
        assert db.session.scalar(sa.select(sa.func.count(ToastItemMap.id))) == 0


def test_engine_never_touches_approved(app):
    pos_ids, r365_ids = _seed(
        app,
        pos_items=[("Deep Eddy Grapefruit", "spirit")],
        r365_items=[("VODKA Deep Eddy Grapefruit", "Bar"), ("VODKA Deep Eddy Lime", "Bar")],
    )
    with app.app_context():
        approved = ToastItemMap(
            pos_item_id=pos_ids["Deep Eddy Grapefruit"],
            r365_item_id=r365_ids["VODKA Deep Eddy Grapefruit"],
            status="approved",
            source="manual",
        )
        db.session.add(approved)
        db.session.commit()

        stats = suggest_mappings()

        assert stats.skipped_approved == 1
        assert stats.suggestions_created == 0
        row = db.session.get(ToastItemMap, approved.id)
        assert row.status == "approved"
        assert row.r365_item_id == r365_ids["VODKA Deep Eddy Grapefruit"]


def test_engine_does_not_repropose_rejected_pair(app):
    pos_ids, r365_ids = _seed(
        app,
        pos_items=[("Deep Eddy Grapefruit", "spirit")],
        r365_items=[("VODKA Deep Eddy Grapefruit", "Bar")],
    )
    with app.app_context():
        db.session.add(
            ToastItemMap(
                pos_item_id=pos_ids["Deep Eddy Grapefruit"],
                r365_item_id=r365_ids["VODKA Deep Eddy Grapefruit"],
                status="rejected",
            )
        )
        db.session.commit()

        stats = suggest_mappings()

        assert stats.skipped_rejected == 1
        rows = db.session.scalars(sa.select(ToastItemMap)).all()
        assert len(rows) == 1 and rows[0].status == "rejected"


def test_engine_replaces_stale_suggestion_with_better_candidate(app):
    pos_ids, r365_ids = _seed(
        app,
        pos_items=[("Deep Eddy Grapefruit", "spirit")],
        r365_items=[("VODKA Deep Eddy Lime", "Bar")],
    )
    with app.app_context():
        # First pass proposes the same-brand wrong-flavor item.
        suggest_mappings()
        first = _mappings()[pos_ids["Deep Eddy Grapefruit"]]
        assert first.r365_item_id == r365_ids["VODKA Deep Eddy Lime"]

        # The right item appears; the undecided suggestion is regenerated.
        grapefruit = R365Item(
            item_id=uuid.uuid4(), name="VODKA Deep Eddy Grapefruit", category1="Bar"
        )
        db.session.add(grapefruit)
        db.session.commit()
        stats = suggest_mappings()

        assert stats.suggestions_created == 1
        rows = db.session.scalars(
            sa.select(ToastItemMap).where(
                ToastItemMap.pos_item_id == pos_ids["Deep Eddy Grapefruit"]
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].r365_item_id == grapefruit.item_id


def test_engine_is_idempotent(app):
    _seed(
        app,
        pos_items=[("Deep Eddy Grapefruit", "spirit")],
        r365_items=[("VODKA Deep Eddy Grapefruit", "Bar")],
    )
    with app.app_context():
        first = suggest_mappings()
        second = suggest_mappings()
        assert first.suggestions_created == 1
        assert second.suggestions_created == 0
        assert second.kept_stale == 1
        assert db.session.scalar(sa.select(sa.func.count(ToastItemMap.id))) == 1


def test_dry_run_writes_nothing(app):
    _seed(
        app,
        pos_items=[("Deep Eddy Grapefruit", "spirit")],
        r365_items=[("VODKA Deep Eddy Grapefruit", "Bar")],
    )
    with app.app_context():
        stats = suggest_mappings(dry_run=True)
        assert stats.suggestions_created == 1
        assert db.session.scalar(sa.select(sa.func.count(ToastItemMap.id))) == 0
