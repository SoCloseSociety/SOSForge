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

- [x] `models/event.py`: validators forcing aware datetimes and bounding
      coordinates. Lesson 15 says a wrong position is worse than none, and today
      only `parse_iso6709` bounds anything -- every other source can inject an
      out-of-globe point.
- [x] `main.py`: real `/readyz` reporting sources up, 404 on unknown events,
      `kind`/`min_magnitude` validated, CORS wildcard fallback removed.
- [x] `frontend/src/main.tsx`: `ErrorBoundary`. One render error currently blanks
      the whole product, which is exactly the failure mode lesson 1 and 3 are
      about.
- [x] `App.tsx`: MapView behind `React.lazy`. The entry chunk is 1277 kB on a
      product whose promise is to come up fast under a bad network.
      **Measured: entry chunk 1277 kB -> 235 kB**, MapLibre in its own 1038 kB
      chunk fetched only once the map mounts.
- [x] `dedupe.py`: remove the early `break` that assumes the deque is sorted by
      event time when it is sorted by arrival. **Measure the before/after on the
      live feed** -- this is a dedup change, rule 5 applies.
      **Measured on 1504 live events: 16 -> 53 multi-source clusters.** The
      early `break` was silently discarding 70% of the cross-source matches.
- [x] `usgs.py`: 2-element GeoJSON positions, cursor advanced after the batch,
      per-feature isolation.
- [x] `.dockerignore`, `npm ci` with the lockfile, backend deps from
      `pyproject.toml`.
- [x] `styles.css`: `prefers-reduced-motion`, at the END of the sheet (lesson:
      a media query before the rules it overrides does nothing).

### Phase 0 verification

| Gate | Result |
|---|---|
| `pytest` | 214 passed |
| `vitest` | 109 passed |
| `ruff check` + `ruff format --check` | clean |
| `mypy app` | no issues, 29 files |
| `tsc --noEmit` | clean |
| `docker build` backend + frontend | both build |
| dedup on live data | 16 -> 53 clusters |
| entry chunk | 1277 kB -> 235 kB |

Every fix in this phase ships with a test that fails without it, in
`backend/tests/test_audit4_phase0.py`. The dedup test in particular had to be
rewritten once: my first version passed against the buggy code because the
arrival order it built never triggered the early `break`. The bug only shows
when an old-dated event arrives *last*, which is exactly the catalog-bulletin
case the comment now describes.

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

### Phase 1 verification

The protocol now has a fifth message: `purge {ids, reason}`. Everything in this
phase hangs off it.

| Gate | Result |
|---|---|
| `pytest` | 237 passed |
| `vitest` | 114 passed |
| ruff / mypy / tsc | clean |
| live websocket | purge of **531 ids** received at t+299 s, reason `stale` |
| heartbeat during that run | 339 ticks in 340 s |

Measured on real data, as required for anything that filters:

| Change | Before | After |
|---|---|---|
| NWS alerts carrying `ongoing` | 0 / 295 | 295 / 295 |
| NWS alerts carrying an end time | 0 | 295 |
| Stale sweep on the real journal | 418 removed | **717** removed |
| Seismic history kept by that sweep | -- | 23.9 h over 965 quakes |
| Spurious revisions from the new fingerprint | -- | **0** over 590 alert-transitions |

Two defects were found while verifying, not in the audit:

1. **`expires` is not the end of the alert.** CAP `expires` is the validity of
   the MESSAGE; NWS `ends` is the end of the event. On the live feed of
   2026-08-18, 82 of 295 active alerts had `expires` already in the past and
   `ends` still hours ahead. Purging on `expires` would have wiped 28% of the
   running US warnings off the map. Fixed to `ends` first, `expires` only for
   the 23 that publish none. Fixture captured verbatim from that feed.
2. **The content fingerprint ignored the end of the alert**, so a warning
   re-issued with a later `ends` -- how NWS extends a tornado warning -- was
   taken for a repeat and kept the old end. Purged while in force, or left
   standing after being cut short.

