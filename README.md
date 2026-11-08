# Aggie Routes

Aggie Routes compares trips from Easterwood in College Station (CLL), Houston Intercontinental (IAH), and Houston Hobby (HOU). Enter a home address and departure date; Google Maps geocoding through SerpApi maps it to the nearest IATA airport in `backend/airports.dat`. The app then returns three recommendations: one nonstop flight, the earliest estimated arrival, and the lowest-fare itinerary with up to two transfers. The included model predicts segment delays using `metadata.json`, `encoders.pkl`, and `model.pt`; each recommendation also shows a suggested time to leave College Station, using a 30-minute buffer for CLL or two hours for either Houston airport.

## Run locally

1. Set `SERP_API_KEY` in the root `.env` file.
2. Start PostgreSQL and the app with `docker compose up --build`.
3. Open [http://localhost:5173](http://localhost:5173). The API docs are at [http://localhost:8000/docs](http://localhost:8000/docs).

The React frontend is in `frontend/` and uses Vite. For frontend development, start the API and database, run `cd frontend && npm install && npm run dev`, and open [http://localhost:5173](http://localhost:5173). Vite proxies API requests to port 8000. The API also runs directly with `uvicorn app.main:app --app-dir backend --reload` after PostgreSQL is available and the Python dependencies are installed.

## Search cache

Flight searches are cached in PostgreSQL by origin, destination, and travel date, and reused without an automatic refresh. A single Google Flights request can query all uncached CLL, IAH, and HOU origins; returned offers and every segment are stored for later routing. Searches request nonstop flights and itineraries with up to two stops. Address-to-airport mappings are cached using a hash of the normalized address; the raw address is not saved in the app database. A new uncached comparison can use one address lookup plus one combined flight search. The address is sent to SerpApi/Google Maps to obtain coordinates. A connection shorter than the incoming flight's predicted P90 buffer is displayed as risky when Google Flights returns that scheduled itinerary.

`flight_search_cache` contains normalized offers for the frontend, and `flight_search_responses` contains the complete provider JSON for each ping. On each flight request, departed offers are removed from both tables using Central time. Results sent to the frontend must also depart beyond the origin’s 30-minute CLL or two-hour Houston buffer.

## Shared SerpApi allowance

The project has a shared allowance of 200 SerpApi pings per renewal period. Both Google Flights searches and Google Maps address lookups count toward it; cached results do not. A ping counts once SerpApi returns an HTTP response, including provider error responses, because the request has reached the provider. Network failures with no provider response do not count. The counter starts at 16 calls, renews on November 18, 2026, and renews on the 18th of each following month. The frontend displays current usage and the renewal date, while `GET /api/usage` provides the same information as JSON.

## API

- `POST /api/trip-plans` with `{"home_address":"...","travel_date":"2026-12-15"}`
- `GET /api/flights?origin=CLL&destination=DFW&date=2026-12-15` (single-route API)
- `GET /api/usage`
- `GET /health` and `GET /health/db`

`backend/ml/artifacts` contains the trained weights, categorical encoders, and metadata used for delay and recommended-buffer predictions. If an offer cannot be scored, flight search still succeeds and the delay estimate is omitted.
