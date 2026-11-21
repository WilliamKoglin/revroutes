"""Curated U.S. commercial hubs used for destination autocomplete.

Airport choice is based on the largest hub serving the metro area, not nearest
airport distance. Add a city here when expanding supported destinations.
"""
from __future__ import annotations

from difflib import SequenceMatcher

MAJOR_AIRPORTS = [
    {"code": "ATL", "airport": "Hartsfield-Jackson Atlanta International", "city": "Atlanta", "state": "GA", "aliases": ["Atlanta"]},
    {"code": "AUS", "airport": "Austin-Bergstrom International", "city": "Austin", "state": "TX", "aliases": ["Austin"]},
    {"code": "BWI", "airport": "Baltimore/Washington International", "city": "Baltimore", "state": "MD", "aliases": ["Baltimore", "Washington"]},
    {"code": "BOS", "airport": "Logan International", "city": "Boston", "state": "MA", "aliases": ["Boston"]},
    {"code": "CLT", "airport": "Charlotte Douglas International", "city": "Charlotte", "state": "NC", "aliases": ["Charlotte"]},
    {"code": "ORD", "airport": "O'Hare International", "city": "Chicago", "state": "IL", "aliases": ["Chicago"]},
    {"code": "CVG", "airport": "Cincinnati/Northern Kentucky International", "city": "Cincinnati", "state": "OH", "aliases": ["Cincinnati"]},
    {"code": "CLE", "airport": "Cleveland Hopkins International", "city": "Cleveland", "state": "OH", "aliases": ["Cleveland"]},
    {"code": "CMH", "airport": "John Glenn Columbus International", "city": "Columbus", "state": "OH", "aliases": ["Columbus"]},
    {"code": "DFW", "airport": "Dallas/Fort Worth International", "city": "Dallas", "state": "TX", "aliases": ["Dallas", "Fort Worth"]},
    {"code": "DEN", "airport": "Denver International", "city": "Denver", "state": "CO", "aliases": ["Denver"]},
    {"code": "DTW", "airport": "Detroit Metropolitan Wayne County", "city": "Detroit", "state": "MI", "aliases": ["Detroit"]},
    {"code": "FLL", "airport": "Fort Lauderdale-Hollywood International", "city": "Fort Lauderdale", "state": "FL", "aliases": ["Fort Lauderdale"]},
    {"code": "HNL", "airport": "Daniel K. Inouye International", "city": "Honolulu", "state": "HI", "aliases": ["Honolulu"]},
    {"code": "IAH", "airport": "George Bush Intercontinental", "city": "Houston", "state": "TX", "aliases": ["Houston"]},
    {"code": "IND", "airport": "Indianapolis International", "city": "Indianapolis", "state": "IN", "aliases": ["Indianapolis"]},
    {"code": "JAX", "airport": "Jacksonville International", "city": "Jacksonville", "state": "FL", "aliases": ["Jacksonville"]},
    {"code": "MCI", "airport": "Kansas City International", "city": "Kansas City", "state": "MO", "aliases": ["Kansas City"]},
    {"code": "LAS", "airport": "Harry Reid International", "city": "Las Vegas", "state": "NV", "aliases": ["Las Vegas"]},
    {"code": "LAX", "airport": "Los Angeles International", "city": "Los Angeles", "state": "CA", "aliases": ["Los Angeles"]},
    {"code": "MEM", "airport": "Memphis International", "city": "Memphis", "state": "TN", "aliases": ["Memphis"]},
    {"code": "MIA", "airport": "Miami International", "city": "Miami", "state": "FL", "aliases": ["Miami"]},
    {"code": "MSP", "airport": "Minneapolis–Saint Paul International", "city": "Minneapolis", "state": "MN", "aliases": ["Minneapolis", "Saint Paul"]},
    {"code": "BNA", "airport": "Nashville International", "city": "Nashville", "state": "TN", "aliases": ["Nashville"]},
    {"code": "MSY", "airport": "Louis Armstrong New Orleans International", "city": "New Orleans", "state": "LA", "aliases": ["New Orleans"]},
    {"code": "JFK", "airport": "John F. Kennedy International", "city": "New York", "state": "NY", "aliases": ["New York", "New York City"]},
    {"code": "EWR", "airport": "Newark Liberty International", "city": "Newark", "state": "NJ", "aliases": ["Newark"]},
    {"code": "MCO", "airport": "Orlando International", "city": "Orlando", "state": "FL", "aliases": ["Orlando"]},
    {"code": "PHL", "airport": "Philadelphia International", "city": "Philadelphia", "state": "PA", "aliases": ["Philadelphia"]},
    {"code": "PHX", "airport": "Phoenix Sky Harbor International", "city": "Phoenix", "state": "AZ", "aliases": ["Phoenix"]},
    {"code": "PIT", "airport": "Pittsburgh International", "city": "Pittsburgh", "state": "PA", "aliases": ["Pittsburgh"]},
    {"code": "PDX", "airport": "Portland International", "city": "Portland", "state": "OR", "aliases": ["Portland"]},
    {"code": "RDU", "airport": "Raleigh-Durham International", "city": "Raleigh", "state": "NC", "aliases": ["Raleigh", "Durham"]},
    {"code": "SMF", "airport": "Sacramento International", "city": "Sacramento", "state": "CA", "aliases": ["Sacramento"]},
    {"code": "SLC", "airport": "Salt Lake City International", "city": "Salt Lake City", "state": "UT", "aliases": ["Salt Lake City"]},
    {"code": "SAT", "airport": "San Antonio International", "city": "San Antonio", "state": "TX", "aliases": ["San Antonio"]},
    {"code": "SAN", "airport": "San Diego International", "city": "San Diego", "state": "CA", "aliases": ["San Diego"]},
    {"code": "SFO", "airport": "San Francisco International", "city": "San Francisco", "state": "CA", "aliases": ["San Francisco"]},
    {"code": "SJC", "airport": "Norman Y. Mineta San Jose International", "city": "San Jose", "state": "CA", "aliases": ["San Jose"]},
    {"code": "SEA", "airport": "Seattle-Tacoma International", "city": "Seattle", "state": "WA", "aliases": ["Seattle"]},
    {"code": "TPA", "airport": "Tampa International", "city": "Tampa", "state": "FL", "aliases": ["Tampa"]},
    {"code": "IAD", "airport": "Washington Dulles International", "city": "Washington", "state": "DC", "aliases": ["Washington", "Washington DC"]},
]


def search_airports(query: str) -> list[dict]:
    query = " ".join(query.casefold().replace(",", " ").split())
    if len(query) < 2:
        return []
    matches: list[tuple[float, dict]] = []
    for airport in MAJOR_AIRPORTS:
        variants = [f"{airport['city']} {airport['state']}", airport["city"], airport["airport"], airport["code"], *airport["aliases"]]
        normalized = [" ".join(value.casefold().replace(",", " ").split()) for value in variants]
        score = max(SequenceMatcher(None, query, candidate).ratio() for candidate in normalized)
        if any(query in candidate for candidate in normalized):
            score = max(score, 0.82 + 0.17 * min(1, len(query) / max(map(len, normalized), default=1)))
        if all(token in " ".join(normalized) for token in query.split()):
            score = max(score, 0.92)
        if score >= 0.38:
            result = {key: airport[key] for key in ("code", "airport", "city", "state")}
            result["match_score"] = round(score, 3)
            matches.append((score, result))
    matches.sort(key=lambda item: (-item[0], item[1]["city"], item[1]["state"]))
    return [airport for _, airport in matches[:8]]
