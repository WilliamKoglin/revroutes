import { useState } from 'react';

const PLANS = [
  { code: 'CLL', name: 'Easterwood Airport', city: 'College Station', buffer: 30, bufferLabel: '30 min airport buffer' },
  { code: 'IAH', name: 'George Bush Intercontinental', city: 'Houston', buffer: 120, bufferLabel: '2 hr Houston buffer' },
  { code: 'HOU', name: 'William P. Hobby Airport', city: 'Houston', buffer: 120, bufferLabel: '2 hr Houston buffer' },
];

function localDateString(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function airportDateTime(value) {
  // Provider times are airport-local wall-clock values without an offset.
  return new Date(`${value.replace(' ', 'T')}:00Z`);
}

function clock(date) {
  const hour = date.getUTCHours();
  return `${hour % 12 || 12}:${String(date.getUTCMinutes()).padStart(2, '0')} ${hour < 12 ? 'AM' : 'PM'}`;
}

async function getTripPlans(homeAddress, travelDate) {
  const response = await fetch('/api/trip-plans', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ home_address: homeAddress, travel_date: travelDate }),
  });
  let data;
  try { data = await response.json(); }
  catch { throw new Error(`Trip planner returned a non-JSON response (HTTP ${response.status}).`); }
  if (!response.ok) throw new Error(data.detail || 'Could not build trip plans.');
  if (!data.destination_airport?.code || !Array.isArray(data.routes)) {
    throw new Error('Trip planner response did not include an airport mapping and route list.');
  }
  return data;
}

function PlanCard({ recommendation, index, errors, destination }) {
  const { kind, title, subtitle, flight, note } = recommendation;
  return (
    <article className="plan-card">
      <div className="plan-head">
        <span className="plan-number">PLAN {String(index + 1).padStart(2, '0')}</span>
        <h3>{title}</h3>
        <p>{subtitle}</p>
        <span className="buffer-pill">{kind === 'direct' ? 'One flight · no transfers' : kind === 'fastest' ? 'Ranked by estimated arrival' : 'Connecting · up to 2 transfers'}</span>
      </div>
      <div className="plan-flights">
        {flight ? <Itinerary flight={flight} note={note} /> : <div className="plan-message">{kind === 'direct' ? `No single-flight option to ${destination} was found for this date.` : kind === 'fastest' ? `No flight options to ${destination} were found for this date.` : `No itinerary with one or two transfers to ${destination} was found for this date.`}</div>}
        {errors.length > 0 && <div className="cache-note">Some airport searches could not be completed: {errors.join(', ')}</div>}
      </div>
    </article>
  );
}

function Itinerary({ flight, note }) {
  const originPlan = PLANS.find(plan => plan.code === flight.origin);
  const firstDeparture = airportDateTime(flight.departure_time);
  const estimatedArrival = airportDateTime(flight.estimated_arrival_time || flight.arrival_time);
  const leaveForAirport = new Date(firstDeparture.getTime() - (originPlan?.buffer ?? 30) * 60_000);
  return (
    <article className="itinerary-card">
      <div className="itinerary-summary">
        <div><span className="itinerary-caption">DEPART FROM</span><strong>{flight.origin}</strong><small>{originPlan?.city || flight.origin}</small></div>
        <div className="itinerary-price">${flight.price_usd ?? '—'}</div>
      </div>
      <div className="itinerary-legs">
        {flight.segments.map((segment, index) => (
          <div className="itinerary-leg" key={`${segment.flight_number}-${index}`}>
            <div className="leg-time"><strong>{clock(airportDateTime(segment.departure_time))}</strong><span>{segment.origin}</span></div>
            <div className="leg-track"><i />{index < flight.segments.length - 1 && <small>CONNECT</small>}</div>
            <div className="leg-time"><strong>{clock(airportDateTime(segment.arrival_time))}</strong><span>{segment.destination}</span></div>
            <div className="leg-airline">{segment.airline} · {segment.flight_number}</div>
          </div>
        ))}
      </div>
      {(flight.connections || []).filter(connection => connection.risky).map((connection, index) => (
        <div className="connection-risk" key={`${connection.airport}-${index}`} role="status">
          <span className="risk-icon" aria-hidden="true">⚠</span>
          <div><strong>RISKY CONNECTION</strong>
            <p>{connection.layover_minutes} min at {connection.airport}, below the model’s P90 buffer of {connection.p90_buffer_minutes} min. Google Flights shows the scheduled connection as possible, but our P90 buffer model flags it as risky.</p>
          </div>
        </div>
      ))}
      <div className="itinerary-meta">
        <span>{flight.stops === 0 ? 'Nonstop' : `${flight.stops} transfer${flight.stops === 1 ? '' : 's'}`}</span>
        <span>{flight.duration_min} min total</span>
      </div>
      <div className="leave-time">
        <span>Leave College Station by</span>
        <strong>{clock(leaveForAirport)}</strong>
        <small>{originPlan?.bufferLabel ?? '30 min airport buffer'} before departure</small>
      </div>
      <div className="arrival-estimate">
        <span>{flight.delay_prediction ? 'Estimated arrival' : 'Scheduled arrival'}</span>
        <strong>{clock(estimatedArrival)}</strong>
      </div>
      {flight.delay_prediction && <div className="prediction">
        ◷ Estimated delay <strong>{flight.delay_prediction.predicted_delay_min} min</strong> · recommended delay buffer <strong>{flight.delay_prediction.recommended_buffer_min} min</strong>
      </div>}
      {!flight.delay_prediction && <div className="cache-note">Delay prediction unavailable for this itinerary.</div>}
      {note && <div className="cache-note">{note}</div>}
      {flight.cached && <div className="cache-note">⚡ Saved flight data · {new Date(flight.cached_at).toLocaleString()}</div>}
    </article>
  );
}

