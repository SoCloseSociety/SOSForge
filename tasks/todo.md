# SOSForge -- todo

Real-time tracker (live to the second) for earthquakes, tsunamis and other
disasters. SuiteForge product: FastAPI backend, React 19 + Vite + TS + Zustand
frontend. **In production at <https://sosforge.soclose.co>** (helper VPS).

## Principle

"Live to the second" does NOT come from hammering one API: it comes from a
fan-in of heterogeneous sources (one websocket push + fast polling), normalized
into a single feed, then a websocket fan-out to browsers.

## Status

| | |
|---|---|
| Sources | 19/19 online |
| Tests | 95 backend + 77 frontend |
| Types | mypy clean (26 files), tsc clean |
| Languages | 5 (en, fr, es, ja, id) |
| Deployment | helper VPS, docker compose behind host nginx, TLS |

## Done

- Normalized `Event` model shared by every source, with cross-source dedup
- In-memory ring buffer + JSONL journal (retention 7 days), no database in v1
- Websocket hub: snapshot on connect, deltas, one-second heartbeat
- 19 sources, each verified against its real API before being called done
- Ingestion horizon, `ongoing` flag, stale-alert sweep, future-timestamp rejection
- Map with severity-coded markers and live P/S wave fronts
- Time window, area search, deep links, remembered filters, five languages
- Country flags resolved server-side, never approximate
- Live views near an event (webcams, satellite imagery)
- SEO: English metadata, canonical, Open Graph, JSON-LD, robots, sitemap, and a
  `noscript` block that actually describes the product
- Deployed, TLS, security headers, HTTP to HTTPS redirect

## Backlog, verified but not wired

Endpoints proven to work, kept out on purpose for now:

- **JTWC** (western Pacific invests): text parsing, and the ash SIGMETs already
  cover named storms
- **Environment Canada CAP**: partly covered by the WMO aggregate; would be worth
  it via AMQP rather than by walking dated directories
- **NASA FIRMS**, **EFFIS**: about 3 h of NRT latency, no event identifier
- **GloFAS / Copernicus EMS**: API key required, GDACS republishes the essentials
- **BOM Australia**: works, but its own payload forbids reuse

## Next

- Browser notifications and a subscription to an area (an emergency product
  should be able to reach someone who does not have the tab open)
- Offline mode (PWA): networks are exactly what fails during a disaster
- Full keyboard navigation on the map
- Export (GeoJSON/CSV) of the current view
