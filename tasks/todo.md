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
- [x] lane A: native app (515eae7, `android/` only), 263 tests, the whole
      funnel + watch service + map run on emulator 5590 under the adb_file
      lock, 4 real notifications. Signed APK 0.1.0 (code 5), sha256 0307549...,
      signer verified with apksigner; dropped in `_apk-drops/sosforge/` with
      its CHECKLIST. Separate PR #4 (touches nothing deployed). Then 0.1.1
      (code 6, d537570): the clock-skew fix lane D found in the web, same
      rule; sha256 971ebc3..., same key, unit tests only (no emulator rerun).
- [ ] lane E findings reproduced one by one before any fix (lesson 25)
- [ ] commits by explicit paths only (lesson 26)
- [ ] review section written here, lessons captured

## Production update, stage 1 (2026-10-26)

- [x] PR #3 open, now backend + sources + web, ready for review, CI green
      on the final state (backend, frontend, agent). `main`'s own CI has
      been red since 2026-08-26 (`No module named 'app'`); PR #3 fixes it, so
      PR #4 (android only) goes green once #3 is merged.
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

## Follow-ups (not in PR #3 / #4)

- **The tick is now the bandwidth floor** (lane B): each 1 Hz tick is ~1 KiB
  on the wire, 8.3 KB of JSON, mostly the health of 32 sources, so ~8 kbit/s
  sustained. Send `sources` only when they change (protocol change for web,
  app and the agent, opt-in like `chunks=1`).
- APK is 39 MB because MapLibre ships three ABIs: split per ABI for the Fold.
- es / ja / id strings of the app and the 28 guidance lines flagged
  `needs_review` need a native reader.
- `specialUse` needs a Play Console declaration before any store release.
- ScanGithub scans 164, 165, 166 are queued behind fleet scan 163 (no cancel
  endpoint): read their libraries when they complete.
- `read_journal` skips events dated more than 24 h ago, so long-running
  alerts vanish until the next poll after a restart (lane F).
- Structure (lane E): sources registry as a table, `protocol.py`, routers;
  behind a golden contract test, after these PRs merge.

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

---

# Round 2 (2026-10-27) -- live, offline map, SEO, app download, UI, sources

## What the owner asked (2026-10-27)

Do everything yourself and put it all live on https://sosforge.soclose.co;
install the APK on his phone and check that the whole notification system
works; check "use my location"; pre-download the OpenFreeMap map so it is not
fetched every time; make the site fully indexable; let people download the
Android APK from the public site; responsive on every format and screen; put
the footer's source list in a slider; fold the kind filters into a button;
use ScanGithub to fill what is missing (climate, live change, more hazards).

## Blocker: the merge

