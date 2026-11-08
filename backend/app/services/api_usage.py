"""Shared SerpApi call allowance and successful-call counter."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import Column, Date, DateTime, Integer, MetaData, String, Table, delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

LIMIT = 200
INITIAL_CALLS_USED = 16
INITIAL_RENEWAL_DATE = date(2026, 11, 18)
USAGE_ID = "serpapi"

metadata = MetaData()
usage_table = Table(
    "serp_api_usage", metadata,
    Column("id", String(32), primary_key=True),
    Column("calls_used", Integer, nullable=False),
    Column("renews_on", Date, nullable=False),
)
reservation_table = Table(
    "serp_api_call_reservations", metadata,
    Column("reservation_id", String(32), primary_key=True),
    Column("renews_on", Date, nullable=False),
    Column("reserved_at", DateTime(timezone=True), nullable=False),
)


class ApiUsageLimitReached(Exception):
    """Raised when no more provider calls are available for this cycle."""


def _add_month(day: date) -> date:
    month = day.month % 12 + 1
    year = day.year + (1 if day.month == 12 else 0)
    # The renewal is always on the 18th, so every month has this date.
    return date(year, month, day.day)


def initialize_usage(engine) -> None:
    metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(pg_insert(usage_table).values(
            id=USAGE_ID,
            calls_used=INITIAL_CALLS_USED,
            renews_on=INITIAL_RENEWAL_DATE,
        ).on_conflict_do_nothing(index_elements=[usage_table.c.id]))


def _current_usage(conn, today: date):
    row = conn.execute(select(usage_table.c.calls_used, usage_table.c.renews_on)
                       .where(usage_table.c.id == USAGE_ID).with_for_update()).one()
    calls_used, renews_on = row
    if today >= renews_on:
        while renews_on <= today:
            renews_on = _add_month(renews_on)
        calls_used = 0
        conn.execute(update(usage_table).where(usage_table.c.id == USAGE_ID)
                     .values(calls_used=0, renews_on=renews_on))
    return calls_used, renews_on


def reserve_call(engine) -> tuple[str, date]:
    """Reserve a slot so concurrent requests cannot exceed the shared cap."""
    now = datetime.now(timezone.utc)
    today = now.date()
    reservation_id = uuid4().hex
    with engine.begin() as conn:
        conn.execute(delete(reservation_table).where(
            reservation_table.c.reserved_at < now - timedelta(minutes=5)
        ))
        calls_used, renews_on = _current_usage(conn, today)
        pending = conn.execute(select(func.count()).select_from(reservation_table)
                               .where(reservation_table.c.renews_on == renews_on)).scalar_one()
        if calls_used + pending >= LIMIT:
            raise ApiUsageLimitReached
        conn.execute(insert(reservation_table).values(
            reservation_id=reservation_id, renews_on=renews_on, reserved_at=now,
        ))
    return reservation_id, renews_on


def record_call(engine, reservation_id: str) -> None:
    """Count a provider ping once SerpApi has returned an HTTP response."""
    with engine.begin() as conn:
        reservation_renewal = conn.execute(select(reservation_table.c.renews_on)
                                           .where(reservation_table.c.reservation_id == reservation_id)
                                           .with_for_update()).scalar_one_or_none()
        if reservation_renewal is None:
            return
        _, current_renewal = _current_usage(conn, datetime.now(timezone.utc).date())
        if reservation_renewal == current_renewal:
            conn.execute(update(usage_table).where(usage_table.c.id == USAGE_ID)
                         .values(calls_used=usage_table.c.calls_used + 1))
        conn.execute(delete(reservation_table)
                     .where(reservation_table.c.reservation_id == reservation_id))


def release_call(engine, reservation_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(delete(reservation_table)
                     .where(reservation_table.c.reservation_id == reservation_id))


def get_usage(engine) -> dict:
    today = datetime.now(timezone.utc).date()
    with engine.begin() as conn:
        calls_used, renews_on = _current_usage(conn, today)
        return {
            "calls_used": calls_used,
            "calls_limit": LIMIT,
            "calls_remaining": max(0, LIMIT - calls_used),
            "renews_on": renews_on.isoformat(),
        }
