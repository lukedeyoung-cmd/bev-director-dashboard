"""Reporting-window resolution for the dashboard.

The dashboard is anchored to the newest business date present in the data
rather than "today", so the pages stay meaningful before ingestion catches
up (and with fixture data). Every scope (group or single venue) resolves its
own anchor from `daily_sales`.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.models.sales import DailySales


@dataclass(frozen=True)
class Period:
    """An inclusive date range."""

    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def prior(self) -> "Period":
        """The equal-length window immediately before this one."""
        prior_end = self.start - timedelta(days=1)
        return Period(start=prior_end - timedelta(days=self.days - 1), end=prior_end)


@dataclass(frozen=True)
class Window:
    """Current reporting period plus its prior comparison window."""

    current: Period
    prior: Period


def resolve_window(
    session: Session, venue_ids: list, days: int
) -> Window | None:
    """Anchor a `days`-long window at the newest business date in scope.

    Returns None when the scope has no sales data at all, so callers can
    render an empty state instead of a wall of zeros.
    """
    if not venue_ids:
        return None
    anchor = session.scalar(
        sa.select(sa.func.max(DailySales.business_date)).where(
            DailySales.venue_id.in_(venue_ids)
        )
    )
    if anchor is None:
        return None
    current = Period(start=anchor - timedelta(days=days - 1), end=anchor)
    return Window(current=current, prior=current.prior())