`gh pr merge 3` is refused twice by the permission classifier ("Merge Without
Review"), even with the owner's go. Not worked around: no direct push to
`main`, no deploy of an unmerged branch. The owner merges on GitHub, or adds
a permission rule for `gh pr merge`. Every "live" item below waits on it.

## Measured before deciding

- OpenFreeMap tiles: `cache-control: max-age=315360000`, dated immutable paths
  (`/planet/20261025_080012_pt/{z}/{x}/{y}.pbf`), Cloudflare HIT. The browser
  already never re-downloads a tile it holds. Style 86400 s, TileJSON 3600 s.
  So a VPS tile proxy buys nothing. What is missing is OFFLINE: the map must
  still draw when the network drops in a disaster. -> service worker cache for
  the tile host (capped, low zooms precached at idle) + offline packs in the app.
- `frontend/public/` already has robots.txt, sitemap.xml, manifest, sw.js
  (shell cached, `/api` and `/ws` never intercepted) and full OG/Twitter/JSON-LD.
  SEO work is completing that, not starting it.

## Contract: the APK download (lane G serves it, lane A reads it)

`GET /download/latest.json`, `Cache-Control: no-cache`:

```json
{"app": "co.soclose.sosforge", "version": "0.1.1", "version_code": 6,
 "url": "/download/sosforge-v0.1.1.apk", "size": 40000000,
 "sha256": "<hex>", "cert_sha256": "610d345a...bdf8", "min_sdk": 26,
 "released": "2026-10-27", "notes": {"en": "...", "fr": "..."}}
```

The APK is served as `application/vnd.android.package-archive` with
`Content-Disposition: attachment`. APK binaries never enter git nor the docker
image: they live in a host directory bind-mounted read-only into the web
container, filled by a script that also writes `latest.json` from the APK
itself (sha256, size, version read back with aapt/apksigner, never typed).

## Lanes (one git worktree each, disjoint files)

| Lane | Agent | Owns | Delivers |
|---|---|---|---|
| G | fresh | `frontend/index.html`, `frontend/public/**` (sw.js, robots, sitemap, manifest, new `/download/` static page), `deploy/nginx.conf`, `docker-compose.yml` (downloads mount), `tools/publish_apk.py`, `downloads/` (gitignored) | SEO completed and measured (Lighthouse SEO), APK download page + latest.json, offline tile cache in sw.js |
| H | lane D resumed | `frontend/src/**` (components, App, styles.css, i18n), `tools/responsive.py` | sources slider (a down source never hides in the scroll), filters behind one button, "Get the app" entry, "use my location" verified, responsive at every format (320 px to 4K, landscape, foldables, 200 % text) |
| C2 | lane C resumed | `backend/app/sources/**`, new `backend/app/climate.py` + its route, new `frontend/src/components/ClimatePanel.tsx` (I mount it) | sources found through ScanGithub + probed live; live climate indicators each with its own as-of date; new sources map onto EXISTING kinds (no contract change) |
| N | lane B resumed | `tools/drill.py` (new, no production code), fixes in hub/scope/changelog only if a drill proves a defect | drill harness that injects real-shaped events into a local server, then proves every alert path: web alarm, macOS agent, websocket scope/purge/escalation, `/api/events` cursor |
| A | lane A resumed | `android/**` | 0.1.2: offline map packs for the user's places + world low zooms, update check from `latest.json`, location flow verified, full notification E2E on the emulator with `tools/drill.py` |
| P | fresh | `backend/app/main.py`, `pipeline.py`, `store/**`, `core/**`, `backend/Dockerfile`, `pyproject.toml`, `agent/**` | measured efficiency (CPU, RSS, fan-out at 1/100/1000 clients, startup replay, journal growth, loop lag, latency), fixes with before/after |
| Q | fresh | new `tools/audit_live.py` + `make audit` only | live data audit, production vs branch: freshness lag, validity, cross-source precision, noise vs importance, missing major events; defects routed with record ids |

## Me

- [ ] plan committed, lanes briefed
- [x] Fold: 0.1.1 installed through the adb queue (owner go, 2026-10-27,
      10:16:58 -> 10:17:01, 3 s): installed base.apk sha256 matches the drop,
      versionCode 6. Notifications and location not granted: the owner's own
      first-run funnel. Reported to agentcockpit.
- [x] reply to agentcockpit with what is needed from the owner (merge #3 and
      #4; optional Search Console, FIRMS key, Play Console, native review)
- [x] production read (2026-10-27, read-only): 28/28 sources connected, no
      last_error, a tick every second, snapshot 300 events / 312 KB raw. A
      +3 s local-vs-server skew was the link (ssh RTT 6 to 21 s at that
      moment), not the server: the VPS is NTP-synchronised, the Mac is
      +0.13 s from time.apple.com.

## Defects found by the drills, routed

| Found by | Defect | Owner |
|---|---|---|
| N | Meteoalarm lifts never reach anyone: an Update carries a NEW identifier and `references` the old one (FR 143/161, IT 120/120, old id never still listed 0/419); `references` ignored, and Meteo-France's AllClear (level 1 green) dropped by select(). Lifted orange warnings stay until `expires` or the 6 h sweep | C2 |
| N | ws quota refusal is a 403 handshake rejection, never close 1013: `close(1013)` runs before `accept()` in websocket_endpoint | P |
| N | web alarm never rings for an escalation: `alarmed` keyed by id only (M6.4 -> M7.3 = one notification) | H |
| N | web never closes the system notification of a cancelled / relocated-out event (a cancelled EEW stays in the tray) | H |
| N | macOS agent silent on an EEW cancel and on a quake relocated out of reach | P |
| N | macOS agent judges area warnings by their centre, ignores `bbox` (UK amber 232 km from Paris: silent) | P |

| Q | duplicates on screen: dedupe `assign()` matches another agency's newer entry before the event's own and re-appends it every poll (usgs:us7000ttfe + emsc:20261027_0000114 both shown) | P |
| Q | two quakes folded into one cluster (us6000u4t9 hidden inside us6000u4t8; 13 on prod-09) | P |
| Q | each cyclone served 2-3 times with different severities (Champi, Arnab, Vance, Olana: GDACS / NHC / EONET) | P |
| Q | the first estimate stays the face (Bonin: JMA EEW M7.5 EXTREME vs M6.2-6.5 everywhere else; Bouvet GEOFON 6.07 vs 5.8) | P + C2 (`preliminary`) |
| Q | journal replay restores events the current rules would reject (36 of 37 post-deploy defects) | P |
| Q | major quakes vanish after 3 days (USGS M7.0 Nagqu 10-22, PAGER orange): keep M6.5+ / orange-red 14 days as past events, owner ask "tout l'historique" | P |
| Q | PHIVOLCS revised bulletins kept as extra quakes (53 of 193) | C2 |
| Q | USGS merged ids kept (aka2026vddzqy inside us6000u4t8) | C2 |
| Q | INGV re-identified events kept twice (46940061 -> 47630362) | C2 |
| Q | NWS PKZ767 (Bering Sea) marker placed off Greenland: centroid averaged across the antimeridian; clear `nws-zones.json` on deploy | C2 |
| Q | INGV stamps "Italy" on foreign quakes (M6.1 Vanuatu) | C2 |
| Q | footer "Last event 40s" for re-listed old events (BMKG newest is 29 h old): breaks the freshness rule | C2 (field) + H (label) |
| Q | static text says "Twenty-six" / "nineteen" sources, /api/sources lists 32 | G |
| Q | `/download/latest.json` answers 200 with the SPA HTML on production | G |

Lane Q result (commit 1fa5ed2, merged): `tools/audit_live.py` + `make audit`.
No important event inside the horizon is missing (every USGS significant /
M5.5+, EMSC M5.5+, GDACS red cyclone, NHC storm is served). The round-1 deploy
removed 180 expired German Meteoalarm warnings, 8 all-clear items, the
expired JMA tsunami forecasts, 4 finished GDACS droughts, a stale SWPC G3 and
a false Sicily swarm; BMKG serves again.

Lane N result (commit 4970235, `tools/drill.py` + `tools/drills/`): `check` 67
pass / 6 fail across v1, zoned fg/bg, drop, poll and agent clients; web drill
15 pass / 3 fail. Every failure is one of the rows above.
- [x] PR #3 merged (772ad82, 2026-10-27) and DEPLOYED: b18a272 -> 772ad82,
      rollback images sosforge-{api,web}:rollback-b18a272 + data copy
      /root/backups/sosforge-20261027T100237Z. Verified from outside: /readyz
      32/32 sources up, /api/version api 2 with every v2 feature, zone +
      cursor + ETag 304, ticks 1.01 s; chunked snapshot complete in 0.06 s
      measured inside the server (the 12 s seen from the Mac was its
      saturated link). Host vhost /ws now forwards X-Real-IP /
      X-Forwarded-For / X-Forwarded-Proto (backup, nginx -t, reload).
      Pings to agentcockpit before and after.
- [x] PR #4 merged (6685dce, owner go 2026-10-27): the Android app source
      (android/ only, CI green). Nothing deployed by it.
- [x] lane N merged (351f161): drills + guards, pytest 759
- [x] lane H merged: vitest 299, tsc, build; responsive 27/27 viewports
- [x] lanes G (11925b4), Q, P (1b64666) merged: pytest 847, vitest 319,
      agent 58, ruff, mypy, tsc, build
- [x] PR #5 adversarial review (92 agents): 41 findings judged, 35
      confirmed (2 high, 21 medium, 12 low), 6 refuted. Web findings -> H
      (scratchpad pr5-review-web.md), tool findings -> N
      (pr5-review-tools.md). HIGH: Near me hides unplaced warnings;
      an escalation re-shown without `renotify` is silent.
- [x] lane C2 merged (8a96c57): Meteoalarm lifts (references / AllClear),
      /api/climate (7 live indicators, dataset dates), US Drought Monitor,
      UK EA built off (5/5 duplicates of Meteoalarm UK), lane Q's source
      defects, `label` / `last_event_at` / `newest_event_time` in
      /api/sources. Gates: pytest 884, vitest 334, mypy 52 files.
- [x] lane G follow-up merged (f771f42): automatic offline precache 1.47 MB
      transfer (was 17.3 MB), the rest on request or known unmetered link.
- [x] WMO two tiers globally: REJECTED on measurement (today: +164 events,
      125 moderate + 30 minor, relays of NWS and Meteoalarm UK, 55 bare
      "Forestfire"). A targeted tier-2 rule for uncovered issuers and kinds
      goes to C2, numbers before shipping.
- [x] lane N2 merged: the 4 tool findings fixed with tests (drill signals,
      web_alarm teardown by PID, responsive.py Chrome ports and profiles),
      agent expectations match lane P. On the merged tree:
      `tools/drill.py check` 73 passed, 0 failed (every scenario x every
      client, the Meteoalarm lift included, quota close 1013); web_alarm.py
      18/18 on lane H's web (N2's run).
- [ ] follow-up, not urgent: scope.split() serializes each event then main.py
      serializes the chunk again: 4.14 -> 2.33 ms per chunked snapshot if
      passed through (lane N2's measurement, byte-identical output)
- [x] lane C2 WMO tier 2 merged (70a599d): +5 placed alerts (3 Colombian
      landslides, Syria flood, Chad heat), IDEAM re-issues collapsed
- [x] lane H polish merged: the 31 web review findings fixed (table in
      lane H's report), place search scopes the feed with nearest events,
      climate panel, offline map button, honest footer freshness. Gates on
      the merged tree: pytest 900, vitest 428 (39 files), tsc, build
- [x] final review of PR #5 (92 agents): recheck 34/35 fixed (#4 partly:
      a non-ongoing tsunami-flagged quake vs the window); new findings
      outside sources: 34 confirmed, 0 high, 16 medium, 18 low. Routed with
      evidence: H 17 + #4, G 11 (nginx access log with query + IP, language
      shells uncached, sw Wi-Fi upgrade continuing on mobile, publish_apk
      downgrade check local only), C2 3 (ENSO synopsis, NSIDC caveat), P 3
      (httpx and ScopeError logs carry places). Sources findings: skeptics
      re-running after the rate limit.
- [x] lane P merged (128127f): opt-in `tick=lite` (42.6 -> 1.37 MB/h raw on
      the app's background URL), privacy findings closed (httpx Nominatim
      URLs, ScopeError reasons, stricter redact_query). pytest 920.
- [x] lane G review fixes merged: all 11 fixed with tests that fail on the
      old code (language shells no-cache, `sos_private` nginx log + log cap,
      Wi-Fi recheck per file, publish_apk checks what the server serves,
      byte-capped tile cache). vitest 435, pytest 935.
- [ ] deploy step to add: the HOST vhost access log keeps full query
      strings: a redacted log_format for sosforge.soclose.co (conf.d,
      nginx -t, reload); deploy order web image -> make publish-apk -> the
      two rsync steps (versioned files --ignore-existing, then latest.json)
- [ ] fixes landed and re-verified (H 18, C2 3 sources)
- [ ] then merge lane C2's WMO country flags (9603657: 58/58 WMO alerts
      flagged, 0 in production; Greek pairs titled by hazard) and the
      Meteoalarm colour floor (owner rule: an official orange is never shown
      below moderate, red never below severe)