And one defect the fix for 1.3 exposed: keying the sweep on the `ongoing` flag
was not enough. The running instance held **210 severe thunderstorm warnings,
none of them still in `/alerts/active`** -- over for hours, displayed as
current, invisible to the sweep because they predated the flag. The sweep now
keys on the NATURE of the event: an earthquake is a point in time and going
quiet says nothing about it; everything else here is an interval, and a source
that stops publishing an interval has said it ended.


---

## Positioning the WMO and Meteoalarm alerts (2026-08-26)

**The hole.** `/api/events` on the live site returns 287 events with `lat: null`
out of 900: **256 from `wmo`, 30 from `meteoalarm`**. An event with no position
is off the map, out of every distance ranking, and cannot answer "is that near
me". The same hole was closed for NWS by `sources/nws_zones.py` (11% -> 100%).

### What today's real bytes say

Measured on `https://severeweather.wmo.int/json/wmo_all.json` of 2026-08-26
(2224 items) and on the 10 Meteoalarm country feeds of the same morning.

1. The WMO aggregate carries **no geometry at all**. Its only positional lead
   is the CAP document it links: `url` for 1247 items, **`capURL` for the other
   977 -- a field the parser never read**, so those 977 also lost their `url`.
2. Fetching those CAP documents (771 of them, all `cap:1.2`) shows the real
   split. For the 444 alerts ranked Severe or Extreme: **194 publish an
   `<area><polygon>`** (Kazakhstan, Russia, Algeria, Saudi Arabia, Argentina,
   Spain, part of NWS...), 191 publish only a `<geocode>`, 45 publish nothing.
3. **`s`/`u`/`c` are ranked the OTHER way round from what the parser assumes.**
   Cross-checked against the `<severity>` of 100 CAP documents sampled across
   every rank: `s` 0=Unknown 1=Minor 2=Moderate 3=Severe 4=Extreme (100/100),
   `u` 1=Past 2=Future 3=Expected 4=Immediate, `c` 2=Possible 3=Likely
   4=Observed. So `WMO_SEVERITY = {1: EXTREME ... 4: MINOR}` is inverted, and
   `max_severity_rank=1` keeps **only the Minor alerts and labels them
   EXTREME**, while dropping the 91 Extreme and 353 Severe ones. That is also
   why the positioning looked hopeless: the kept set is 158/246 Chinese county
   blue warnings, the one producer that publishes no geometry.
4. **Meteoalarm publishes no geometry either**, with one exception: the UK Met
   Office fills `area[].polygon` (a list of CAP `lat,lon` strings) inside the
   JSON itself. Everything else is an `EMMA_ID` (or `NUTS3` for France,
   `WARNCELLID` for Germany) and an area name.

### Plan

- [x] New `backend/app/sources/cap_area.py`: parse a CAP `<area>` into one
      bounded point, and a disk-cached, per-cycle-bounded fetcher for the WMO
      CAP documents, on the shape of `nws_zones.py`.
- [x] `alerts_world.py`: read `capURL` as well as `url`; resolve a bounded
      batch of CAP documents per cycle and emit the positions; re-emit on a 304
      so a position learned late reaches open tabs as a revision.
- [x] `alerts_world.py`: fix `WMO_SEVERITY` / `urgency` / `certainty` to the
      measured ranks, and make the filter keep the top N tiers, so the existing
      `settings.wmo_max_severity_rank=1` keeps Extreme instead of Minor. No
      wiring change needed, but the setting deserves a rename.
- [x] `parse_meteoalarm`: read `area[].polygon` when the producer publishes it.
- [x] Tests first, fixtures verbatim from the payloads captured above.
- [x] Measure before/after against the live feed.

### Deliberately not done

