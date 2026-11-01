"""Aggie flight finder API with a PostgreSQL-backed SerpApi cache."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo
import calendar

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import Date, DateTime, Float, Integer, String, Text, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from dotenv import load_dotenv

from app.db.session import engine
from app.services.airport_lookup import nearest_airport
from app.services.flight_leg import parse_flights

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if not (PROJECT_ROOT / "backend").exists():  # Docker build context is backend/.
    PROJECT_ROOT = BACKEND_DIR
load_dotenv(PROJECT_ROOT / ".env")
MONTHLY_SEARCH_LIMIT = 200
DATE_WINDOW_AFTER_CALLS = 150
MAX_SEARCHES_PER_IP_PER_DAY = 3
GLOBAL_MIN_REQUEST_INTERVAL_SECONDS = float(os.getenv("GLOBAL_MIN_REQUEST_INTERVAL_SECONDS", "1"))
LOCAL_TIMEZONE = ZoneInfo("America/Chicago")
logger = logging.getLogger(__name__)


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


class SearchUsage(Base):
    __tablename__ = "serp_search_usage"
    month: Mapped[str] = mapped_column(String(7), primary_key=True)
    searches: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class FlightSearchResponse(Base):
    """Complete SerpApi response archive, retained through the travel date."""
    __tablename__ = "flight_search_responses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    origin: Mapped[str] = mapped_column(String(8), nullable=False)
    destination: Mapped[str] = mapped_column(String(8), nullable=False)
    travel_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IpDailyUsage(Base):
    __tablename__ = "ip_daily_search_usage"
    ip_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    search_date: Mapped[date] = mapped_column(Date, primary_key=True)
    searches: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class GlobalRateLimit(Base):
    __tablename__ = "global_rate_limits"
    scope: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_request_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AddressAirportCache(Base):
    __tablename__ = "address_airport_cache"
    address_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    airport_code: Mapped[str] = mapped_column(String(3), nullable=False)
    airport_name: Mapped[str] = mapped_column(String(200), nullable=False)
    city: Mapped[str] = mapped_column(String(120), nullable=False)
    country: Mapped[str] = mapped_column(String(120), nullable=False)
    distance_km: Mapped[float] = mapped_column(Float, nullable=False)
    mapped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TripPlanRequest(BaseModel):
    home_address: str = Field(min_length=8, max_length=300)
    travel_date: date


app = FastAPI(title="Aggie Routes", description="Find flights from College Station and plan the trip home.", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


@app.on_event("startup")
def create_tables() -> None:
    Base.metadata.create_all(bind=engine)
    _purge_departed_flights(datetime.now(LOCAL_TIMEZONE))
    with engine.begin() as conn:
        conn.execute(IpDailyUsage.__table__.delete().where(IpDailyUsage.search_date < datetime.now(LOCAL_TIMEZONE).date()))


def _cache_key(origin: str, destination: str, travel_date: date) -> str:
    raw = f"itinerary-v3:{origin.upper()}:{destination.upper()}:{travel_date.isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _api_key() -> str | None:
    return os.getenv("SERP_API_KEY") or os.getenv("SERPAPI_KEY")


def _calendar_month_after(day: date) -> date:
    month = day.month % 12 + 1
    year = day.year + (1 if day.month == 12 else 0)
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _client_ip_hash(request: Request) -> str:
    raw_ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
    raw_ip = raw_ip.split(",", 1)[0].strip()
    try:
        raw_ip = str(ipaddress.ip_address(raw_ip))
    except ValueError:
        raw_ip = request.client.host if request.client else "unknown"
    return hashlib.sha256(raw_ip.encode()).hexdigest()


def _check_search_request(request: Request, travel_date: date) -> str:
    """Apply the shared inbound request rate and monthly date window."""
    now = datetime.now(timezone.utc)
    today = now.astimezone(LOCAL_TIMEZONE).date()
    ip_hash = _client_ip_hash(request)
    month = now.strftime("%Y-%m")
    interval = max(0.0, GLOBAL_MIN_REQUEST_INTERVAL_SECONDS)
    with engine.begin() as conn:
        conn.execute(insert(GlobalRateLimit).values(scope="user_search", last_request_at=None)
                     .on_conflict_do_nothing(index_elements=[GlobalRateLimit.scope]))
        last_request = conn.execute(select(GlobalRateLimit.last_request_at)
                                    .where(GlobalRateLimit.scope == "user_search").with_for_update()).scalar_one()
        current = datetime.now(timezone.utc)
        if last_request is not None and (current - last_request).total_seconds() < interval:
            retry_after = max(1, int(interval - (current - last_request).total_seconds() + 0.999))
            raise HTTPException(status_code=429, detail="Searches are limited to one request per second across all users.",
                                headers={"Retry-After": str(retry_after)})
        conn.execute(GlobalRateLimit.__table__.update().where(GlobalRateLimit.scope == "user_search")
                     .values(last_request_at=current))

    with engine.begin() as conn:
        month_count = conn.execute(select(SearchUsage.searches).where(SearchUsage.month == month)).scalar_one_or_none() or 0
    if month_count >= DATE_WINDOW_AFTER_CALLS and travel_date > _calendar_month_after(today):
        raise HTTPException(status_code=422, detail="After 150 monthly API calls, travel dates must be within the next month.")
    return ip_hash


def _reserve_serp_call(ip_hash: str) -> None:
    """Atomically apply the per-IP daily cap and monthly SerpApi ceiling."""
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    today = datetime.now(LOCAL_TIMEZONE).date()
    with engine.begin() as conn:
        conn.execute(IpDailyUsage.__table__.delete().where(IpDailyUsage.search_date < today))
        conn.execute(insert(IpDailyUsage).values(ip_hash=ip_hash, search_date=today, searches=0)
                     .on_conflict_do_nothing(index_elements=[IpDailyUsage.ip_hash, IpDailyUsage.search_date]))
        daily_count = conn.execute(select(IpDailyUsage.searches)
                                   .where(IpDailyUsage.ip_hash == ip_hash, IpDailyUsage.search_date == today)
                                   .with_for_update()).scalar_one()
        if daily_count >= MAX_SEARCHES_PER_IP_PER_DAY:
            raise HTTPException(status_code=429, detail="Daily SerpApi limit reached: no more than 3 API calls per IP per day.")

        conn.execute(insert(SearchUsage).values(month=month, searches=0)
                     .on_conflict_do_nothing(index_elements=[SearchUsage.month]))
        used = conn.execute(select(SearchUsage.searches).where(SearchUsage.month == month).with_for_update()).scalar_one()
        if used >= MONTHLY_SEARCH_LIMIT:
            raise HTTPException(status_code=429,
                detail="Monthly limit of 200 SerpApi calls reached. No more live flight data will be fetched or tracked this month; saved results remain available.")
        conn.execute(SearchUsage.__table__.update().where(SearchUsage.month == month).values(searches=used + 1))
        conn.execute(IpDailyUsage.__table__.update()
                     .where(IpDailyUsage.ip_hash == ip_hash, IpDailyUsage.search_date == today)
                     .values(searches=daily_count + 1))


def _wait_for_serp_slot() -> None:
    """Serialize all outbound SerpApi calls to the configured global rate."""
    interval = max(0.0, GLOBAL_MIN_REQUEST_INTERVAL_SECONDS)
    with engine.begin() as conn:
        conn.execute(insert(GlobalRateLimit).values(scope="serp_api", last_request_at=None)
                     .on_conflict_do_nothing(index_elements=[GlobalRateLimit.scope]))
        last_request = conn.execute(select(GlobalRateLimit.last_request_at)
                                    .where(GlobalRateLimit.scope == "serp_api").with_for_update()).scalar_one()
        now = datetime.now(timezone.utc)
        if last_request is not None:
            wait_seconds = interval - (now - last_request).total_seconds()
            if wait_seconds > 0:
                time.sleep(wait_seconds)
        conn.execute(GlobalRateLimit.__table__.update().where(GlobalRateLimit.scope == "serp_api")
                     .values(last_request_at=datetime.now(timezone.utc)))


def _fetch_serp(origin: str, destination: str, travel_date: date) -> dict:
    key = _api_key()
    if not key:
        raise HTTPException(status_code=503, detail="Flight search is not configured. Set SERP_API_KEY in .env.")
    params = urlencode({"engine": "google_flights", "departure_id": origin, "arrival_id": destination,
                        "outbound_date": travel_date.isoformat(), "type": "2", "stops": "3",
                        "currency": "USD", "hl": "en", "gl": "us", "api_key": key})
    try:
        with urlopen(f"https://serpapi.com/search.json?{params}", timeout=20) as response:
            payload = json.loads(response.read())
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Flight search provider is temporarily unavailable.") from exc
    if payload.get("error"):
        raise HTTPException(status_code=502, detail="Flight search provider rejected the request.")
    return payload


def _fetch_address_coordinates(address: str) -> tuple[float, float]:
    key = _api_key()
    if not key:
        raise HTTPException(status_code=503, detail="Address lookup is not configured. Set SERP_API_KEY in .env.")
    params = urlencode({"engine": "google_maps", "type": "search", "q": address,
                        "hl": "en", "gl": "us", "api_key": key})
    try:
        with urlopen(f"https://serpapi.com/search.json?{params}", timeout=20) as response:
            payload = json.loads(response.read())
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Address lookup provider is temporarily unavailable.") from exc
    if payload.get("error"):
        raise HTTPException(status_code=502, detail="Address lookup provider could not map that address.")
    candidates = []
    if isinstance(payload.get("place_results"), dict):
        candidates.append(payload["place_results"])
    candidates.extend(payload.get("local_results") or [])
    for candidate in candidates:
        coords = candidate.get("gps_coordinates") if isinstance(candidate, dict) else None
        if not isinstance(coords, dict):
            continue
        try:
            latitude, longitude = float(coords["latitude"]), float(coords["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if -90 <= latitude <= 90 and -180 <= longitude <= 180:
            return latitude, longitude
    raise HTTPException(status_code=422, detail="Could not locate that address. Enter a full street address, city, state, and ZIP code.")


def _address_airport(address: str, ip_hash: str) -> dict:
    normalized = " ".join(address.casefold().split())
    address_hash = hashlib.sha256(normalized.encode()).hexdigest()
    lock_id = int.from_bytes(hashlib.sha256(f"address:{address_hash}".encode()).digest()[:8], "big", signed=True)
    with engine.connect() as lock_conn:
        lock_conn.exec_driver_sql("SELECT pg_advisory_lock(%s)", (lock_id,))
        lock_conn.commit()
        try:
            with engine.begin() as conn:
                cached = conn.execute(select(AddressAirportCache.airport_code, AddressAirportCache.airport_name,
                                             AddressAirportCache.city, AddressAirportCache.country,
                                             AddressAirportCache.distance_km, AddressAirportCache.mapped_at)
                                      .where(AddressAirportCache.address_hash == address_hash)).one_or_none()
            if cached:
                return {"code": cached.airport_code, "name": cached.airport_name, "city": cached.city,
                        "country": cached.country, "distance_km": cached.distance_km,
                        "cached": True, "mapped_at": cached.mapped_at.isoformat()}

            _reserve_serp_call(ip_hash)
            _wait_for_serp_slot()
            latitude, longitude = _fetch_address_coordinates(address)
            nearest = nearest_airport(latitude, longitude)
            if not nearest:
                raise HTTPException(status_code=500, detail="Airport data is unavailable.")
            airport, distance_km = nearest
            mapped_at = datetime.now(timezone.utc)
            with engine.begin() as conn:
                conn.execute(insert(AddressAirportCache).values(
                    address_hash=address_hash, airport_code=airport["code"], airport_name=airport["name"],
                    city=airport["city"], country=airport["country"], distance_km=distance_km, mapped_at=mapped_at,
                ).on_conflict_do_nothing(index_elements=[AddressAirportCache.address_hash]))
            return {"code": airport["code"], "name": airport["name"], "city": airport["city"],
                    "country": airport["country"], "distance_km": distance_km,
                    "cached": False, "mapped_at": mapped_at.isoformat()}
        finally:
            lock_conn.exec_driver_sql("SELECT pg_advisory_unlock(%s)", (lock_id,))
            lock_conn.commit()


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


@app.get("/api/usage")
def usage():
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    with engine.begin() as conn:
        count = conn.execute(select(SearchUsage.searches).where(SearchUsage.month == month)).scalar_one_or_none() or 0
    return {
        "month": month,
        "searches_used": count,
        "api_calls_used": count,
        "searches_limit": MONTHLY_SEARCH_LIMIT,
        "api_calls_limit": MONTHLY_SEARCH_LIMIT,
        "searches_remaining": max(0, MONTHLY_SEARCH_LIMIT-count),
        "live_data_enabled": count < MONTHLY_SEARCH_LIMIT,
        "live_data_notice": (
            "Monthly SerpApi limit reached. No more live flight data will be fetched or tracked this month."
            if count >= MONTHLY_SEARCH_LIMIT else None
        ),
        "date_window_days": 30 if count >= DATE_WINDOW_AFTER_CALLS else None,
    }


@app.get("/api/flights")
def flights(request: Request,
            origin: str = Query("CLL", min_length=3, max_length=4),
            destination: str = Query(..., min_length=3, max_length=4),
            travel_date: date = Query(..., alias="date")):
    request_now = datetime.now(LOCAL_TIMEZONE)
    _purge_departed_flights(request_now)
    ip_hash = _check_search_request(request, travel_date)
    origin, destination = origin.upper(), destination.upper()
    if travel_date < request_now.date():
        raise HTTPException(status_code=422, detail="Departure date has already passed.")
    return _get_route(origin, destination, travel_date, ip_hash)


@app.post("/api/trip-plans")
def trip_plans(body: TripPlanRequest, request: Request):
    request_now = datetime.now(LOCAL_TIMEZONE)
    _purge_departed_flights(request_now)
    ip_hash = _check_search_request(request, body.travel_date)
    if body.travel_date < request_now.date():
        raise HTTPException(status_code=422, detail="Departure date has already passed.")
    airport = _address_airport(body.home_address, ip_hash)
    origins = ("CLL", "IAH", "HOU")
    route_results: dict[str, dict] = {}
    missing_origins = []
    for origin in origins:
        cached = _read_cached_route(origin, airport["code"], body.travel_date)
        if cached is None:
            missing_origins.append(origin)
        else:
            route_results[origin] = cached

    if missing_origins:
        try:
            # SerpApi accepts comma-separated departure airports, so one ping
            # can cover every uncached Aggie departure airport for this trip.
            combined = _get_route(",".join(missing_origins), airport["code"], body.travel_date, ip_hash)
            for origin in missing_origins:
                origin_flights = [f for f in combined["flights"] if f.get("origin") == origin]
                route_key = _cache_key(origin, airport["code"], body.travel_date)
                # A multi-origin provider response also warms the ordinary
                # per-origin cache keys so later trips can reuse it directly.
                with engine.begin() as conn:
                    conn.execute(insert(FlightSearchCache).values(
                        cache_key=route_key, origin=origin, destination=airport["code"],
                        travel_date=body.travel_date, results_json=json.dumps(origin_flights),
                        fetched_at=datetime.now(timezone.utc),
                    ).on_conflict_do_nothing(index_elements=[FlightSearchCache.cache_key]))
                route_results[origin] = {
                    "flights": origin_flights,
                    "cached": combined["cached"],
                    "cached_at": combined["cached_at"],
                    "buffer_minutes": 120 if origin in {"IAH", "HOU"} else 30,
                }
        except HTTPException as exc:
            for origin in missing_origins:
                route_results[origin] = {"flights": [], "cached": False, "cached_at": None,
                    "buffer_minutes": 120 if origin in {"IAH", "HOU"} else 30, "error": exc.detail}

    routes = [{"origin": origin, **route_results[origin]} for origin in origins]
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    with engine.begin() as conn:
        calls = conn.execute(select(SearchUsage.searches).where(SearchUsage.month == month)).scalar_one_or_none() or 0
    return {
        "destination_airport": airport,
        "travel_date": body.travel_date.isoformat(),
        "routes": routes,
        "api_calls_used": calls,
        "api_calls_limit": MONTHLY_SEARCH_LIMIT,
        "live_data_enabled": calls < MONTHLY_SEARCH_LIMIT,
    }


def _read_cached_route(origin: str, destination: str, travel_date: date) -> dict | None:
    key = _cache_key(origin, destination, travel_date)
    with engine.connect() as conn:
        cached = conn.execute(select(FlightSearchCache.results_json, FlightSearchCache.fetched_at)
                              .where(FlightSearchCache.cache_key == key)).one_or_none()
    if not cached:
        return None
    return _flight_response(json.loads(cached.results_json), origin, cached.fetched_at, cached=True)


def _get_route(origin: str, destination: str, travel_date: date, ip_hash: str) -> dict:
    key = _cache_key(origin, destination, travel_date)
    # Hold a PostgreSQL session lock until the provider response is cached. This
    # prevents parallel requests for the same leg/date from using two searches.
    lock_id = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
    with engine.connect() as lock_conn:
        lock_conn.exec_driver_sql("SELECT pg_advisory_lock(%s)", (lock_id,))
        lock_conn.commit()
        try:
            return _search_locked(origin, destination, travel_date, key, ip_hash)
        finally:
            lock_conn.exec_driver_sql("SELECT pg_advisory_unlock(%s)", (lock_id,))
            lock_conn.commit()


def _search_locked(origin: str, destination: str, travel_date: date, key: str, ip_hash: str) -> dict:
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        cached = conn.execute(
            select(FlightSearchCache.results_json, FlightSearchCache.fetched_at)
            .where(FlightSearchCache.cache_key == key)
        ).one_or_none()
        if cached:
            return _flight_response(json.loads(cached.results_json), origin, cached.fetched_at, cached=True)

    _reserve_serp_call(ip_hash)
    _wait_for_serp_slot()

    payload = _fetch_serp(origin, destination, travel_date)
    offers = parse_flights(payload)
    for offer in offers:
        segment_predictions = []
        for segment in offer["segments"]:
            prediction = _predict_delay(segment["model_features"])
            segment["delay_prediction"] = prediction
            if prediction is not None:
                segment_predictions.append(prediction)
        if len(segment_predictions) == len(offer["segments"]):
            predicted_delay = round(sum(p["predicted_delay_min"] for p in segment_predictions), 1)
            buffer_margin = round(sum(p["buffer_margin_min"] for p in segment_predictions), 1)
            offer["delay_prediction"] = {
                "predicted_delay_min": predicted_delay,
                "buffer_margin_min": buffer_margin,
                "recommended_buffer_min": round(predicted_delay + buffer_margin, 1),
                "confidence_pct": min(p["confidence_pct"] for p in segment_predictions),
            }
            scheduled = datetime.strptime(offer["arrival_time"], "%Y-%m-%d %H:%M")
            offer["estimated_arrival_time"] = (scheduled + timedelta(minutes=predicted_delay)).strftime("%Y-%m-%d %H:%M")

    with engine.begin() as conn:
        stored_origin = origin if len(origin) <= 8 else "MULTI"
        stmt = insert(FlightSearchCache).values(cache_key=key, origin=stored_origin, destination=destination,
                    travel_date=travel_date, results_json=json.dumps(offers), fetched_at=now)
        conn.execute(stmt.on_conflict_do_update(index_elements=[FlightSearchCache.cache_key], set_={"results_json": json.dumps(offers), "fetched_at": now}))
        conn.execute(insert(FlightSearchResponse).values(cache_key=key, origin=stored_origin, destination=destination,
                    travel_date=travel_date, response_json=json.dumps(payload), fetched_at=now))
    return _flight_response(offers, origin, now, cached=False)


def _airport_buffer(origin: str) -> int:
    return 120 if origin in {"IAH", "HOU"} else 30


def _connection_analysis(offer: dict) -> list[dict]:
    segments = offer.get("segments") or []
    connections = []
    for incoming, outgoing in zip(segments, segments[1:]):
        airport = incoming.get("destination")
        if not airport or airport != outgoing.get("origin"):
            continue
        try:
            arrival = datetime.strptime(incoming["arrival_time"], "%Y-%m-%d %H:%M")
            departure = datetime.strptime(outgoing["departure_time"], "%Y-%m-%d %H:%M")
        except (KeyError, TypeError, ValueError):
            continue
        layover_minutes = int((departure - arrival).total_seconds() // 60)
        if layover_minutes < 0:
            continue
        incoming_prediction = incoming.get("delay_prediction") or {}
        p90_buffer = incoming_prediction.get("recommended_buffer_min")
        risky = p90_buffer is not None and layover_minutes < float(p90_buffer)
        connections.append({
            "airport": airport,
            "arrival_time": incoming["arrival_time"],
            "departure_time": outgoing["departure_time"],
            "layover_minutes": layover_minutes,
            "p90_buffer_minutes": round(float(p90_buffer), 1) if p90_buffer is not None else None,
            "analyzed_with_p90": p90_buffer is not None,
            "scheduled_connection_possible": True,
            "risky": risky,
        })
    return connections


def _flight_response(offers: list[dict], origin: str, cached_at: datetime, cached: bool) -> dict:
    origins = origin.split(",")
    buffer_by_origin = {code: _airport_buffer(code) for code in origins}
    now = datetime.now(LOCAL_TIMEZONE)
    upcoming = []
    for offer in offers:
        offer_origin = offer.get("origin") or origins[0]
        if offer_origin not in buffer_by_origin:
            continue
        try:
            departure = datetime.strptime(offer["departure_time"], "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL_TIMEZONE)
        except (KeyError, TypeError, ValueError):
            continue
        if offer.get("stops", 0) > 2:
            continue
        offer["connections"] = _connection_analysis(offer)
        if departure > now + timedelta(minutes=buffer_by_origin[offer_origin]):
            upcoming.append(offer)
    return {
        "flights": upcoming,
        "cached": cached,
        "cached_at": cached_at.isoformat(),
        "buffer_minutes": buffer_by_origin[origins[0]] if len(origins) == 1 else None,
        "buffer_minutes_by_origin": buffer_by_origin,
        "response_schema": "itinerary-v3",
    }


def _departure_is_future(value: object, now: datetime) -> bool:
    if not isinstance(value, str):
        return True
    try:
        departure = datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL_TIMEZONE)
    except ValueError:
        return True
    return departure > now


def _purge_departed_flights(now: datetime) -> None:
    """Remove departed offers from both normalized cache and archived provider JSON."""
    with engine.begin() as conn:
        cache_rows = conn.execute(
            select(FlightSearchCache.cache_key, FlightSearchCache.results_json)
            .order_by(FlightSearchCache.cache_key)
            .with_for_update()
        ).all()
        for row in cache_rows:
            try:
                offers = json.loads(row.results_json)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(offers, list):
                continue
            remaining = [offer for offer in offers
                         if not isinstance(offer, dict) or _departure_is_future(offer.get("departure_time"), now)]
            if len(remaining) != len(offers):
                conn.execute(FlightSearchCache.__table__.update()
                             .where(FlightSearchCache.cache_key == row.cache_key)
                             .values(results_json=json.dumps(remaining)))

        response_rows = conn.execute(
            select(FlightSearchResponse.id, FlightSearchResponse.response_json)
            .order_by(FlightSearchResponse.id)
            .with_for_update()
        ).all()
        for row in response_rows:
            try:
                payload = json.loads(row.response_json)
            except (TypeError, json.JSONDecodeError):
                continue
            changed = False
            for offer_key in ("best_flights", "other_flights"):
                offers = payload.get(offer_key)
                if not isinstance(offers, list):
                    continue
                remaining = []
                for offer in offers:
                    if not isinstance(offer, dict):
                        remaining.append(offer)
                        continue
                    segments = offer.get("flights") or []
                    departure = segments[0].get("departure_airport", {}).get("time") if segments else None
                    if _departure_is_future(departure, now):
                        remaining.append(offer)
                if len(remaining) != len(offers):
                    payload[offer_key] = remaining
                    changed = True
            if changed:
                conn.execute(FlightSearchResponse.__table__.update()
                             .where(FlightSearchResponse.id == row.id)
                             .values(response_json=json.dumps(payload)))


def _predict_delay(features: dict) -> dict | None:
    if not features:
        return None
    try:
        from ml.predict import DelayPredictor
        if not hasattr(app.state, "predictor"):
            app.state.predictor = DelayPredictor(str(BACKEND_DIR / "ml/artifacts"))
        return app.state.predictor.predict_buffer(features)
    except Exception:
        if not getattr(app.state, "prediction_error_logged", False):
            logger.exception("Could not load flight delay model artifacts or predict a segment")
            app.state.prediction_error_logged = True
        return None
