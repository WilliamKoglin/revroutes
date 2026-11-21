import { useEffect, useState } from 'react';

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');

function localDateTime(date) {
  const adjusted = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return adjusted.toISOString().slice(0, 16);
}

function formatDateTime(value) {
  if (!value) return '—';
  const date = new Date(`${value.replace(' ', 'T')}:00Z`);
  return new Intl.DateTimeFormat(undefined, { timeZone: 'UTC', weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(date);
}

function formatTime(value) {
  if (!value) return '—';
  const date = new Date(`${value.replace(' ', 'T')}:00Z`);
  return new Intl.DateTimeFormat(undefined, { timeZone: 'UTC', hour: 'numeric', minute: '2-digit' }).format(date);
}

async function jsonRequest(url, options) {
  const response = await fetch(`${API_BASE_URL}${url}`, options);
  let data;
  try { data = await response.json(); }
  catch { throw new Error(`The server returned an invalid response (HTTP ${response.status}).`); }
  if (!response.ok) throw new Error(data.detail || 'The request failed.');
  return data;
}

function Itinerary({ flight, label }) {
  return <article className="route-result">
    <div className="result-heading"><span className="result-label">{label}</span><strong>{flight.price_usd == null ? 'Fare unavailable' : `$${Math.round(flight.price_usd)}`}</strong></div>
    <div className="result-path">
      <div><strong>{formatTime(flight.departure_time)}</strong><small>CLL</small></div>
      <div className="path-line"><span>{flight.stops === 0 ? 'Nonstop' : `${flight.stops} connection${flight.stops === 1 ? '' : 's'}`}</span></div>
      <div className="destination-time"><strong>{formatTime(flight.arrival_time)}</strong><small>{flight.destination}</small></div>
    </div>
    <p className="result-detail">{flight.segments.map(segment => `${segment.airline} ${segment.flight_number}`.trim()).join(' · ')} · {Math.floor(flight.duration_min / 60)}h {flight.duration_min % 60}m</p>
    <div className="result-legs">{flight.segments.map((segment, index) => <div className="result-leg" key={`${segment.flight_number}-${index}`}>
      <span>{index + 1}. {segment.origin} → {segment.destination}</span>
      <span>{formatDateTime(segment.departure_time)} – {formatDateTime(segment.arrival_time)}</span>
      <small>{segment.airline} {segment.flight_number}</small>
    </div>)}</div>
  </article>;
}

export default function App() {
  const now = new Date();
  const earliestDefault = localDateTime(new Date(now.getTime() + 24 * 60 * 60_000));
  const arrivalDefault = localDateTime(new Date(now.getTime() + 48 * 60 * 60_000));
  const [destinationText, setDestinationText] = useState('');
  const [selectedAirport, setSelectedAirport] = useState(null);
  const [airportCode, setAirportCode] = useState('');
  const [suggestions, setSuggestions] = useState([]);
  const [earliestDeparture, setEarliestDeparture] = useState(earliestDefault);
  const [arriveBy, setArriveBy] = useState(arrivalDefault);
  const [usage, setUsage] = useState(null);
  const [results, setResults] = useState(null);
  const [status, setStatus] = useState('');
  const [error, setError] = useState(false);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    jsonRequest('/api/usage').then(setUsage).catch(() => setUsage(null));
  }, []);

  useEffect(() => {
    if (airportCode.trim() || selectedAirport || destinationText.trim().length < 2) { setSuggestions([]); return undefined; }
    const timer = setTimeout(() => {
      jsonRequest(`/api/airports?q=${encodeURIComponent(destinationText.trim())}`)
        .then(data => setSuggestions(data.airports || []))
        .catch(() => setSuggestions([]));
    }, 180);
    return () => clearTimeout(timer);
  }, [destinationText, airportCode, selectedAirport]);

  function chooseAirport(airport) {
    setSelectedAirport(airport);
    setDestinationText(`${airport.city}, ${airport.state}`);
    setSuggestions([]);
  }

  async function resolveDestination() {
    const code = airportCode.trim().toUpperCase();
    if (code) return /^[A-Z]{3}$/.test(code) ? { code, city: 'Custom airport', state: '', airport: 'IATA code override' } : null;
    if (selectedAirport) return selectedAirport;
    const query = destinationText.trim();
    if (query.length < 2) return null;
    const data = await jsonRequest(`/api/airports?q=${encodeURIComponent(query)}`);
    const matches = data.airports || [];
    setSuggestions(matches);
    const [best, next] = matches;
    const clearBestMatch = best?.match_score >= 0.78
      && (!next || best.match_score - next.match_score >= 0.08);
    if (!clearBestMatch) return null;
    chooseAirport(best);
    return best;
  }

  async function refreshUsage() {
    try { setUsage(await jsonRequest('/api/usage')); } catch { /* Keep the last known usage visible. */ }
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError(false);
    setResults(null);
    setSearching(true);
    setStatus('Matching your destination…');
    try {
      const destination = await resolveDestination();
      if (!destination) throw new Error('Choose a suggested city and state, or enter a valid three-letter IATA code.');
      setStatus('Searching flights from College Station…');
      const data = await jsonRequest('/api/flights/search', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ destination: destination.code, earliest_departure: earliestDeparture, arrive_by: arriveBy }),
      });
      setResults(data);
      setStatus(data.results.length
        ? `${data.results.length} itinerary${data.results.length === 1 ? '' : 's'} match your time window${data.cached ? ' · served from cache' : ''}.`
        : 'No itineraries matched that departure and arrival window. Try a later arrival deadline.');
    } catch (requestError) {
      setError(true);
      setStatus(requestError.message);
    } finally {
      setSearching(false);
      refreshUsage();
    }
  }

  const shownResults = results?.results || [];
  const appSearchesRemaining = usage
    ? Math.min(usage.calls_remaining, usage.provider?.searches_remaining ?? usage.calls_remaining)
    : null;
  const sameFeatured = results?.cheapest && results?.quickest
    && results.cheapest.departure_time === results.quickest.departure_time
    && results.cheapest.arrival_time === results.quickest.arrival_time
    && results.cheapest.price_usd === results.quickest.price_usd;
  return <>
    <header className="topbar"><a className="brand" href="/"><span className="brand-mark">A</span><span>Aggie Routes<small>FLIGHTS FROM AGGIELAND</small></span></a><div className="top-note"><span className="pulse" /> DEPARTING FROM CLL</div></header>
    <main className="new-app">
      <section className="hero new-hero"><div className="hero-copy"><p className="eyebrow">START IN COLLEGE STATION</p><h1>Find your way<br />to <em>what’s next.</em></h1><p className="lede">Compare fares and arrival times from Easterwood Airport to the country’s major airport hubs.</p></div><div className="hero-stamp"><span>CLL</span><i>→</i><span>YOUR NEXT STOP</span></div></section>
      <section className="search-card new-search">
        <div className="card-heading"><div><p className="eyebrow">FLIGHT FINDER</p><h2>Where are you headed?</h2></div><span className="direct-tag">UP TO 3 FLIGHT LEGS</span></div>
        <form onSubmit={handleSubmit}>
          <div className="new-fields">
            <label className="field destination-field"><span>DESTINATION CITY AND STATE</span><div className="airport-input"><b>⌖</b><input value={destinationText} onChange={event => { setDestinationText(event.target.value); setSelectedAirport(null); setAirportCode(''); }} onBlur={() => { resolveDestination().catch(() => {}); }} placeholder="e.g. Houston, TX" autoComplete="off" required /></div>
              {suggestions.length > 0 && <div className="airport-suggestions" role="listbox">{suggestions.map(airport => <button type="button" role="option" key={airport.code} onMouseDown={event => event.preventDefault()} onClick={() => chooseAirport(airport)}><span><strong>{airport.city}, {airport.state}</strong><small>{airport.airport}</small></span><b>{airport.code}</b></button>)}</div>}
            </label>
            <label className="field"><span>EARLIEST CLL DEPARTURE · CENTRAL TIME</span><div className="airport-input"><b>▦</b><input type="datetime-local" value={earliestDeparture} min={localDateTime(now).slice(0, 16)} onChange={event => setEarliestDeparture(event.target.value)} required /></div></label>
            <label className="field"><span>ARRIVE BY · DESTINATION LOCAL TIME</span><div className="airport-input"><b>◷</b><input type="datetime-local" value={arriveBy} min={earliestDeparture} onChange={event => setArriveBy(event.target.value)} required /></div></label>
            <button className="search-button" type="submit" disabled={searching}><span>{searching ? 'Searching…' : 'Find flights'}</span><b>↗</b></button>
          </div>
        </form>
        <div className="airport-override"><label htmlFor="airport-code">Use a different airport code (optional)</label><input id="airport-code" value={airportCode} onChange={event => setAirportCode(event.target.value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 3))} placeholder="IATA, e.g. AUS" maxLength={3} /><p>Find the three-letter IATA code on an airline ticket or an airport’s website. City suggestions choose a major hub; small regional airports are left out.</p></div>
        <p className="route-hint">We search from College Station’s Easterwood Airport (CLL), allow up to two connections, and show flights that meet your exact time window. Flight times are local to each airport.</p>
      </section>
      <section className="usage-banner" aria-live="polite"><strong>{appSearchesRemaining != null ? `${appSearchesRemaining.toLocaleString()} of 200 app searches remaining` : '200 app searches per month'}</strong><span>{usage ? `${usage.calls_used} of ${usage.calls_limit} app searches used${usage.provider?.renews_on ? ` · renews ${usage.provider.renews_on}` : ''}` : 'App usage status loading…'}</span><p>{usage?.provider ? `SerpApi account balance: ${usage.provider.searches_remaining?.toLocaleString() ?? 'unavailable'} searches remaining. The app caps its own searches at 200; cached results do not use a search.` : 'The app caps successful uncached SerpApi searches at 200 per monthly cycle. Cached results do not use a search.'}</p></section>
      {status && <section className={`status${error ? ' error' : ''}`} aria-live="polite">{status}</section>}
      {results && <section className="flight-results"><div className="results-title"><div><p className="eyebrow">{results.origin} → {results.destination}</p><h2>Matching flights</h2></div><span>{shownResults.length} itineraries</span></div>
        <div className="featured-results">{results.cheapest && sameFeatured ? <Itinerary flight={results.cheapest} label="CHEAPEST · QUICKEST" /> : <>{results.cheapest && <Itinerary flight={results.cheapest} label="CHEAPEST" />}{results.quickest && <Itinerary flight={results.quickest} label="QUICKEST" />}</>}</div>
        {shownResults.length > 0 && <><h3 className="all-results-title">All matching itineraries</h3><div className="all-results">{shownResults.map((flight, index) => <Itinerary key={`${flight.departure_time}-${flight.arrival_time}-${index}`} flight={flight} label={`OPTION ${String(index + 1).padStart(2, '0')}`} />)}</div></>}
      </section>}
      <footer><span>AGGIE ROUTES · COLLEGE STATION, TEXAS</span></footer>
    </main>
  </>;
}