- **Chinese `CPEAS Geographic Code`, `EMMA_ID`, `WARNCELLID`, `AMOC-AreaCode`,
  `KZSTD`**: these are code -> polygon tables that neither WMO nor Meteoalarm
  publishes (`api.meteoalarm.org/metadata/v1/regions` answers 401, every other
  candidate 404; the only geometry Meteoalarm serves is inside its map's MVT
  tiles). Resolving them means a third-party gazetteer, and a same-name
  collision would put an alert in the wrong province -- lesson 15.
- **`<circle>`**: CAP defines it, **0 of 771 real documents used it**. No real
  fixture, so no parser: untested speculative parsing is worse than a missing
  position.

### Review -- measured on the real feeds, 2026-08-26

**Baseline, taken from the live site** (`/api/events?limit=2000`, 1596 events):
744 without a position, and the two sources own all of them --
**wmo 293/293 unplaced (0%)**, **meteoalarm 450/450 unplaced (0%)**.

**WMO, after.** Same aggregate, same code path, positions read from the CAP
documents:

| kept set | before | after |
|---|---|---|
| `wmo_max_severity_rank = 1` (Extreme only, the current wiring) | 0 / 91 | **72 / 91 (79%)** |
| `wmo_max_severity_rank = 2` (Severe and above) | 0 / 443 | **218 / 443 (49%)** |

Producers that draw their warnings are placed whole: Kazakhstan 77/77, Russia
48/48, Argentina 21/21, Algeria 19/19, Saudi Arabia 17/17, Ecuador 7/7. The
gap is one producer: China, 0/144, and India 0/34 -- see below.

**Meteoalarm, after: 0 / 454. Unchanged, and that is the honest answer.** Of
the 2564 warnings served by the ten country feeds this morning, **2 carry a
geometry** -- both from the UK Met Office, both level 2, so both below the
alerting threshold. The parser now reads them; today they do not exist.

**Verified live**, on an isolated instance (`:8317`, wmo + meteoalarm only, so
the running `:8300` was left alone): `/api/sources` shows both `up`, and
`/api/events` shows the WMO alerts arriving with coordinates and filling up
cycle after cycle as the CAP cache learns -- **24/91, then 62/91, then 72/91**,
which is the 40-document budget doing exactly its job and landing on the number
the offline measurement predicted. The 19 that stay unplaced are precisely the
producers that publish no shape: China 14, India 3, Italy 2. And **48 of those
events came back with `revision > 0`**: a position learned ten minutes late
reaches an open tab as a revision instead of waiting for the alert to be
re-issued. Spot-checked against the payload: "Nauyrzym district (Kostanay
Region)" lands at 51.41 N, 64.42 E, in Kostanay, Kazakhstan.

**Suite: 357 backend tests green (13 + 9 new), `ruff check`,
`ruff format --check`, `mypy` all clean.**

**No wiring change is required**: `WmoCapSource(poll, rank)` keeps its
signature, and the CAP cache defaults to memory. Two optional follow-ups for
whoever owns `main.py` and `config.py`:

- pass `cap_cache=settings.data_dir / "wmo-cap.json"` so the positions survive
  a restart (the cache is bounded to 20000 entries and evicts the oldest --
  unlike an NWS zone, there is a new CAP document per alert issued);
- `wmo_max_severity_rank = 2` would take the feed from 91 Extreme alerts to 443
  Severe-and-above, of which 218 placed. The setting also deserves a rename:
  it counts tiers down from Extreme, and its current name says otherwise.

### Not done, on purpose

- **China (144 alerts) and India (34)**: CMA publishes a `CPEAS Geographic
  Code` and a county name; NDMA publishes neither geometry nor geocode. There
  is no public table from a GB/T 2260 code to a shape, so placing them means
  sending county names to a third-party gazetteer -- which is bulk geocoding
  against Nominatim's policy, and one same-name collision puts a flood warning
  in the wrong province. Lesson 15.
- **The 31 US alerts relayed by WMO with a UGC geocode**: resolvable through
  `api.weather.gov/zones`, but they are duplicates of alerts the `nws` source
  already places, and it would cost two extra hops per zone at another agency.
