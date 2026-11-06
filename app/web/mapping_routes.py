"""Review UI for Toast↔R365 item mappings.

Server-rendered review queue: suggestions sorted by confidence with
approve/reject/search actions, an unmatched-item view so catalog entries
without any proposal never silently fall through, and approved-mapping
coverage on every page. All routes are login-protected and CSRF-checked.
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.extensions import db
from app.mapping.coverage import (
    category_group_coverage,
    coverage_for_venues,
    group_coverage,
)
from app.mapping.engine import DEFAULT_MIN_CONFIDENCE
from app.models.catalog import PosItem
from app.models.mapping import ToastItemMap
from app.models.purchasing import R365Item
from app.models.venue import Venue

mapping_bp = Blueprint("mapping", __name__)

PAGE_SIZE = 50
# Status filter values for the review queue (?status=...).
STATUSES = ("suggested", "approved", "rejected", "all")

# How many R365 items a search renders (server-rendered review: the reviewer
# picks from a short list, so depth beats exhaustiveness).
SEARCH_LIMIT = 15


def _search_r365(query: str) -> list[R365Item]:
    """R365 items matching a reviewer's search, shortest name first."""
    needle = f"%{query.strip()}%"
    return list(
        db.session.scalars(
            sa.select(R365Item)
            .where(R365Item.name.is_not(None), R365Item.name.ilike(needle))
            .order_by(sa.func.length(R365Item.name), R365Item.name)
            .limit(SEARCH_LIMIT)
        )
    )


def _safe_next() -> str:
    """Where to redirect after a decision — same page the reviewer was on."""
    candidate = request.form.get("next", "")
    if candidate.startswith("/") and not candidate.startswith("//"):
        return candidate
    return url_for("mapping.review")


@mapping_bp.get("/mapping")
@login_required
def review():
    """Review queue: suggestions (default) or any status, plus coverage."""
    status = request.args.get("status", "suggested")
    if status not in STATUSES:
        status = "suggested"
    venue_filter = request.args.get("venue", "")
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1

    # Search for a replacement target (?map_for=<map id>&q=...).
    map_for = request.args.get("map_for", "")
    query_text = request.args.get("q", "").strip()
    search_map_id = None
    search_results = None
    if map_for and query_text:
        map_uuid = _uuid_or_none(map_for)
        row = db.session.get(ToastItemMap, map_uuid) if map_uuid else None
        if row is None:
            abort(404)
        search_map_id = row.id
        search_results = _search_r365(query_text)

    base_filters = []
    if status != "all":
        base_filters.append(ToastItemMap.status == status)
    if venue_filter:
        base_filters.append(PosItem.venue_id == _venue_id_or_404(venue_filter))

    rows, pages = _mapping_page(base_filters, page)
    counts = _status_counts()
    venues = db.session.scalars(sa.select(Venue).where(Venue.is_active).order_by(Venue.name)).all()

    venue_rows = coverage_for_venues(db.session)
    return render_template(
        "mapping.html",
        title="Item mapping",
        rows=rows,
        status=status,
        statuses=STATUSES,
        counts=counts,
        venues=venues,
        venue_filter=venue_filter,
        page=page,
        pages=pages,
        search_map_id=search_map_id,
        search_query=query_text,
        search_results=search_results,
        venue_coverage=venue_rows,
        group=group_coverage(venue_rows),
        category_coverage=category_group_coverage(venue_rows),
        min_confidence=DEFAULT_MIN_CONFIDENCE,
    )


