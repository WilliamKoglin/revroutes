"""Normalize SerpApi Google Flights offers into compact itineraries."""


def _parse_offer(offer: dict) -> dict | None:
    flights = offer.get("flights") or []
    if not flights or len(flights) > 3:
        return None
    segments = []
    for flight in flights:
        dep = flight.get("departure_airport") or {}
        arr = flight.get("arrival_airport") or {}
        if not dep.get("id") or not arr.get("id") or not dep.get("time") or not arr.get("time"):
            return None
        segments.append({
            "airline": flight.get("airline") or "Unknown airline",
            "flight_number": flight.get("flight_number") or "",
            "aircraft": flight.get("airplane"),
            "airline_logo": flight.get("airline_logo"),
            "origin": dep.get("id"),
            "origin_name": dep.get("name"),
            "destination": arr.get("id"),
            "destination_name": arr.get("name"),
            "departure_time": dep["time"],
            "arrival_time": arr["time"],
            "duration_min": _as_int(flight.get("duration")),
        })
    first, last = segments[0], segments[-1]
    try:
        total_duration = int(offer.get("total_duration"))
    except (TypeError, ValueError):
        total_duration = sum(segment.get("duration_min") or 0 for segment in segments)
    price = offer.get("price")
    try:
        price = float(price) if price is not None else None
    except (TypeError, ValueError):
        price = None
    return {
        "origin": first["origin"],
        "destination": last["destination"],
        "departure_time": first["departure_time"],
        "arrival_time": last["arrival_time"],
        "duration_min": total_duration,
        "price_usd": price,
        "stops": len(segments) - 1,
        "segments": segments,
    }


def _as_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_flights(response: dict) -> list[dict]:
    offers = []
    for key in ("best_flights", "other_flights"):
        value = response.get(key)
        if isinstance(value, list):
            offers.extend(value)
    # Accept the alternate top-level shape as well, in case SerpApi changes
    # the Google Flights payload or returns a generic results list.
    if not offers and isinstance(response.get("flights"), list):
        offers = response["flights"]
    parsed = [_parse_offer(offer) for offer in offers]
    return [offer for offer in parsed if offer is not None]