- **CAP `<circle>`**: 0 of 771 real documents used it, so there is no real
  fixture and therefore no parser.

---

## One ladder for every hazard: CAP severity / urgency / certainty (2026-08-26)

### The measured defect

`/api/events?limit=2000` on the live site, 2026-08-26 10:26 UTC, 1579 events:

- **480 Meteoalarm, 461 of them SEVERE** -- the rank this product gives a M6.5
  earthquake. The most frequent title in the entire product was "Orange
  Thunderstorm warning" (216). Poland alone: 331 events, 328 severe.
- **215 WMO, all EXTREME.** On the aggregate itself, 103 alerts at `s` = 4 and
  **75 of them are one member's routine forest-fire-danger bulletins**
  (`mid` 070, Kazhydromet). Not one of the 103 claimed `c` = 4 (Observed).
- Of the 233 EXTREME events in the feed, **zero** were an earthquake, a tsunami
  or an eruption. Sorted by severity, the top 20 was 7 red heat warnings and 13
  identical "Forestfire" bulletins.
- 714 of 1579 events (45%) sat at or above the alarm threshold
  (`SEVERITY_RANK >= severe` in `store.ts`, and the `ALERT` log line in
  `pipeline.py`).

Root cause: five national colour codes, a magnitude ladder, a CAP rank and an
aviation colour code were all being flattened onto one five-step severity as if
they meant the same thing -- and of CAP's three axes only `severity` was read.

### The rule

`severity_for_cap` in `models/event.py`, extending `severity_for_quake` rather
than inventing a second philosophy:

- the agency's own rank is the base (CAP defines it in harm terms);
- `certainty` is this product's PAGER, the field that knows more than we do
  about whether the harm is REAL. Observed -> the rank at face value;
  anything else -> one rung lower, because a forecast of harm is not a
  measurement of harm. `Possible`/`Unlikely` -> one further rung down;
- `urgency` deliberately does NOT enter the base, except `Past` (the danger is
  over -> capped at MINOR). Severity is "how bad", urgency is "how soon", and
  collapsing them re-creates the flattening. Demoting the advance notice is
  also lesson 14 in a new costume;
- nothing promotes above the agency's own rank: no CAP field says who is
  exposed.

Applied to `meteoalarm` (via the harmonized awareness level, the one scale its
ten countries share), `wmo`, `nws`. Plus one unrelated calibration fix in
`hazards.py`: EONET gave a flat MODERATE to everything, which put eleven
drifting Antarctic icebergs on the rung of a felt M5.

### Measured after (same payloads, same ingestion filters)

| source | before severe+extreme | after severe+extreme |
|---|---|---|
| meteoalarm (469) | 463 | 17 |
| wmo (103) | 103 | 96 |
| nws (3) | 3 | 0 |
| eonet (54) | 1 | 1 |

Whole feed, alarm threshold: 714/1579 (45%) -> 124/1155 (11%). EXTREME went
from 233 events, none of them a measured disaster, to one: AEMET reporting
180 mm already on the ground north of Tarragona.

### Not done, on purpose

- **The 75 Kazakh fire-danger bulletins still sit at SEVERE.** Their CAP
  document carries no `description`, no `instruction` and no `responseType`:
  `Fire / Forestfire / Extreme / Future / Likely` and nothing else. Nothing in
  the payload separates them from a genuine extreme fire emergency. The two
  honest fixes -- per-member calibration, or a "forecast of conditions"
  product family -- both need a measurement over days, not one snapshot.
  Demoting every WMO forecast to reach them would also have demoted the Indian
  "Extremely Heavy Rain" over Uttar Pradesh, which is the trade lesson 19
  forbids.
- **The ingestion filters** (`meteoalarm_min_level`, `wmo_max_severity_rank`)
  are untouched. They decide recall, not rank, and changing them needs its own
  before/after.
- **The ash SIGMET threshold** (`SEVERE` above 25000 ft): all nine live SIGMETs
  are below it, so no measurement could say whether moving it helps.