function priceOf(flight) {
  const price = Number(flight.price_usd);
  return Number.isFinite(price) ? price : Number.POSITIVE_INFINITY;
}

function estimateOf(flight) {
  return airportDateTime(flight.estimated_arrival_time || flight.arrival_time).getTime();
}

function recommend(searchResults) {
  const flights = searchResults.flatMap(result => result.value
    ? result.value.flights.map(flight => ({ ...flight, cached: result.value.cached, cached_at: result.value.cached_at }))
    : []);
  const direct = flights.filter(flight => flight.stops === 0).sort((a, b) => priceOf(a) - priceOf(b) || estimateOf(a) - estimateOf(b))[0] ?? null;
  const earliest = [...flights].sort((a, b) => estimateOf(a) - estimateOf(b) || priceOf(a) - priceOf(b))[0] ?? null;
  const connecting = flights.filter(flight => flight.stops > 0 && flight.stops <= 2)
    .sort((a, b) => priceOf(a) - priceOf(b) || a.stops - b.stops || estimateOf(a) - estimateOf(b))[0] ?? null;
  const bounded = connecting ?? (flights.filter(flight => flight.stops <= 2)
    .sort((a, b) => priceOf(a) - priceOf(b) || a.stops - b.stops || estimateOf(a) - estimateOf(b))[0] ?? null);
  return [
    { kind: 'direct', title: 'One flight total', subtitle: 'Lowest fare among nonstop options', flight: direct },
    { kind: 'fastest', title: 'Earliest estimated arrival', subtitle: earliest?.delay_prediction ? 'Scheduled arrival plus model-predicted segment delays' : 'Earliest scheduled arrival; delay prediction unavailable', flight: earliest },
    { kind: 'connections', title: 'Lowest fare · up to 2 transfers', subtitle: connecting ? 'Cheapest connecting itinerary found' : 'No connecting itinerary found; showing the cheapest eligible flight', flight: bounded },
  ];
}

function RouteIllustration() {
  return (
    <div className="route-art" aria-hidden="true">
      <div className="sun" />
      <svg viewBox="0 0 520 235" fill="none">
        <path d="M26 178C117 118 174 207 264 137S411 42 491 77" stroke="white" strokeOpacity=".64" strokeWidth="2" strokeDasharray="6 8" />
        <circle cx="26" cy="178" r="7" fill="#fff" /><circle cx="264" cy="137" r="7" fill="#fff" /><circle cx="491" cy="77" r="7" fill="#fff" />
        <path d="m235 131 15-11 19 5-13 8 1 13-5 3-5-13-12-1Z" fill="white" />
      </svg>
      <div className="airport-label a">CLL <small>COLLEGE STATION</small></div>
      <div className="airport-label b">HOU <small>HOUSTON</small></div>
      <div className="airport-label c">HOME <small>YOUR NEXT STOP</small></div>
    </div>
  );
}

