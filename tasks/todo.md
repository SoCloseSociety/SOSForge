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

---

# Audit 4 -- remediation plan

## Read first: the audit does not describe this repository

The master prompt lists a batch of fixes as "already done and committed". **None
of them are in this repository.** Verified, not assumed:

| Claimed done | Actual state here |
|---|---|
| `/readyz` in `main.py` | absent (0 occurrences) |
| `React.lazy` on MapView, entry chunk 235 kB | absent -- entry chunk is **1277 kB** |
| `ErrorBoundary` in `main.tsx` | absent |
| Coordinate/datetime validators in `models/event.py` | absent |
| `.dockerignore` | absent |
| `prefers-reduced-motion` block | absent |
| `npm ci` with lockfile in the Dockerfile | absent |
| dedupe early-break removal, ring replay try/finally, USGS cursor, nginx headers | absent |

Conversely, two items the audit reports as MISSING are already shipped here: the
PWA manifest and service worker (its 5.7) and the location-watch UI with a
remove button (its 4.9).

**Conclusion.** The audit was run against a different working copy -- most likely
another session's local tree that was never pushed. Its findings are still worth
treating as real: they were reproduced somewhere, and most of them are visible in
this code too. But "do not redo" cannot be followed literally, or fifteen real
defects would be left in place.

**So the plan adds a Phase 0** covering the audit-4 items that genuinely are
absent here, and re-verifies each remaining item against this tree before fixing
it. Anything that turns out not to apply gets written down as such rather than
silently skipped.

## Phase 0 -- the "already done" that is not done

Ordered by what breaks without it.

- [ ] `models/event.py`: validators forcing aware datetimes and bounding
      coordinates. Lesson 15 says a wrong position is worse than none, and today
      only `parse_iso6709` bounds anything -- every other source can inject an
      out-of-globe point.
- [ ] `main.py`: real `/readyz` reporting sources up, 404 on unknown events,
      `kind`/`min_magnitude` validated, CORS wildcard fallback removed.
- [ ] `frontend/src/main.tsx`: `ErrorBoundary`. One render error currently blanks
      the whole product, which is exactly the failure mode lesson 1 and 3 are
      about.
- [ ] `App.tsx`: MapView behind `React.lazy`. The entry chunk is 1277 kB on a
      product whose promise is to come up fast under a bad network.
- [ ] `dedupe.py`: remove the early `break` that assumes the deque is sorted by
      event time when it is sorted by arrival. **Measure the before/after on the
      live feed** -- this is a dedup change, rule 5 applies.
- [ ] `usgs.py`: 2-element GeoJSON positions, cursor advanced after the batch,
      per-feature isolation.
- [ ] `.dockerignore`, `npm ci` with the lockfile, backend deps from
      `pyproject.toml`.
- [ ] `styles.css`: `prefers-reduced-motion`, at the END of the sheet (lesson:
      a media query before the rules it overrides does nothing).

## Phase 1 -- the store lies to open tabs

This is the phase the prompt asks me to plan before touching `ring.py`. All six
items share one root cause: **the websocket protocol can add and update, but it
cannot remove.** `tick`, `snapshot`, `event`, `update` -- and nothing else. Every
defect below follows from that hole, so the first change is the protocol, and the
rest hangs off it.

### 1.1 A swept event stays on screen forever -- FIRST, everything depends on it

`prune_stale` returns what it removed; `sweep_stale` only logs it. The client has
no removal branch. A dissipated cyclone stays on the map of every open tab while
`/api/events` no longer returns it. On a product built to be left open for hours,
this is the common case, not an edge case.

- add `{"type": "purge", "ids": [...]}` to the protocol (`types.ts` +
  `hub.broadcast` from the sweep);
- `store.ts`: remove from `events`, from `fresh`, and clear `selected` if it
  pointed at a purged event;
- test first: a purge removes exactly its ids and touches nothing else.

### 1.2 A promoted cluster representative never reaches the browser

`_gc` promotes a survivor silently, and the client drops anything with
`primary === false`, so it never had the secondary and will never get the
promotion. The quake vanishes from that tab.

Broadcast the promotion as an `update` carrying `primary: true`. Test: evict a
primary, assert the survivor is broadcast and the client keeps it.

### 1.3 An ongoing alert that no normalizer marks ongoing

`pipeline.py` computes `ongoing` locally and never writes it; `prune_stale`
filters on the persisted field. An NWS or tsunami alert therefore escapes both
the horizon and the sweep.

One shared `is_ongoing(event)` predicate used by both, `ongoing=True` set in
`nws.parse_feature` (the endpoint is `/alerts/active`: true by construction) and
in `tsunami.parse_entry` when the bulletin is an actual alert. Test both paths.

### 1.4 A cancelled EEW does not disappear

`eew.py` returns `[]` on cancellation, which only stops re-emission. Needs the
explicit removal path from 1.1.

### 1.5 The replay bypasses the pipeline

`load_backlog` calls `store.upsert` directly, so a restart restores events that
`min_magnitude` excludes and never re-seeds `Deduper._recent`. A restart in the
5-15 minute window between the EMSC push and the USGS solution produces a
duplicate. Route the replay through `pipeline.emit` with the quiet flag, and use
`settings.max_event_age_days` instead of the hard-coded 24 h.

### 1.6 One bad event kills a whole poll cycle

`pipeline.emit` has no per-event isolation: one exception loses the rest of the
batch and marks the source failed. Wrap the body, count rejects, expose the
counter in `/api/stats`.

### 1.7 `_gc` is O(n) on every insertion once the ring is full

Steady state: each upsert rebuilds a 5000-id set inside the loop that owes a tick
every second. Remember the evicted element before the append and drop only its
key.

### 1.8 Journal hygiene

`raw` (the full upstream payload) is written on every line, rotation is by day
with no size ceiling, on a disk shared with the rest of the suite. Exclude `raw`,
add a size ceiling, count restored events rather than lines.

### Order of work

1.1 protocol and client removal -> 1.2 promotion -> 1.4 (uses 1.1) -> 1.3
predicate -> 1.5 replay -> 1.6 isolation -> 1.7 `_gc` -> 1.8 journal.

Each item: write the failing test first, then fix. One commit for the phase, with
the dedup before/after numbers where rule 5 applies.

## Phases 2 to 7

Kept as written in the master prompt, to be re-verified item by item against this
tree before being fixed -- several were written against code this repository does
not have.