- **`cyclone_severity`**: already anchored on Saffir-Simpson, i.e. on harm.

---

# Upgrade 2026-10-26 -- native app, alerting, sources, design

## What the owner asked (2026-10-26)

Upgrade the whole project with Fable; use every tool and access we already
have; use ScanGithub; liaise with the other agents; adopt the SentinelHouse
design; take the whole system and structure back in hand; build the SOSForge
APK -- native, NOT a WebView, with real mobile funnels; perfect the sources,
the notification system, prevention and alerting; widen, perfect, complete
on every point.

## Decisions taken by default (the owner can overturn any of them)

- **Native stack**: Kotlin + Jetpack Compose, same pins, scaffold and
  `build.sh` shape as `SuiteForge/GSTATSFORGE/android` (the fleet's proven
  native app), Sentinel House kit (Lunar / Soft, IBM Plex, the mint thread,
  Fold 600 dp rule, 4 text sizes). No WebView anywhere. Package
  `co.soclose.sosforge`, minSdk 26, targetSdk 36. Lives in `android/`.
- **Map**: MapLibre Native Android, same OpenFreeMap style as the web. A map
  that cannot start degrades, the feed keeps running (same rule as the web).
- **Background alerts without Firebase in v1**: an opt-in "Watch" foreground
  service holding a zone-filtered websocket, plus a WorkManager fallback.
  FCM needs a Firebase project (an owner account action) and routes every
  alert through Google; the product is key-free. The app must say plainly
  when the watch is NOT running (battery optimisation, permission refused,
  socket dead): the freshness rule applies to alerts exactly as to the feed.
- **Release key outside the repo**: `~/.soclose/android/sosforge.properties`
  + `sosforge-release.jks`, like GStats. The APK goes to
  `VsCodeN30/_apk-drops/sosforge/` with a CHECKLIST.
- **Not done without the owner's go**: production deploy to the helper VPS,
  Play Store, installing on the owner's Fold.

## Contract between the backend and the app (additive: the web is unchanged)

- `/ws?zones=lat,lon,radius_km;...&min_severity=...&kinds=...` -- snapshot
  and deltas filtered server-side, `tick` ALWAYS sent. No parameter = today's
  behaviour, byte for byte.
- `GET /api/events` gains `lat`, `lon`, `radius_km`.
- `GET /api/guidance?lang=` -- prevention catalogue per hazard kind (before,
  during, after), short imperative lines, every line carrying the official
  source it comes from. One catalogue so the web and the app never disagree
  on what to do in an emergency.
- `GET /api/version` -- `{api, features[]}` so the app can detect a server
  that predates the contract and fall back to client-side filtering.
- The client ALWAYS re-filters what it receives (defence in depth: a server
  that ignored the filter must not flood the phone with the world).

## Lanes (Fable subagents, one git worktree each, disjoint files)

| Lane | Scope | Files |
|---|---|---|
| A -- app | native Android app, funnels, watch service, notifications, signed APK | `android/` only |
| B -- alerting API | the contract above, zone relevance, guidance catalogue | `backend/app/main.py`, `hub.py` (critical), new modules, tests |
| C -- sources | audit every source live, ScanGithub research, add only what runs against the real API | `backend/app/sources/`, registry, tests |
| D -- web | SentinelHouse charte on the web UI, notification and watch funnel | `frontend/` only |
| E -- system audit | read-only review of the whole system and structure | none (report) |

Critical files touched by lane B (`hub.py`, `main.py`) and lane C
(`sources/*.py`): this section is their written plan. `pipeline.py`,
`dedupe.py` and `store/ring.py` are NOT in any lane's scope; a finding there
comes back to me first.

## Port move 5273 -> 5380 (flagged by adsagent)

