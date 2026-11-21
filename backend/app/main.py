"""Flight finder API for trips from College Station to major U.S. hubs."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Date, DateTime, Integer, String, Text, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.session import engine
from app.services.api_usage import ApiUsageLimitReached, get_usage, initialize_usage, record_call, release_call, reserve_call
from app.services.flight_leg import parse_flights
from app.services.major_airports import search_airports

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if not (PROJECT_ROOT / "backend").exists():
    PROJECT_ROOT = BACKEND_DIR
load_dotenv(PROJECT_ROOT / ".env")
logger = logging.getLogger(__name__)
COLLEGE_STATION_TZ = ZoneInfo("America/Chicago")


class Base(DeclarativeBase):
    pass


class FlightSearchCache(Base):
    __tablename__ = "flight_search_cache"
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    origin: Mapped[str] = mapped_column(String(8), nullable=False)
    destination: Mapped[str] = mapped_column(String(8), nullable=False)
    travel_date: Mapped[date] = mapped_column(Date, nullable=False)
    results_json: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FlightSearchResponse(Base):
    __tablename__ = "flight_search_responses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    origin: Mapped[str] = mapped_column(String(8), nullable=False)
    destination: Mapped[str] = mapped_column(String(8), nullable=False)
    travel_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FlightSearchRequest(BaseModel):
    destination: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    earliest_departure: datetime
    arrive_by: datetime

    @field_validator("earliest_departure", "arrive_by")
    @classmethod
    def require_local_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is not None:
            raise ValueError("Use a local date and time without a timezone offset.")
        return value

    @field_validator("arrive_by")
    @classmethod
    def arrival_after_departure(cls, value: datetime, info):
        departure = info.data.get("earliest_departure")
        if departure and value <= departure:
            raise ValueError("Arrival deadline must be after earliest departure.")
        return value


app = FastAPI(title="Aggie Routes", description="Compare flights from College Station to major airports.", version="2.0.0")
CORS_ORIGINS = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "*").split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["GET", "POST"], allow_headers=["*"])


@app.on_event("startup")
def create_tables() -> None:
    Base.metadata.create_all(bind=engine)
    initialize_usage(engine)


def _api_key() -> str | None:
    return os.getenv("SERP_API_KEY") or os.getenv("SERPAPI_API_KEY") or os.getenv("SERPAPI_KEY")


def _request_serp(params: dict[str, str], unavailable_message: str) -> dict:
    key = _api_key()
    if not key:
        raise HTTPException(status_code=503, detail="Flight search is not configured. Set SERP_API_KEY in .env.")
    try:
        reservation_id, _ = reserve_call(engine)
    except ApiUsageLimitReached as exc:
        raise HTTPException(status_code=429, detail="The app's monthly SerpApi call allowance has been used.") from exc
    params = {**params, "api_key": key}
    try:
        try:
            with urlopen(f"https://serpapi.com/search.json?{urlencode(params)}", timeout=30) as response:
                payload = json.loads(response.read())
        except HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"{unavailable_message} (provider HTTP {exc.code})") from exc
        if not isinstance(payload, dict) or payload.get("error"):
            raise HTTPException(status_code=502, detail=payload.get("error", unavailable_message) if isinstance(payload, dict) else unavailable_message)
        # SerpApi's successful results count as searches; provider errors and
        # transport failures release their reservation without incrementing.
        record_call(engine, reservation_id)
        return payload
    except HTTPException:
        release_call(engine, reservation_id)
        raise
    except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        release_call(engine, reservation_id)
        raise HTTPException(status_code=502, detail=unavailable_message) from exc
    except Exception:
        release_call(engine, reservation_id)
        raise


def _cache_key(destination: str, travel_date: date) -> str:
    # Keep the broad provider result set for the whole destination/date. The
    # user's time constraints are applied after reading this cached snapshot.
    raw = f"cll-major-v2:CLL:{destination}:{travel_date.isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _search_flights(destination: str, earliest_departure: datetime, arrive_by: datetime) -> dict:
    travel_date = earliest_departure.date()
    cache_key = _cache_key(destination, travel_date)
    lock_id = int.from_bytes(hashlib.sha256(cache_key.encode()).digest()[:8], "big", signed=True)
    with engine.connect() as lock_conn:
        lock_conn.exec_driver_sql("SELECT pg_advisory_lock(%s)", (lock_id,))
        lock_conn.commit()
        try:
            return _search_flights_locked(destination, earliest_departure, arrive_by, cache_key)
        finally:
            lock_conn.exec_driver_sql("SELECT pg_advisory_unlock(%s)", (lock_id,))
            lock_conn.commit()


def _search_flights_locked(destination: str, earliest_departure: datetime, arrive_by: datetime, cache_key: str) -> dict:
    with engine.connect() as conn:
        cached = conn.execute(select(FlightSearchCache.results_json, FlightSearchCache.fetched_at)
                              .where(FlightSearchCache.cache_key == cache_key)).one_or_none()
    if cached:
        offers = json.loads(cached.results_json)
        fetched_at = cached.fetched_at
        was_cached = True
    else:
        payload = _request_serp({
            "engine": "google_flights", "departure_id": "CLL", "arrival_id": destination,
            "outbound_date": earliest_departure.date().isoformat(), "type": "2",
            "stops": "3",
            "currency": "USD", "hl": "en", "gl": "us",
        }, "Flight search provider is temporarily unavailable.")
        offers = parse_flights(payload)
        fetched_at = datetime.now(timezone.utc)
        with engine.begin() as conn:
            conn.execute(insert(FlightSearchCache).values(
                cache_key=cache_key, origin="CLL", destination=destination,
                travel_date=earliest_departure.date(), results_json=json.dumps(offers), fetched_at=fetched_at,
            ).on_conflict_do_update(index_elements=[FlightSearchCache.cache_key], set_={
                "results_json": json.dumps(offers), "fetched_at": fetched_at,
            }))
            conn.execute(insert(FlightSearchResponse).values(
                cache_key=cache_key, origin="CLL", destination=destination,
                travel_date=earliest_departure.date(), response_json=json.dumps(payload), fetched_at=fetched_at,
            ))
        was_cached = False

    # Apply exact user constraints to the broad cached snapshot, using the
    # airport-local wall-clock values in each SerpApi itinerary.
    matching = []
    for offer in offers:
        try:
            departure = datetime.strptime(offer["departure_time"], "%Y-%m-%d %H:%M")
            arrival = datetime.strptime(offer["arrival_time"], "%Y-%m-%d %H:%M")
        except (KeyError, TypeError, ValueError):
            continue
        if departure < earliest_departure or arrival > arrive_by or len(offer.get("segments", [])) > 3:
            continue
        matching.append({**offer, "cached": was_cached, "cached_at": fetched_at.isoformat()})
    cheapest = sorted(matching, key=lambda item: (item["price_usd"] if item.get("price_usd") is not None else float("inf"), item["duration_min"] or float("inf")))
    quickest = sorted(matching, key=lambda item: (item["duration_min"] or float("inf"), item["price_usd"] if item.get("price_usd") is not None else float("inf")))
    return {"destination": destination, "origin": "CLL", "earliest_departure": earliest_departure.isoformat(),
            "arrive_by": arrive_by.isoformat(), "cached": was_cached, "cached_at": fetched_at.isoformat(),
            "results": cheapest, "cheapest": cheapest[0] if cheapest else None,
            "quickest": quickest[0] if quickest else None}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/db")
def health_db():
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return {"status": "ok", "db": "connected"}
    except Exception:
        return {"status": "error", "db": "unavailable"}


@app.get("/api/airports")
def airports(q: str = Query("", max_length=80)):
    return {"airports": search_airports(q)}


@app.get("/api/usage")
def api_usage():
    usage = get_usage(engine)
    key = _api_key()
    if not key:
        return {**usage, "provider": None}
    # SerpApi documents account.json as a free endpoint with plan searches left.
    try:
        with urlopen(f"https://serpapi.com/account.json?{urlencode({'api_key': key})}", timeout=10) as response:
            account = json.loads(response.read())
        if account.get("error"):
            raise ValueError("Account API returned an error")
        usage["provider"] = {
            "searches_used": account.get("this_month_usage"),
            "searches_limit": account.get("searches_per_month"),
            "searches_remaining": account.get("plan_searches_left", account.get("total_searches_left")),
            "renews_on": account.get("plan_renewal_date"),
        }
    except Exception:
        logger.exception("Could not fetch SerpApi account usage")
        usage["provider"] = None
    return usage


@app.post("/api/flights/search")
def search(body: FlightSearchRequest):
    destination = body.destination.upper()
    if destination == "CLL":
        raise HTTPException(status_code=422, detail="Choose a destination other than College Station (CLL).")
    if body.earliest_departure.date() < datetime.now(COLLEGE_STATION_TZ).date():
        raise HTTPException(status_code=422, detail="Departure date has already passed.")
    return _search_flights(destination, body.earliest_departure, body.arrive_by)
