"""Candidate suggestion engine: proposes Toast item -> R365 item mappings.

An offline scoring pass, run as `flask map-suggest`. It scores every active
Toast catalog item against the R365 item catalog (token-inverted index, then
full scoring of the sharing-token candidates) and writes the single best
candidate per item with status `suggested` — it never approves.

Review decisions are respected: a pair a human rejected is never re-proposed,
and approved mappings are never touched. A stale engine suggestion (the best
candidate changed since the last run) is deleted and re-proposed, because an
undecided `suggested` row is regenerable engine state, not history.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

import sqlalchemy as sa

from app.extensions import db
from app.mapping.normalize import ordered_tokens, significant_tokens
from app.mapping.scoring import score_pair
from app.models.catalog import PosItem
from app.models.mapping import ToastItemMap
from app.models.purchasing import R365Item
from app.models.venue import Venue

DEFAULT_MIN_CONFIDENCE = 0.60

# "ZZZ DNU ..." R365 items are do-not-use/retired SKUs; proposing them would
# push reviewers toward dead purchasing records.
_DNU_PREFIX = "zzz dnu"

# Confidence buckets for the run report (label -> (low, high) as 2 decimals).
_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("0.90+", 0.90, 1.0),
    ("0.80\u20130.89", 0.80, 0.8999),
    ("0.70\u20130.79", 0.70, 0.7999),
    ("0.60\u20130.69", 0.60, 0.6999),
)


@dataclass(frozen=True)
class _R365Candidate:
    """One R365 item as the engine sees it (precomputed tokens/sizes)."""

    item_id: uuid.UUID
    name: str
    category1: str | None
    category2: str | None
    tokens: frozenset[str]


@dataclass
class SuggestStats:
    """What one engine run did — the CLI prints it, tests assert on it."""

    items_considered: int = 0
    suggestions_created: int = 0
    suggestions_updated: int = 0
    skipped_approved: int = 0
    skipped_rejected: int = 0  # best candidate is a human-rejected pair
    kept_stale: int = 0  # existing suggestion kept (no better candidate)
    unmatched: int = 0  # no candidate reached the threshold
    confidence_buckets: dict[str, int] = field(
        default_factory=lambda: {label: 0 for label, _, _ in _BUCKETS}
    )

    def bucket_for(self, score: float) -> str:
        for label, low, high in _BUCKETS:
            if low <= round(score, 2) <= high:
                return label
        return "0.60–0.69" if score >= DEFAULT_MIN_CONFIDENCE else _BUCKETS[-1][0]

    def record_suggestion(self, score: float) -> None:
        label = self.bucket_for(score)
        self.confidence_buckets[label] = self.confidence_buckets.get(label, 0) + 1


def build_r365_index(session: sa.Session) -> tuple[list[_R365Candidate], dict[str, set[int]]]:
    """Load the R365 catalog and invert it: significant token -> candidate rows."""
    rows = session.execute(
        sa.select(R365Item.item_id, R365Item.name, R365Item.category1, R365Item.category2).where(
            R365Item.name.is_not(None)
        )
    ).all()

    candidates: list[_R365Candidate] = []
    index: dict[str, set[int]] = {}
    for item_id, name, category1, category2 in rows:
        if name.strip().lower().startswith(_DNU_PREFIX):
            continue
        tokens = significant_tokens(name)
        candidates.append(
            _R365Candidate(
                item_id=item_id, name=name, category1=category1, category2=category2, tokens=tokens
            )
        )
        for token in tokens:
            index.setdefault(token, set()).add(len(candidates) - 1)
    return candidates, index


def best_candidate(
    pos_item: PosItem,
    candidates: list[_R365Candidate],
    index: dict[str, set[int]],
    *,
    min_confidence: float,
) -> tuple[_R365Candidate, float] | None:
    """Best R365 candidate for one Toast item, or None below the threshold.

    Ties break on the R365 name so runs are deterministic for tests and
    re-runs produce stable suggestions.
    """
    token_ids: set[int] = set()
    for token in ordered_tokens(pos_item.name):
        token_ids |= index.get(token, set())
    if not token_ids:
        return None

    best: tuple[_R365Candidate, float] | None = None
    for i in token_ids:
        candidate = candidates[i]
        score = score_pair(
            pos_item.name,
            candidate.name,
            toast_category=pos_item.category,
            r365_categories=(candidate.category1 or "", candidate.category2 or ""),
        ).total
        if best is None or score > best[1] or (score == best[1] and candidate.name < best[0].name):
            best = (candidate, score)

    if best is not None and best[1] >= min_confidence:
        return best
    return None


def suggest_mappings(
    *,
    venue_id: uuid.UUID | None = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    dry_run: bool = False,
) -> SuggestStats:
    """Score the active Toast catalog and write suggested mappings.

    Pass `venue_id` to scope to one venue; None means every venue. With
    `dry_run` the pass scores and reports without writing.
    """
    session = db.session
    stats = SuggestStats()

    candidates, index = build_r365_index(session)
    pos_query = (
        sa.select(PosItem)
        .join(Venue, Venue.id == PosItem.venue_id)
        .where(PosItem.is_active.is_(True), Venue.is_active.is_(True))
        .order_by(PosItem.name)
    )
    if venue_id is not None:
        pos_query = pos_query.where(PosItem.venue_id == venue_id)
    pos_items = list(session.scalars(pos_query))
    stats.items_considered = len(pos_items)

    # Existing review state, bulk-loaded: the one active row per item (the
    # partial unique index guarantees at most one) and all rejected targets.
    active_by_item: dict[uuid.UUID, ToastItemMap] = {}
    rejected_pairs: dict[uuid.UUID, set[uuid.UUID]] = {}
    if pos_items:
        item_ids = [item.id for item in pos_items]
        existing = session.scalars(
            sa.select(ToastItemMap).where(ToastItemMap.pos_item_id.in_(item_ids))
        ).all()
        for row in existing:
            if row.status == "rejected":
                rejected_pairs.setdefault(row.pos_item_id, set()).add(row.r365_item_id)
            elif row.status in ToastItemMap.ACTIVE_STATUSES:
                active_by_item[row.pos_item_id] = row

    for item in pos_items:
        active = active_by_item.get(item.id)
        if active is not None and active.status == "approved":
            stats.skipped_approved += 1
            continue

        best = best_candidate(item, candidates, index, min_confidence=min_confidence)
        if best is None:
            if active is None:
                stats.unmatched += 1
            else:
                stats.kept_stale += 1
            continue

        candidate, score = best
        if candidate.item_id in rejected_pairs.get(item.id, set()):
            stats.skipped_rejected += 1
            continue

        if active is not None and active.status == "suggested":
            if active.r365_item_id == candidate.item_id:
                new_confidence = Decimal(str(round(score, 4)))
                if active.confidence != new_confidence:
                    if not dry_run:
                        active.confidence = new_confidence
                    stats.suggestions_updated += 1
                else:
                    stats.kept_stale += 1
            else:
                # The engine's earlier proposal lost to a better candidate.
                # Undecided suggested rows are regenerable engine state.
                if not dry_run:
                    session.delete(active)
                    session.flush()  # clear the active slot before inserting
                    session.add(
                        ToastItemMap(
                            pos_item_id=item.id,
                            r365_item_id=candidate.item_id,
                            status="suggested",
                            source="auto",
                            confidence=Decimal(str(round(score, 4))),
                        )
                    )
                stats.suggestions_created += 1
            stats.record_suggestion(score)
        else:
            if not dry_run:
                session.add(
                    ToastItemMap(
                        pos_item_id=item.id,
                        r365_item_id=candidate.item_id,
                        status="suggested",
                        source="auto",
                        confidence=Decimal(str(round(score, 4))),
                    )
                )
            stats.suggestions_created += 1
            stats.record_suggestion(score)

    if not dry_run:
        session.commit()
    return stats
