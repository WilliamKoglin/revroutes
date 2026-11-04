"""Map geocoded coordinates to the closest IATA airport in airports.dat."""
import csv
import math
from functools import lru_cache
from pathlib import Path


AIRPORTS_FILE = Path(__file__).resolve().parents[2] / "airports.dat"


@lru_cache(maxsize=1)
def _airport_records() -> tuple[dict, ...]:
    records = []
    with AIRPORTS_FILE.open(encoding="utf-8", newline="") as source:
        for row in csv.reader(source):
            if len(row) < 14 or row[12].lower() != "airport":
                continue
            code = row[4].strip().upper()
            if len(code) != 3 or code == r"\N":
                continue
            try:
                latitude, longitude = float(row[6]), float(row[7])
            except (TypeError, ValueError):
                continue
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                continue
            records.append({
                "code": code,
                "name": row[1],
                "city": row[2],
                "country": row[3],
                "latitude": latitude,
                "longitude": longitude,
            })
    return tuple(records)


def nearest_airport(latitude: float, longitude: float) -> tuple[dict, float] | None:
    """Return closest listed IATA airport and straight-line distance in km."""
    if not _airport_records():
        return None
    lat1 = math.radians(latitude)
    lon1 = math.radians(longitude)
    best, best_distance = None, float("inf")
    for airport in _airport_records():
        lat2 = math.radians(airport["latitude"])
        lon2 = math.radians(airport["longitude"])
        dlat, dlon = lat2 - lat1, lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        a = min(1.0, max(0.0, a))
        distance = 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        if distance < best_distance:
            best, best_distance = airport, distance
    return (best, round(best_distance, 1)) if best else None