export default function App() {
  const today = localDateString(new Date());
  const [homeAddress, setHomeAddress] = useState('');
  const [destinationAirport, setDestinationAirport] = useState(null);
  const [travelDate, setTravelDate] = useState(today);
  const [status, setStatus] = useState('');
  const [isError, setIsError] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const [results, setResults] = useState(null);
  const [searchErrors, setSearchErrors] = useState([]);

  async function handleSubmit(event) {
    event.preventDefault();
    setResults(null);
    setDestinationAirport(null);
    setSearchErrors([]);
    setIsError(false);
    setStatus('Mapping your address to the nearest airport, then finding flight plans…');
    setIsSearching(true);
    try {
      const data = await getTripPlans(homeAddress.trim(), travelDate);
      const destinationCode = data.destination_airport.code;
      setDestinationAirport(data.destination_airport);
      const plansResults = data.routes.map(route => route.error
        ? { error: new Error(`${route.origin}: ${route.error}`) }
        : { value: route });
      setResults(recommend(plansResults));
      const failures = plansResults.filter(item => item.error).length;
      setSearchErrors(plansResults.flatMap(item => item.error ? [item.error.message] : []));
      setStatus(failures
        ? `${3 - failures} of 3 plans loaded. ${failures} search${failures === 1 ? '' : 'es'} could not be completed.`
        : `Your address mapped to ${destinationCode} · ${data.destination_airport.name}, ${data.destination_airport.city}. Plans for ${new Date(`${travelDate}T12:00:00`).toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' })}.`);
    } catch (error) {
      setIsError(true);
      setStatus(error.message);
    } finally {
      setIsSearching(false);
    }
  }

  return (
    <>
      <header className="topbar">
        <a className="brand" href="/"><span className="brand-mark">A</span><span>Aggie Routes<small>GET HOME, THE SMART WAY</small></span></a>
        <div className="top-note"><span className="pulse" /> BUILT FOR AGGIES</div>
      </header>
      <main>
        <section className="hero">
          <div className="hero-copy">
            <p className="eyebrow">YOUR JOURNEY STARTS HERE</p>
            <h1>Three ways home<br />from <em>Aggie land.</em></h1>
            <p className="lede">Compare flights from College Station and both Houston airports. We’ll account for the time you need to get to each airport.</p>
          </div>
          <RouteIllustration />
        </section>
        <section className="search-card">
          <div className="card-heading">
            <div><p className="eyebrow">FLIGHT FINDER</p><h2>Plan your trip home</h2></div>
            <span className="direct-tag">✦ LIVE FLIGHT OPTIONS</span>
          </div>
          <form id="search-form" onSubmit={handleSubmit}>
            <div className="fields">
              <label className="field destination-field">
                <span>HOME ADDRESS / WHERE YOU'RE GOING</span>
                <div className="airport-input"><b>⌂</b><input value={homeAddress} onChange={event => setHomeAddress(event.target.value)} placeholder="Street, city, state, ZIP" required minLength="8" maxLength="300" autoComplete="street-address" /></div>
              </label>
              <label className="field date-field">
                <span>DEPARTURE DATE</span>
                <div className="airport-input"><b>▦</b><input type="date" value={travelDate} min={today} onChange={event => setTravelDate(event.target.value)} required /></div>
              </label>
              <button className="search-button" type="submit" disabled={isSearching}>
                <span>{isSearching ? 'Finding flights…' : 'Compare 3 plans'}</span><b>↗</b>
              </button>
            </div>
          </form>
          <p className="route-hint">We map your address to the closest airport in our airport directory, then compare CLL, IAH, and HOU. Your address is sent to SerpApi/Google Maps for lookup; Aggie Routes stores only the selected airport mapping. Airport arrival buffers: 30 minutes at CLL and 2 hours in Houston.</p>
        </section>
        {status && <section className={`status${isError ? ' error' : ''}`} aria-live="polite">{status}</section>}
        {destinationAirport && <section className="mapped-airport">Nearest listed airport: <strong>{destinationAirport.code}</strong> · {destinationAirport.name}, {destinationAirport.city} ({destinationAirport.distance_km} km straight-line distance)</section>}
        {results && <section className="results" aria-live="polite">
          {results.map((recommendation, index) => <PlanCard key={recommendation.kind} recommendation={recommendation} index={index} errors={searchErrors} destination={destinationAirport?.code || ''} />)}
        </section>}
        <section className="how">
          <div><p className="eyebrow">A BETTER WAY HOME</p><h2>Less guessing.<br /><em>More home time.</em></h2></div>
          <div className="benefit"><span>01</span><h3>Three airport choices</h3><p>See the best options from Easterwood and both Houston airports.</p></div>
          <div className="benefit"><span>02</span><h3>Time to get there</h3><p>Each plan includes the airport arrival buffer you asked for.</p></div>
          <div className="benefit"><span>03</span><h3>Delay aware</h3><p>Use the model’s predicted delay and recommended buffer to compare flights.</p></div>
        </section>
        <footer><span>AGGIE ROUTES · COLLEGE STATION, TEXAS</span></footer>
      </main>
    </>
  );
}