@mapping_bp.get("/mapping/unmatched")
@login_required
def unmatched():
    """Active Toast items with no suggested/approved mapping row.

    The nothing-falls-through view: anything listed here has no costing
    bridge at all. A previously-rejected item shows with a badge — it has
    candidates on record but none left to act on.
    """
    venue_filter = request.args.get("venue", "")
    category_filter = request.args.get("category", "")
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1

    # Per-row R365 search (?pos_item=<id>&q=...) — results render inline.
    pos_item_filter = request.args.get("pos_item", "")
    query_text = request.args.get("q", "").strip()
    search_item_id = None
    search_results = None
    if pos_item_filter and query_text:
        item_uuid = _uuid_or_none(pos_item_filter)
        item = db.session.get(PosItem, item_uuid) if item_uuid else None
        if item is None:
            abort(404)
        search_item_id = item.id
        search_results = _search_r365(query_text)

    no_active_map = ~sa.exists(
        sa.select(ToastItemMap.id).where(
            ToastItemMap.pos_item_id == PosItem.id,
            ToastItemMap.status.in_(ToastItemMap.ACTIVE_STATUSES),
        )
    )
    filters = [PosItem.is_active.is_(True), Venue.is_active.is_(True), no_active_map]
    if venue_filter:
        filters.append(PosItem.venue_id == _venue_id_or_404(venue_filter))
    if category_filter:
        filters.append(PosItem.category == category_filter)

    conditions = sa.select(
        sa.func.count(sa.distinct(PosItem.id)),
    ).join(Venue, Venue.id == PosItem.venue_id).where(*filters)
    total = db.session.scalar(conditions) or 0
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(page, pages)

    items = list(
        db.session.scalars(
            sa.select(PosItem)
            .join(Venue, Venue.id == PosItem.venue_id)
            .where(*filters)
            .order_by(Venue.name, PosItem.category.nulls_last(), PosItem.name)
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
    )

    rejected_counts = _rejected_counts([item.id for item in items])

    venues = db.session.scalars(sa.select(Venue).where(Venue.is_active).order_by(Venue.name)).all()
    venue_codes = {venue.id: venue.code for venue in venues}
    categories = list(
        db.session.scalars(
            sa.select(PosItem.category)
            .where(PosItem.is_active.is_(True), PosItem.category.is_not(None))
            .group_by(PosItem.category)
            .order_by(PosItem.category)
        )
    )
    return render_template(
        "mapping_unmatched.html",
        title="Unmatched Toast items",
        items=items,
        total=total,
        venues=venues,
        venue_codes=venue_codes,
        categories=categories,
        venue_filter=venue_filter,
        category_filter=category_filter,
        page=page,
        pages=pages,
        search_item_id=search_item_id,
        search_query=query_text,
        search_results=search_results,
        rejected_counts=rejected_counts,
    )


@mapping_bp.post("/mapping/<uuid:map_id>/approve")
@login_required
def approve(map_id: uuid.UUID):
    return _decide(map_id, "approved")


@mapping_bp.post("/mapping/<uuid:map_id>/reject")
@login_required
def reject(map_id: uuid.UUID):
    return _decide(map_id, "rejected")


def _decide(map_id: uuid.UUID, action: str) -> object:
    row = db.session.get(ToastItemMap, map_id)
    if row is None:
        abort(404)
    try:
        row.transition(action, user_id=current_user.id)
        db.session.commit()
        flash(f"Mapping {action}.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(f"Could not {action} mapping: {exc}", "error")
    return redirect(_safe_next())


@mapping_bp.post("/mapping/set")
@login_required
def set_mapping():
    """Reviewer sets (or replaces) the target for a Toast item.

    Creates an approved manual mapping directly — choosing a target by hand
    is itself the decision. An existing engine suggestion to a different
    target is deleted (undecided engine state); an existing approved mapping
    blocks the change so a deliberate decision is never silently overwritten.
    """
    pos_item_id = _uuid_or_none(request.form.get("pos_item_id"))
    r365_item_id = _uuid_or_none(request.form.get("r365_item_id"))
    if pos_item_id is None or r365_item_id is None:
        flash("Missing Toast item or R365 item.", "error")
        return redirect(_safe_next())

    item = db.session.get(PosItem, pos_item_id)
    target = db.session.get(R365Item, r365_item_id)
    if item is None or target is None:
        flash("Unknown Toast item or R365 item.", "error")
        return redirect(_safe_next())

    active = db.session.scalar(
        sa.select(ToastItemMap).where(
            ToastItemMap.pos_item_id == pos_item_id,
            ToastItemMap.status.in_(ToastItemMap.ACTIVE_STATUSES),
        )
    )
    if active is not None and active.status == "approved":
        flash("This item already has an approved mapping — reject it first to change it.", "error")
        return redirect(_safe_next())

    if active is not None and active.status == "suggested":
        if active.r365_item_id == r365_item_id:
            active.transition("approved", user_id=current_user.id)
            db.session.commit()
            flash(f"Approved mapping for {item.name}.", "success")
            return redirect(_safe_next())
        db.session.delete(active)
        db.session.flush()  # free the item's active slot before inserting

    db.session.add(
        ToastItemMap(
            pos_item_id=pos_item_id,
            r365_item_id=r365_item_id,
            status="approved",
            source="manual",
            decided_by_user_id=current_user.id,
            decided_at=datetime.now(UTC),
        )
    )
    db.session.commit()
    flash(f"Mapped {item.name} to {target.name}.", "success")
    return redirect(_safe_next())


# ----------------------------------------------------------------------
# Query helpers
# ----------------------------------------------------------------------


def _mapping_page(base_filters: list, page: int) -> tuple[list, int]:
    """One page of mapping rows (joined Toast/Venue/R365) and the page count."""
    conditions = [PosItem.is_active.is_(True), *base_filters]
    total = db.session.scalar(
        sa.select(sa.func.count())
        .select_from(ToastItemMap)
        .join(PosItem, PosItem.id == ToastItemMap.pos_item_id)
        .where(*conditions)
    ) or 0
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(page, pages)
    target = sa.orm.aliased(R365Item)
    rows = list(
        db.session.execute(
            sa.select(ToastItemMap, PosItem, Venue, target)
            .join(PosItem, PosItem.id == ToastItemMap.pos_item_id)
            .join(Venue, Venue.id == PosItem.venue_id)
            .join(target, target.item_id == ToastItemMap.r365_item_id, isouter=True)
            .where(*conditions)
            .order_by(ToastItemMap.confidence.desc().nulls_last(), PosItem.name)
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        ).all()
    )
    return rows, pages


def _status_counts() -> dict[str, int]:
    counts = dict(
        db.session.execute(
            sa.select(ToastItemMap.status, sa.func.count()).group_by(ToastItemMap.status)
        ).all()
    )
    counts["all"] = sum(counts.values())
    return counts


def _rejected_counts(item_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Rejected-candidate counts for the given Toast items (badge display)."""
    if not item_ids:
        return {}
    rows = db.session.execute(
        sa.select(ToastItemMap.pos_item_id, sa.func.count())
        .where(
            ToastItemMap.pos_item_id.in_(item_ids),
            ToastItemMap.status == "rejected",
        )
        .group_by(ToastItemMap.pos_item_id)
    ).all()
    return dict(rows)


def _venue_id_or_404(venue_id: str) -> uuid.UUID:
    parsed = _uuid_or_none(venue_id)
    if parsed is None or db.session.get(Venue, parsed) is None:
        abort(404)
    return parsed


def _uuid_or_none(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None