5273 is AdsForge's declared Vite port (its CLAUDE.md, vite.config.ts,
scripts/dev.sh): two dev servers would fight for it, or an agent would test
the other product's UI for an hour. SOSForge moves to 5380 (free, undeclared,
next to its 8300 / 8380). Me: Makefile, README, CLAUDE.md, .env.example,
tools/responsive.py, and the CORS default in `backend/app/core/config.py`
(one line). Lane D: frontend/vite.config.ts (with strictPort) and
vitest.config.ts. The VPS is untouched: production does not use Vite.

## Integration (me)

- [x] baseline gates recorded before any merge: pytest 486 passed, vitest
      166 passed (19 files), ruff clean, mypy clean (40 files)
- [x] lane B merged, gates green (f93acd6), then lane F (3c25a60). One
      interaction caught and pinned (4f3b22f): an alert re-listed past
      `expires` is retracted by the pipeline (F) and must reach the phone's
      cursor `removed` (B). 635 backend tests, agent 43, ruff + mypy clean.
      The pinned snapshot grew 331 -> 879 KiB; JSON deflates ~8.5x and
      uvicorn negotiates permessage-deflate by default, so ~100 KiB on the
      wire. Accepted.
- [x] lane C merged (5d6733d) and integrated (47092b4: registry built,
      zones match `bbox`, change log judges the previous box). Live on the
      integrated code, :8370, 2026-10-26 21:31 UTC: 32 sources, 32 up, 0
      degraded; the four new ones all with events (imo 15, imo-volcano 3,
      jma-volcano 12, ncs 300 seen / 48 ingested); 101 of 928 events carry a
      bbox. `space` and `jma-tsunami` saw 0 CURRENT events: their only items
      were expired and are now withdrawn at the source, as intended.
- [ ] lane D merged, screenshots at 390 / 1440 px, both themes, measured
- [ ] lane A rebuilt against the merged backend, emulator run of every funnel
      step, signed APK dropped with its CHECKLIST
- [ ] lane E findings reproduced one by one before any fix (lesson 25)
- [ ] commits by explicit paths only (lesson 26)
- [ ] review section written here, lessons captured

## Production update, stage 1 (2026-10-26)

- [x] PR #3 open (backend half: contract v2 + core fixes), CI green
      (backend, frontend, agent).
- [ ] merge: refused by the permission classifier ("merge without review").
      Waiting for the owner to review / merge, or to allow it.
- [x] lane D merged into the PR (14ca365): 246 frontend tests, panel fix
      verified on the captures.
- [ ] BLOCKER found by lane D before merge: the pinned snapshot is 118 KiB
      on the wire (52 before) and the 15 s watchdog runs while it downloads,
      so below ~64 kbit/s (was ~28) a fresh tab reconnects forever. Fix:
      chunked snapshot (`snapshot.more` + `snapshot_more` messages), server
      by lane B, client by lane D. PR #3 back to draft until it lands.
- [ ] then on helper-vps: tag the running images for rollback, copy the
      journal out of the volume, `git pull && docker compose up -d --build`,
      verify /readyz, /api/version, a live socket, the UI.
- [ ] then the host vhost `/ws` block: the three forwarded headers,
      `nginx -t`, reload, verify the API sees the real client address.

## Liaison

The 12 peer sessions are told what SOSForge is doing. Asked specifically:
`sentinelhouse-e3` (which design files are canonical), `gstatsforge-6a`
(native scaffold and build pitfalls), `agentfarm` (device-farm test protocol).

Answers received and relayed to the lanes:

- **agentfarm**: emulators 5584 / 5590 for the full functional test, every
  adb call through `adb_file.py` with the per-serial lock. The Fold only with
  a slot and its GO. Farm production phones (192.168.8.x and the four USB
  serials) never: live social accounts.
- **sentinelhouse-e3**: `charte.css` is the token source (Lunaire `:root`,
  Doux `[data-theme="doux"]`), align with `sentinel.css`, never fork it.
  Frontiers 599/600 and 779/780 only. Severity hues fixed across themes,
  a glyph of a different shape per severity, AA measured on both themes.
