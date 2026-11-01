"""Normalize SerpApi Google Flights offers and build model inputs per segment."""
from datetime import datetime


def _model_features(flight: dict, origin: str, destination: str) -> dict | None:
    departure = flight.get("departure_airport", {})
    flight_number = flight.get("flight_number", "")
    dep_time = departure.get("time")
    try:
        dep_dt = datetime.strptime(dep_time, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return None
    return {
        "Reporting_Airline": flight_number.split(" ")[0] if flight_number else "",
        "Origin": origin,
        "Dest": destination,
        "Month": dep_dt.month,
        "DayOfWeek": dep_dt.isoweekday(),
        "DayofMonth": dep_dt.day,
        "DepHour": dep_dt.hour,
    }


def _parse_offer(offer: dict) -> dict | None:
    flights = offer.get("flights") or []
    if not flights:
        return None
    segments = []
    for flight in flights:
        dep = flight.get("departure_airport", {})
        arr = flight.get("arrival_airport", {})
        features = _model_features(flight, dep.get("id"), arr.get("id"))
        if not features:
            return None
        number = flight.get("flight_number", "")
        segments.append({
            "carrier": features["Reporting_Airline"],
            "airline": flight.get("airline") or features["Reporting_Airline"],
            "flight_number": number,
            "aircraft": flight.get("airplane"),
            "airline_logo": flight.get("airline_logo"),
            "origin": dep.get("id"),
            "origin_name": dep.get("name"),
            "destination": arr.get("id"),
            "destination_name": arr.get("name"),
            "departure_time": dep.get("time"),
            "arrival_time": arr.get("time"),
            "duration_min": flight.get("duration"),
            "model_features": features,
        })

    first, last = segments[0], segments[-1]
    try:
        scheduled_arrival = datetime.strptime(last["arrival_time"], "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return None
    return {
        "origin": first["origin"],
        "destination": last["destination"],
        "dest": last["destination"],
        "carrier": first["carrier"],
        "flight_number": first["flight_number"],
        "airline": first["airline"],
        "aircraft": first["aircraft"],
        "model_features": first["model_features"],
        "departure_time": first["departure_time"],
        "arrival_time": last["arrival_time"],
        "estimated_arrival_time": scheduled_arrival.strftime("%Y-%m-%d %H:%M"),
        "duration_min": offer.get("total_duration"),
        "price_usd": offer.get("price"),
        "stops": len(segments) - 1,
        "segments": segments,
        "delay_prediction": None,
    }


def parse_flights(response: dict) -> list[dict]:
    """Parse direct and connecting offers, up to two transfers."""
    offers = response.get("best_flights", []) + response.get("other_flights", [])
    parsed = [_parse_offer(offer) for offer in offers]
    return [offer for offer in parsed if offer is not None]
