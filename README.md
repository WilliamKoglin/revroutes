# Aggie Routes

Aggie Routes searches flights from Easterwood Airport in College Station (CLL) to major U.S. commercial hubs. Choose a destination city and state, the earliest time you can depart from CLL, and the latest time you want to arrive (in the destination's local time). Results include itineraries with up to two connections and highlight the cheapest and quickest options.

Destination suggestions use the curated major-hub list in `backend/app/services/major_airports.py`. The city and state field ranks close matches and fills in a clear best match when the field loses focus or the search is submitted; ambiguous matches remain in the suggestion list for the user to choose. Selecting a city routes to its listed hub, not a nearby regional airport. You can enter a different three-letter IATA code if needed; airport codes appear on airline tickets and airport websites.

## Run locally

1. Set `SERP_API_KEY` in the root `.env` file.
2. Start PostgreSQL and the app with `docker compose up --build`.
3. Open [http://localhost:5173](http://localhost:5173). API docs are at [http://localhost:8000/docs](http://localhost:8000/docs).

The React frontend is in `frontend/` and uses Vite. For frontend development, start the API and database, run `cd frontend && npm install && npm run dev`, and open [http://localhost:5173](http://localhost:5173). The API also runs directly with `uvicorn app.main:app --app-dir backend --reload` after PostgreSQL is available and the Python dependencies are installed.

## Search cache and SerpApi usage

Successful flight-search results are cached in PostgreSQL by destination and departure date. The cached result set covers the whole departure day, so changing the earliest departure or arrival deadline re-filters saved itineraries without another SerpApi search. Searches request flights with up to two stops (three flight legs total).

The app's shared monthly counter increments only after SerpApi returns a valid successful response. It is shared across SerpApi engines: Google Flights and Google Maps searches both count when used by the app. Provider errors and failed requests release their reserved slot without incrementing the counter. The usage banner reads actual monthly searches used, remaining, and renewal date from SerpApi's free Account API when available; the app's own 200-search guard remains as an additional project limit. `GET /api/usage` returns both sources.

## API

- `GET /api/airports?q=hou` — major-hub destination suggestions
- `POST /api/flights/search` with `{"destination":"IAH","earliest_departure":"2026-12-15T08:00","arrive_by":"2026-12-15T21:00"}`
- `GET /api/usage`
- `GET /health` and `GET /health/db`