- **gstatsforge-6a**: copy the kit from the CURRENT tree (0.2.4) and note
  the source commit; TileRow, text heights, screenshot method, live-stream
  single source. No shared kit library exists yet (owner decision).
- **ebookforge-84**: JDK 21, release gate (permissions allowlist, 16 KB
  aligned .so, signer sha256, no dev host).
- **agentkr**: cursor + ETag/304 polling, quiet-hours rules (an extreme
  alert near the user is never held).
- **aiblog-c5, clip-generator-local-67**: auth handoff and Glance widgets,
  not needed for a key-free app.

## Lane E findings -> owners (audit 2026-10-26, top four re-measured by me)

Re-measured on production before dispatch (lesson 25): a fresh tab's snapshot
reaches back 8.3 h; 49 of 70 severe/extreme/tsunami events and 264 of 346
ongoing alerts are missing from it. `/ws` sends no forwarded address and
uvicorn runs without `--proxy-headers`, so the 24-socket cap is shared by the
whole site. On a phone `WatchPanel` is not mounted while the sheet is
closed. jma-tsunami has ingested 12 257 times against 181 for jma.

| # | Finding | Owner | Files |
|---|---|---|---|
| 1 | snapshot misses ongoing / severe | B (composition), D (client 1000 cut), A (app) | main.py / scope.py, store.ts |
| 2 | 24 tabs for the whole site | F (repo side); VPS host vhost = owner go | deploy/nginx.conf, backend/Dockerfile |
| 3 | location alarm dead on phones | D | App.tsx, WatchPanel, store |
| 4 | escalation never sounds | F (server `escalated` flag + agent), D and A (consume it) | pipeline.py, agent/ |
| 5 | restart resurrects withdrawn events | F | store/ring.py |
| 6 | expired alerts flap every 5 min | F (pipeline), C (jma_tsunami skips expired rows) | pipeline.py, sources/ |
| 7 | depth rule bypassed (EMSC etc.) | C | sources/ |
| 8 | tsunami counter 0 during a warning | F (counter), D (banner 6 h cutoff) | ring.py, App.tsx |
| 9 | swarm counts itself, duplicates | F | swarm.py |
| 10 | half-dead source shows green | C (degraded + last_event_at), D (footer) | sources/base.py |
| 11 | silence sweep ignores source health | F, MEASURE on the journal first | ring.py |
| 12 | socket stuck in CONNECTING | D | live.ts |
| 13 | agent swallows outage events | F; A applies the same rule to the watch service | agent/ |
| 14 | aftershock forecasts lack `expires` | C | sources/aftershock.py |
| 15 | security headers sent twice | me, at deploy (owner go) | VPS host vhost |
| 16 | Meteoalarm HTML entities; geocode global queue | C; me | sources/, geocode.py |
| -- | docs drift (19/26/28 sources, test counts, API docs) | me, at integration | README, CLAUDE.md, .env.example |
| -- | nginx 1.27 EOL, CI gaps | F | frontend/Dockerfile, .github/ |
| -- | backend lockfile | owner decision | -- |
| -- | structure (registry, protocol, routers) | me, after B merges, behind a golden contract test | main.py |

Lane F (new) owns the critical files no other lane holds: `pipeline.py`,
`store/ring.py`, `swarm.py`, `agent/`, the Dockerfiles, `deploy/nginx.conf`,
CI. This section is its written plan.

## Alerting contract v2 for the native app (2026-10-26, lane B)

Additive. With no new parameter, `/ws` and `/api/events` answer with the
same messages and the same body keys as before; the only change a legacy
client sees is the snapshot composition (below), asked for by the
orchestrator after the production audit. `hub.py` keeps its slow-client
eviction policy untouched: a scoped client gets a narrowed message (or
none) and then goes through the same bounded queue and the same eviction.