- [ ] then owner go on PR #5, merge, deploy, APK published
- [x] app 0.1.2 (lane A, branch lane-a-app-0.1.2, PR #6): the Fold location
      bug fixed (request survives the step change, last known from every
      provider, fused + network + GPS for 30 s, honest timer), near me by
      default, onboarding with lane K's art and real captures, offline
      packs (world z0..3 54 MB, place 21 MB, unmetered only), update check
      pinned to the release certificate, honest privacy wording. APK sha256
      d56b0c20...a6e9b3, versionCode 7, same signer; copied to _apk-drops
      with its checklist. I reviewed the welcome, location-wait and Live
      captures myself.
- [ ] Fold: 0.1.2 install queued at 13:00 but the Fold was not connected
      (adb: device not found, lock released at once); a watch installs it
      when it reappears
- [ ] lane P: opt-in lighter tick for zoned sockets (the app's watch costs
      ~4.7 MB/h off Wi-Fi: 5 KB ticks, no permessage-deflate on OkHttp)
- [ ] later: an OpenFreeMap style without the unused relief raster, for the
      app's offline packs (104 MB of the old 157 MB world pack)
- [ ] remaining lanes (G, C2, P, Q, A, K) -> next PR + deploy, APK published

## Fold evidence (2026-10-27, read-only, under the adb lock)

The owner ran the funnel at 17:05 (device time). Notifications granted,
coarse location granted APPROXIMATE + while using. Then
LocationManagerService: the app's fused request (BALANCED, duration 30 s) was
added at 17:05:30.428 and REMOVED by the app at 17:05:33.115 with
`locations = 0`. The app cancelled its own request: that is the "location
does not work" the owner sees. No foreground watch running, no notification
posted since. Four channels exist (extreme 4, severe 3, digest 2, status 2).
-> lane A, priority 1 for 0.1.2.

## Every owner ask of 2026-10-27, and where it stands

| Ask | Where |
|---|---|
| deploy live | round 1 live (772ad82); round 2 in progress |
| APK on his phone | 0.1.1 installed; 0.1.2 next (lane A) |
| notifications fully working | drills (N): 67/73 server, web fixed (H); app drills (A); agent fixes (P) |
| "use my location" / geolocation, web and app | web fixed (H, 4 Chrome scenarios); app bug proven on the Fold (A) |
| live view on the person's location by default, world otherwise | web done (H); app (A) |
| OpenFreeMap pre-downloaded | measured: already cached 10 years; offline added (G web, A app) |
| site fully indexed | G |
| APK downloadable from the site | G (page + latest.json), H (entry) |
| responsive on every format | web 27/27 (H); app (A) |
| sources in a slider, filters in a button | done (H) |
| ScanGithub: more sources, climate, live change | C2 |
| optimise / perfect / complete everything | P (efficiency), Q (live data audit) |
| nothing fake, only real live data | rule sent to every lane; drills guarded (N) |
| onboarding with images, overview, preview (kie.ai) | K (art), A (funnel) |
| "quand je fais une recherche rien ne s'affiche" (koh samui) | H: the picked place left `filters.query` set, so the feed text-matched "koh samui" against titles and emptied; fix = picking a place clears the text filter and scopes the feed to that place, nearest events shown when nothing is near |
| "notre système devrait tout donner! météo, mers, vent, air, risque, tout l'historique" | place report workflow: sources measured live at 8 places with licences checked -> contract + critique -> backend `app/place/` + `/api/place` and `PlaceReport.tsx` in worktrees -> live truth check against each upstream, reviews, fixes |
| adversarial review before PR #5 goes live (ultracode) | review workflow: 5 lenses, 3 skeptics per serious finding |

- [ ] Fold: install 0.1.1 through the adb queue (owner go, 2026-10-27), report
- [ ] reply to agentcockpit with what is needed from the owner
- [ ] integrate lanes on `feat/sosforge-round2-2026-10`, gates, PR #5
- [ ] after the merge: deploy (ping agentcockpit, image tags, journal backup,
      pull + build, host vhost headers, publish the APK, verify live)

## Lane P -- measured efficiency of the core (plan, critical files)

Scope: `main.py`, `pipeline.py`, `store/**`, `dedupe.py` (measure only),
`core/**`, `backend/Dockerfile`, `backend/pyproject.toml`, `agent/**`. Every
item is measured first, on a live run of all 32 real sources (:8335) or on
real journals, and re-measured after. Tools: `tools/ws_load.py` (N websocket
clients, tick period / latency, server CPU and RSS from `ps`),
`tools/bench_core.py` (hot paths in-process, full ring), `tools/bench_geocode.py`.

Measured before (2026-10-27, M4 Pro, machine load ~13 from other lanes):

- 1 / 100 / 1000 sockets: server CPU 1.3 / 1.4 / 14.8 %, tick period
  1.0019 / 1.0015 / 1.0256 s (the heartbeat sleeps 1 s AFTER its work),
  tick latency p99 6 / 9 / 518 ms, RSS 0.15-0.45 MB per connected socket
  (the higher figure while 1000 sockets opened at 400/s).
- 1000 sockets opened at 400/s: snapshot p50 0.83 s, p95 4.68 s, while every
  tick to the sockets already open stalls (max gap 1.46 s).
- Full ring (5000): `stats()` 2.1 ms per call (each tick and each snapshot),
  legacy snapshot 5.8 ms, chunked 17 ms, `/api/events` 12.9 ms p50.
- Restart: `read_journal` drops every event whose `time` is over 24 h old,
  while the pipeline's own horizon is 3 days and exempts ongoing alerts. On
  the real 3 h journal of 2026-10-26 a restart restored **679 of 1347**
  events: 362 of 499 alerts and 44 of 104 severe-or-above lost until their
  source's next poll (Meteoalarm 227, GDACS 75, EONET 20...), and the quakes
  between 24 h and 3 days old lost for good.
- Journal: `raw` (the upstream payload, never read back from a restored
  event) is 19.4 % of the live journal's bytes. Plan 1.8, never done.
- Geocode: one address firing N searches makes every other visitor wait N
  seconds (10 -> 9.96 s, 30 -> 29.98 s). The queue is unbounded.

Changes, smallest first:

- [x] `main.py`: the quota refusal closed before `accept()`, which a real
      server sends as HTTP 403 (lane N's drill). Accept, close 1013 with a
      reason; tested with a real uvicorn and a real client (e7a3d9a).
- [x] `agent/`: the reconnect backoff never reset (`run_once` only leaves by
      an exception). Reset after a session that delivered (a022416).
- [x] `agent/`: lane N's drills, real agent, notifier recorded (f36ba98).
      A cancelled / lifted / ended / relocated alert it had rung got no
      follow-up; now one, once, without sound. An area alert was judged by
      its centre; now by its bbox, the clamp of `Zone.touches`. Drill rows:
      eew-cancelled tokyo M6.1, M6.6 -> + "Cancelled"; relocated-out tokyo
      M5.9 -> + "No longer near you"; relocated-out paris silent -> UK
      amber, then "No longer near you".
- [x] `store/ring.py` `read_journal`: no cutoff on `event.time`; the
      pipeline's horizon is the one rule (expired, too old and not ongoing).
      Read line by line instead of `read_text()` (no whole-file copy at
      startup). Test: an ongoing alert dated 30 days ago and a quake of 2
      days ago, both journaled today, survive a restart; a quake of 4 days
      does not. **After: 1348 of 1348 restored (was 679), alerts 500 (137),
      severe+ 104 (60); second journal 1196 (622).**
- [x] `store/ring.py` journal lines without `raw`. Test: the line carries no
      `raw`, the replayed event is equal on every public field.
- [x] `main.py` heartbeat on a monotonic deadline, so the period is 1 s
      whatever the fan-out costs; a stalled loop resumes from now instead of
      firing a burst of catch-up ticks. **After: mean period 1.0000004 s
      (was 1.0019) with one socket, 0.99994 s (was 1.025) with 1000.**
- [x] `main.py` + `store/ring.py`: the unscoped snapshot (web `chunks=1`,
      agent legacy) serialized once per store version and shared, instead
      of composed and serialized for every socket. The store gets a
      `version` counter bumped on every mutation (insert, revision,
      removal, promotion); `last_seen` refreshes do not bump it, so the
      cache also expires after 1 s. Scoped sockets keep their own snapshot.
      Gate: same bytes as the uncached path for the same store. Built in
      its own coroutine too: as locals of the endpoint it stayed referenced
      for the whole connection (339 -> 87 KB held per socket in-process).
      **After, 1000 web sockets opened at 100/s: 1000/1000 connected (was
      908, 92 handshake timeouts), snapshot p95 0.029 s (was 8.45 s), CPU
      7.8 % (23.4 %), RSS 108 MB (189). At 300/s: 1000/1000 (770), the
      watcher's tick period max 1.017 s (1.86 s).**
- [x] `main.py` legacy `/api/events` returns its JSONResponse: FastAPI's
      `jsonable_encoder` re-walked 300 JSON-native dicts, 6.9 ms of 14.
      Same bytes (tested). **After, live: p50/p95 6.5/10.2 ms (was
      15.1/35.9), limit=2000 17.9/34.5 ms (66.0/233).**
- [x] `geocode.py` + `main.py`: per-address token bucket (burst 3, 1 per
      second) on cache misses, 429 + `Retry-After` beyond. The web debounces
      at 450 ms and treats a failure as "no places", so a person typing is
      never refused. **After: a burst of 10 or 30 from one
      address costs another visitor 2.95 s (was 9.96 / 29.98 s); the burst
      gets 3 answers and 429 for the rest.**
- [ ] `Dockerfile`: NOT changed. Measured: `pip install .` adds 22 MB to
      python:3.12-slim (~125 MB); removable parts are watchfiles 1.0 MB and
      pyyaml 0.66 MB (uvicorn[standard], dev reload / YAML logging only)
      and a second 1 MB copy of `app` in site-packages. About 2 % of the
      image, and Docker does not run on this Mac, so an unbuilt Dockerfile
      is not worth that. For whoever can build: install the dependencies
      from pyproject (tomllib) BEFORE `COPY app`, and drop `pip install .`
      (uvicorn runs /srv/app), so a code change stops reinstalling 22 MB.
- [x] `dedupe.py`: the number said so. cProfile of the startup replay on the
      real journals: `Deduper.assign` is 0.83 s of 1.23 s, of which 0.37 s
      is 1.38 million `datetime.timestamp()` calls -- every located quake,
      re-polls included, scans the 800-entry window converting each
      entry's time twice. Live, a 400-726 event poll (GEOFON, PHIVOLCS)
      blocks the loop for roughly 65-115 ms, which matches the 170 ms tick
      stalls measured over 15 min. Change: each entry's time cached as
      integer microseconds in a parallel deque (exact, no float rounding),
      same scan, same order, same tests. Gate: the cluster of EVERY event
      identical between the old and the new deduper on the real journals.
      **After: 6678 assigns on 6707 real entries, 0 clusters different,
      same window; 136.6 -> 21.5 us per assign (x6.3).**
- [x] `pipeline.py`: measured, no change. After the deduper fix the replay
      is 0.60 s for 5714 real entries (was 1.23 s, cProfile), dominated by
      journal validation; `emit` itself is 0.28 s.

### Lane P, second batch: lane Q's live-data findings in the core (plan)

Q's proofs: `scratchpad/laneq/` (prove_dedupe_churn.py, saved reads run2,
run3) and `tools/audit_live.py`. Critical files: dedupe.py, pipeline.py,
store/ring.py. Measured on real data before and after, like every dedup
change (rule 5).

1. [x] **Re-listing churn** (dedupe.py). A re-polled row scans the window
   newest-first, meets ANOTHER agency's newer copy of the same quake before
   its own entry, matches it and appends itself again. Q's proof, my tree:
   after 399 cycles of two agencies re-listing one quake, 400 copies each
   fill the 800-entry window and an unrelated quake drops out; its late
   EMSC twin then forms its own cluster (shown twice). Fix: an id already
   in the window answers from its own entry, O(1), without appending. The
   store already keeps the first cluster of a stored id, so clusters can
   only change where the churn evicted the real match.
   **After, Q's proof: the window never overflows in 1000 cycles and the
   late twin joins usgs:us7000ttfe. Real journals (10-26 + 10-27, 1228
   quakes, re-listed every 50 entries): visible twins 4 -> 0, multi-source
   clusters 92 -> 96, window 486 -> 800 distinct ids of 800.**
2. [ ] **Two quakes in one cluster** (dedupe.py). NOT SHIPPED, measured. A second USGS quake 90 s
   after the first joins its cluster through EMSC's solution of the first.
   Rule: a cluster never takes two events from the same source (an agency
   does not publish one quake twice). Measure first, on the real journals:
   same-source clusters (with their time and magnitude gaps, to tell two
   quakes from an agency duplicate) and cross-source merges lost.
   **Measured, the rule is wrong as stated.** Built and replayed: same-source
   clusters 28 -> 0 and 14 -> 0 on Q's two production reads, but visible
   twins 1 -> 21 and 2 -> 15 (after item 1). PHIVOLCS publishes ONE quake
   under several bulletin ids (`2026_1026_1509_B2F` and `B3F`, same M3.5,
   2 km), INGV shows the same, and refusing them a shared cluster then splits
   identical EMSC/INGV solutions (0 s, 0 km) through the chain. Reverted
   before commit. A per-source rule (USGS / EMSC / GEOFON ids are one per
   quake, PHIVOLCS bulletins are not) needs each source's id semantics
   checked against its payload first: lane C2 knows them.
3. [ ] **Cyclones served 2-3 times** (dedupe only clusters quakes). Plan
   only here: grouping storms by name + basin + distance, with the
   agency face chosen by authority, needs item 4's re-election, or the
   first poller after a restart becomes the face. Measure the duplicates.
   **Measured (Q's run3): 4 of 6 named storms served by two sources --
   Arnab gdacs extreme + eonet severe, Champi gdacs extreme + eonet
   moderate, Olana nhc minor + eonet moderate, Vance nhc + eonet.**
4. [ ] **The first estimate stays the face.** Re-electing the cluster's
   representative (reviewed > automatic > early warning) changes which id
   is `primary` under every client's feet: the web, the app and the agent
   hide non-primaries. A protocol-visible change (lanes N, H, A); plan and
   numbers, not shipped by this lane alone. **Measured (run3): 17 of 214
   multi-member clusters show an early warning or a preliminary solution
   while a reviewed one exists (e.g. jma_eew:20261027092519 M5.0 over USGS
   and EMSC M4.8, geofon:gfz2026vacc M5.1 over USGS and EMSC M4.7).**
   Mechanism when it is decided: the store re-points the cluster to the
   better member (rank: reviewed > automatic > early warning, then source
   authority), broadcasts the new face as `update primary: true` and the old
   one as `purge reason=superseded`, which every client already handles.
5. [ ] **Journal-replay residue.** The replay already goes through the
   current pipeline (expiry, horizon, min magnitude, removals). What it
   cannot re-apply is a NORMALIZER rule changed since the line was written
   (depth cap, preliminary flag): the line holds the normalized event, and
   `raw` is not a parser input. Not built: the proof (36 of 37 defects on
   restored events) is on production journals this lane cannot read. Two
   options for the owner, with their cost: (a) journal lines carry a hash
   of the source module that normalized them and the replay drops lines
   from an older normalizer (correct at once, loses the history of
   sources whose feed no longer lists the event); (b) the replay
   re-derives what is derivable from the normalized fields (quake
   severity from magnitude + depth through the current `severity_for_quake`,
   EEW `preliminary`) and keeps the rest.
6. [ ] **Major events kept 14 days** (owner call by default). Needs the
   horizon to exempt them, the replay to read 14 days of journal for them,
   `journal_keep_days` >= 14, and the ring not to evict them. Measure how
   many events that keeps before building it. **Measured on Q's reference
   read: 3 quakes in 7 days meet M6.5+ or PAGER orange/red (Nagqu M7.0
   orange 10-22, Vanuatu M6.9 10-24, Bonin M6.5 10-25), so 3-6 over 14
   days.** Smallest build: a separate `major.jsonl` journal (a few lines a
   week) replayed whatever its age up to 14 days, a horizon exemption for
   those events only, and the ring re-appending a major event it evicts.
   Not built in this lane: it changes what the feed calls current, and
   the UI must show them as past (lane H).

**Q's audit, same minute (2026-10-27 10:47:29Z), base code vs this lane,
both started from one copy of the real journal, 32 live sources:** restored
592 vs 954 events; dedupe summary clean on both (0 split, 0 extra
primaries, 0 over-merged; multi-source groups 46 vs 48 -- the churn needs
hours of polling to show, its proof is Q's script above). Two more defects
on the new side, both on events the base had dropped at restart and both
normalizer rules (item 5, lane C2): gdacs:EQ1573942 under-graded and
phivolcs:2026_1026_1025_B1 superseded but never retracted. Also new only:
ingv:47788232 stamped IT at the Vanuatu position (C2). Read with care: the
new side's sources show `ingested=0`, "newest 88 m" because restored events
keep their real `received_at` and re-polls are no-ops; the base re-ingested
them as new at restart (lesson 4).
## Lane C2 (sources + climate) -- plan

- [x] Meteoalarm lifts (critical file `sources/alerts_world.py`): an Update /
      AllClear carries a NEW identifier and `references` the old one (France
      143/161, Italy 120/120; referenced id still listed: 0 of 1009 today).
      Fix in `MeteoalarmSource.select`: every warning's `references` withdraws
      the referenced event, BEFORE the level filter -- AllClear as `lifted`, a
      replacing Update as `stale` (its successor is published under its own
      id). No stable-key remapping: the store key of a replaced warning is its
      own identifier, which survives restarts; a chain map would not.
      Measure: published warnings replaced/lifted between two real captures.
- [ ] New sources measured live first: UK EA floods, DWD WFS warnings,
      ECCC alerts, INMET Brazil (research report 2026-10-27)
- [ ] `app/climate.py` + `GET /api/climate` + `climate` feature; real dataset
      dates only, unavailable with the real error when a fetch fails
- [ ] `frontend/src/components/ClimatePanel.tsx` (+ css, test, i18n strings)
      Done: one cycle on the 2026-10-27 feeds withdraws 333 referenced
      warnings as lifted (Croatia 172, France 143, Germany 18) and 902 as
      stale (most never stored: no-ops). Of the 35 orange+ warnings published
      at the 2026-10-26 19:10 capture, 3 (UK) were replaced by an Update by
      the next morning and would have stayed until their own `expires` -- a
      lower bound, since a chain of updates references only its predecessor.
- [ ] Lane Q audit findings in `sources/` (critical files), each re-read on the
      original record first, measured before/after with `tools/audit_live.py`:
      PHIVOLCS newer bulletin withdraws the older one of the same quake; USGS
      `ids` aliases withdrawn; INGV re-identified event withdraws the old id;
      zone/CAP/UK centroids through `geometry.center` (antimeridian) and the
      two persisted area caches versioned so bad centres are re-read; INGV
      stamps no country it cannot read; footer freshness = first-seen time of
      a NEW event + the newest event's own time (not "last poll returned
      something"); EEW/CENC preliminary; HANS NMI -> MP.

## Lane C2 -- WMO targeted tier 2 (critical file `sources/alerts_world.py`)

- [x] Global tier 2 rejected by measurement (rank 1: 59, rank 2: 223, mostly
      relays of NWS / Meteoalarm / EA and "Forestfire"/"Low water" noise).
      Targeted rule instead: keep `s=3` (Severe) ONLY when the ISSUER is not
      one we ingest directly (no NWS relay, no Meteoalarm member) AND the
      kind is one we have little or nothing of (heat, landslide, drought,
      avalanche, flood), and never when the product severity comes out minor
      or info. Issuer = the CAP source directory of the item (`co-ideam-es`).
      Measure before shipping, with the CAP documents fetched: per issuer and
      kind, placed share, country, duplicates against the direct sources.
      Measured on the 10:47 aggregate (1314 items), every CAP fetched: top
      tier 54 published today; IDEAM re-issues collapsed -> 48; targeted tier
      2 -> 53 (+5: Colombia landslide 3, Syria flood 1, Chad heat 1, all
      placed with a box). BOM excluded (5 more, 0 placed, licence). Live at
      11:27: prod 63 WMO vs branch 58. Not shipped: numbers sent first.

## Lane P: the lighter tick, opt-in (plan, critical files main.py + hub.py)

Lane A measured on the emulator: the app's background watch costs ~4.7 MB
an hour off Wi-Fi. The tick is sent every second with the health of every
source in it, and OkHttp's websocket offers no permessage-deflate, so it
travels uncompressed.

Contract (additive; no parameter = every message byte for byte as today):

- `/ws?...&tick=lite` (any other value: `bad_tick`, error + 1008).
- A lite client's tick, every second, unchanged in rhythm (the 15 s
  watchdog): `{type: tick, server_time, stats, clients}`, `stats` without
  `by_source` (the same counters travel per source as `ingested`).
- `{type: sources, server_time, sources: [...]}`, the full list in the
  tick's own shape, to lite clients only: whenever a source's STATE changes
  (connected, degraded, last_error, failing feeds), and as a refresh every
  300 s so `last_ok` / `last_event_at` / counters are never older than
  that. The snapshot already carries the full list on connect.
- A lite client must read a tick without `sources` as "unchanged", not as
  "no sources" (the 0.1.2 decoder would empty the list: it must not opt in).
- `/api/version` features gains `tick_lite`.

Implementation: `Client.lite`; `Hub.broadcast(message, lite=None)` sends
`lite` (serialized once) to lite clients instead of `message`, and a
`message` of None reaches nobody else. The heartbeat builds both ticks from
one `stats()`. Measured before/after: bytes and messages per hour on a real
zoned socket, all real sources, with a client that offers no
permessage-deflate (OkHttp's behaviour), and the negotiation checked.

**Lighter tick, measured (da80ea2).** 15 min on :8335, all 32 real sources,
the app's background URL (two zones, unplaced=JP,FR, min_severity=moderate,
chunks=1), two sockets side by side from a client that offers NO
permessage-deflate: the server answered with no Sec-WebSocket-Extensions,
so every frame went raw, as OkHttp gets it.

| socket | ticks | sources msgs | bytes / hour | with TLS+TCP (est.) |
|---|---|---|---|---|
| full tick | 900 x 11.8 KB | -- | 42.6 MB | 42.8 MB |
| tick=lite | 900 x 270 B | 9 x ~11 KB (3 refreshes, 6 changes) | 1.37 MB | 1.64 MB |

x31 less. The snapshot (12.9 KB, once per connection) is the same for both.
The full tick is larger than lane A's 4.7 MB/h figure suggests (11.8 KB raw
today): the source list grows with every `last_error` it carries.

**Privacy, final review of PR #5 (49c4ada):** the httpx logger now goes
through the same filter (Nominatim URLs redacted), the geocode warning logs
no URL, a refused socket logs its code only, and redact_query blanks bare
parts and safe names holding a value of the wrong shape.

## Lane C2 -- WMO country from the issuer (critical file `sources/alerts_world.py`)

- [x] The aggregate's OID ids carry no country (id-prefix rule: 0 of 1319
      items). Flag from the CAP source directory (`co-ideam-es` -> CO) only
      for land hazards (heat, landslide, flood, drought, avalanche, wildfire,
      storm over land), only when the prefix is a national ISO 3166-1 code
      (`uk` -> GB), never for a marine issuer or product, never when the CAP
      area names another country. Measure on the real aggregate: flags gained,
      and every one judged by eye.