- [x] `app/scope.py`: `zones` (<= 10, lat/lon bounded, radius 1..2000 km),
      `min_severity`, `kinds`, `unplaced` (`drop` | `all` | ISO country
      list). Great-circle match on the event's point, and since lane C on
      its `bbox` when the source published a shape (`Zone.touches`).
      Events with no position: dropped by default and COUNTED
      (`unplaced.dropped`), or delivered by country / all on request.
- [x] Snapshot composition (`scope.compose`): latest N primaries + every
      older primary that is ongoing, severe-or-above or tsunami; the zone
      filter runs BEFORE the cut. Same composition for v2 full REST reads.
- [x] `app/hub.py`: `Client.scope` + held ids; tick always passes; an
      event/update that stops matching a held id becomes
      `purge reason=out_of_scope`; purges narrowed to held ids; snapshot
      ids held before the first await. `changelog.record` cannot break the
      fan-out.
- [x] `app/changelog.py`: in-memory log with, per entry, the event's
      matching fields BEFORE the change, so a REST delta can tell "left
      your scope" from "never was in it". Seeded from the store after the
      journal replay and backfill. Opaque cursor `<epoch>:<seq>`.
- [x] `app/main.py`: `/ws` params (refused with an `error` message, then
      close 1008), `/api/events` v2 (`lat`/`lon`/`radius_km`, the ws scope
      params, `since`, ETag + `Cache-Control: no-cache`, 304), legacy path
      byte-for-byte (and deliberately no ETag), `/api/version`,
      `/api/guidance`.
- [x] `app/guidance.py` + `app/data/guidance.json`: 13 kinds x 3 phases x
      5 languages, 753 lines, each with source URL, source language and the
      verbatim excerpt it paraphrases. Every excerpt re-checked as a
      substring of the fetched page (680 on a fresh fetch, 73 against the
      saved page where the site refuses plain curl).
- [x] Tests: test_scope, test_changelog, test_ws_contract,
      test_events_v2, test_guidance (113 new; mutation-checked).
- [x] Live measurement on :8310 (review below).

### Chunked snapshot, opt-in (`/ws?chunks=1`)

The pinned snapshot in one message regressed slow links: the web's 15 s
silence watchdog runs while it downloads, so a fresh tab closed mid-snapshot
and reconnected forever. Pre-chunking web builds THROW on an unknown frame
and installed macOS agents are old, so chunking is opt-in:

- without `chunks=1`: the pre-v2 snapshot rule, byte for byte in keys and
  content (latest `snapshot_size` primaries, scope applied if given, no
  pinning, no `more`);
- with `chunks=1` (any other value: `bad_chunks`, error + 1008): the
  composed snapshot (latest + every ongoing / severe+ / tsunami primary,
  scope before the cut) as `snapshot` + `more: N`, then `snapshot_more`
  `{events, remaining, server_time}` chunks in composition order, all
  before the first queued delta, every id held from the start. Chunks are
  cut at 40 events or 32 KB of JSON, whichever comes first; the first
  message is the first chunk.

Measured on :8310, permessage-deflate as negotiated by uvicorn 0.54
(server_max_window_bits=12), real wire bytes through a counting proxy and a
deflate simulator that matched them to the byte:

| | messages | largest on the wire | floor for no 15 s gap |
|---|---|---|---|
| pre-merge (= no opt-in) | 1 (300 events) | 54.6 KiB | 29.8 kbit/s |
| PR #3, one message | 1 (469 events) | 79.7 KiB | 43.5 kbit/s |
| `chunks=1` | 18 (469 events) | 6.9 KiB (total 81.6) | 3.8 kbit/s |

### Not done, on purpose

- **A magnitude-dependent reach** for earthquakes (the local agent's felt
  radius): the zone is the client's own radius, by contract.
- **The 1 Hz tick costs about 1 KiB on the wire** (8.3 KB of JSON, 28
  sources' health): roughly 8 kbit/s sustained. Below that, a client that
  got its snapshot still falls further behind every second and is evicted
  at the queue bound. Slimming the tick (sources only when they change) is
  a protocol change for lanes D and F, not done here.