## Lane C2 -- Meteoalarm colour floor (critical file `sources/alerts_world.py`)

- [x] Owner/coordinator rule: an official colour is never contradicted on
      screen. Meteoalarm ONLY: after the CAP demotions, orange never below
      MODERATE, red never below SEVERE; yellow and lifts unchanged; NWS / WMO
      Severe-Possible demotion unchanged. Test: the Velebit orange (certainty
      Possible, published MINOR on 2026-10-27). Measure what moves.
      Measured on today's captures (ten countries, 09:53 / 11:40 UTC): 1 of
      the 15 published warnings moves -- the Velebit orange, MINOR -> MODERATE.

## Lane C2 -- PR #5 final review, sources (critical files `sources/regional.py`, `sources/base.py`, `sources/alerts_world.py`, `sources/tsunami.py`, `sources/usdm.py`)

- [x] INGV re-identification: withdraw an old id only when the CURRENT page no
      longer lists it (the 302 case). Two ids listed at once within 1 s and
      10 km are two catalogue solutions, both kept. Test on real pages
      holding both ids (Sefro 47459342/47459372, Eolie 47569802/47569772);
      reproduce the double withdrawal first.
      Reproduced on the two real pages: cycle 1 withdrew one id of each pair,
      every later cycle all four. After: 0 withdrawn over 3 cycles; the page
      without 47569772 withdraws exactly that id.
- [x] Freshness baseline per feed, not per first `ok()`: Meteoalarm's
      per-country `ok()` no longer primes the source (the cycle's end does),
      and a feed answering for the first time after failing since startup
      lists its baseline, not new data (`feed_ok` before that feed's emits in
      Meteoalarm and tsunami). Replay the real country payloads in `run()`
      order before/after.
      Ten real feeds (13:11 UTC) through `run()`: before, `last_event_at` =
      startup time from cycle 1 (and re-stamped when Greece, 503 in cycle 1,
      came back); after, null for 3 cycles in both cases, and stamped when a
      warning appears after the baseline. Live :8333: meteoalarm up, 25
      events, `last_event_at` null after its first full cycle.
- [x] USDM: persist the published week's region ids in the source memory, so
      a restart between a weekly release and the next poll still withdraws
      the previous map's regions. Test with the real map.
      Real maps valid 2026-10-13 and 2026-10-20 (D4): 9 -> 7 regions, 7 ids
      gone. Before: a restart between them withdrew 0; after: the 7, as
      `stale`. Live :8333: `memory-usdm.json` holds the 30 ids of the
      2026-10-22 release.
