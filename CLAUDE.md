# CLAUDE.md -- SOSForge

Product guide. Authoritative for the inside of SOSForge; the SuiteForge
`CLAUDE.md` stays authoritative for cross-product conventions.

> Never use em dashes. Use `--`.
> The whole project is written in **English**: code, comments, docs, commit
> messages. The only French left is inside the i18n dictionary and inside test
> fixtures captured verbatim from real feeds.

## What it is

A real-time tracker for earthquakes, tsunamis, volcanoes and disaster alerts.
Nineteen public sources merged into one normalized feed, broadcast over
websocket with a one-second heartbeat. FastAPI + React 19 / Vite / TypeScript /
Zustand / MapLibre. No API key: everything is public.

Live at <https://sosforge.soclose.co> (helper VPS, `/root/SAAS/SuiteForge/SOSForge`).

## The rule that governs the product

**The feed must never lie about its own freshness.** An emergency tracker that
shows frozen data while claiming to be live is worse than one that is switched
off. Concretely:

- the server emits a `tick` every second; a client that stops receiving them for
  15 s declares itself disconnected and reconnects;
- a server that accepts the connection and then says nothing is closed too --
  otherwise the very first connection could hang forever announcing "LIVE";
- ages ("12 s ago") are computed on the **server** clock (`clockSkew`), never on
  the browser's;
- `/api/sources` exposes the real state of every source, including its last
  error, and the footer shows it permanently;
- a partial failure (one dead source, no WebGL) degrades, it does not go dark.

## Critical files -- write a plan in `tasks/todo.md` before touching them

| File | Why |
|---|---|
| `backend/app/pipeline.py` | the single path of every event. A bug here touches the whole product |
| `backend/app/dedupe.py` | cross-source grouping. Too loose: earthquakes vanish. Too strict: duplicates everywhere |
| `backend/app/store/ring.py` | the only storage point, also handles revisions and sweeps |
| `backend/app/hub.py` | websocket fan-out, and the slow-client eviction policy |
| `backend/app/sources/*.py` | each normalizer is pinned to a real schema: never "fix" a field without re-reading the source payload |
| `frontend/src/live.ts` | reconnection + watchdog: this is what backs the "live" promise |

## Traps verified against the real sources (do not rediscover them)

- **EMSC**: `geometry.coordinates[2]` is a **negative** elevation (`-10.0`);
  `properties.depth` is positive in km. ISO timestamps. `id` == `properties.unid`.
- **USGS**: `time` and `updated` in **epoch milliseconds**;
  `geometry.coordinates[2]` is a **positive** depth. The opposite convention to EMSC.
- **GDACS**: `gdacs:severity` carries the numeric value in the **attribute**
  `value` (the text is for humans: "Magnitude 5.8M, Depth:54.7km"). The unit
  changes with the type: M, km/h, ha. One event has several episodes: the stable
  key is `eventid`. The server regularly takes 60 s to return 1.2 MB, so a wide
  timeout is mandatory. `gdacs:iscurrent=false` is GDACS saying the event is
  **over** (all 43 such items had a past `todate`): it is withdrawn. Published
  anyway, four orange droughts closed on 2026-10-24 -- one open since
  December 2025 -- stayed on screen as severe and ongoing, re-listed every
  cycle, so the silence sweep never fired.
- **tsunami.gov**: the category (Information / Watch / Advisory / Warning) and
  the magnitude live in the `summary` HTML, served sometimes escaped and
  sometimes as real elements (which then come back prefixed by the Atom
  namespace). So we regex on the **stripped** text, never on the markup. PHEB's
  `link rel="self"` wrongly points at PAAQ: do not trust it. The two centres
  fail independently (PAAQ timed out on 2026-10-26 while PHEB answered): one
  dead centre is `degraded` with its name in `failed_feeds`, never plain green.
- **Ash SIGMETs (AWC `isigmet`)**: `coords` has **two shapes**, told apart by
  `geom`: `"AREA"` is one ring of `{lat, lon}`, `"AREAS"` a LIST of rings. The
  second one took the source down for 15.6 h on production (2026-10-25 17:21
  -> 10-26 08:58 UTC, "'list' object has no attribute 'get'"): a Whakaari plume
  split at the antimeridian, one ring written with **lon 180.3**. Wrap every
  longitude and use `sources/geometry.py`, which knows the antimeridian. New
  Zealand writes `qualifier: "MT"` and names the volcano only in `rawSigmet`.
  `validTimeTo` is the end (not always +6 h: 2.1 h seen).
- **NWS**: identifying User-Agent mandatory; `limit` returns 400 on
  `/alerts/active`; `geometry` is often `null` (UGC zones) -- the alert must stay
  usable without a position. **"Hurricane Force Wind Warning" is NOT a
  hurricane**: it is the marine product for a non-tropical low (the tropical
  one is "Hurricane Warning"). Read on the word "hurricane", Alaskan gales
  were published as EXTREME cyclones, here and in the WMO relay. The full
  event vocabulary is `GET /alerts/types` (111 names): measure a classifier
  change against it, not against today's handful.
- **HANS volcanoes**: no coordinates in the response. They come from the
  Smithsonian Holocene catalogue, joined on `vnum` == `Volcano_Number`. The key
  is the VOLCANO, not the bulletin, otherwise notices stack up as markers.
- **JMA**: ISO 6709 in `cod`, where depth is in **metres and negative**. The feed
  also serves a **degrees-minutes** variant (`+3237.5+13040.7`) that must be
  rejected, not parsed -- a wrong position is far worse than none. And the JMA
  relays **distant** earthquakes (`遠地地震に関する情報`): do not stamp them
  `country="Japan"`, a M7.7 in Indonesia was wearing a Japanese flag.
- **JMA EEW / CENC (Wolfx relay)**: the magnitude key is misspelled
  `Magunitude`; the CENC payload is a dict indexed `No1`/`No2`, not an array;
  both timestamp in **local time without an offset** (Tokyo, Beijing); a
  cancelled EEW must disappear. The EEW payload also carries `isTraining` and
  `isCancel`: a drill must never be published, whatever its magnitude.
- **JMA tsunami**: the list keeps an event for **weeks**, so our own 12 h
  ceiling (`expires`) is the only exit -- an advisory past it is withdrawn,
  never re-emitted. Re-emitted, two forecasts of 2026-09-30 and 2026-10-10
  were purged by every sweep and put back by the next poll, announced to every
  open tab as new events every five minutes. 「津波予報（若干の海面変動）」
  ("slight sea-level change") is the tier BELOW an advisory: a TSUNAMI-kind
  event at INFO, without the tsunami flag -- NOAA's "Information" rule.
- **BMKG**: `gempaterkini` is only the 15 latest **M5+**; with no M5 in three
  days the horizon drops all of them and BMKG publishes nothing (30 seen, 0
  ingested, 2026-10-26). `gempadirasakan` (felt quakes, any magnitude) and
  `autogempa` (the latest) key the same quake by the same `DateTime`: merge
  them. **`autogempa` reuses `Potensi` for something else** ("Gempa ini
  dirasakan untuk diteruskan pada masyarakat" -- felt, pass it on): the
  tsunami flag needs the word "tsunami", or a felt M4.9 becomes an EXTREME
  tsunami. Times: `DateTime` is UTC with an offset; `Jam` is WIB (UTC+7).
- **AFAD**: `date` is **UTC** (proven by cross-checking EMSC). `limit` truncates
  **before** sorting, so `orderby=timedesc&limit=3` returns the three OLDEST of
  the window: use a short window, a large limit, and sort client-side. Answers
  302 before its JSON.
- **Meteoalarm**: `awareness_level` is a composite string ("1; green; Minor");
  `responseType: AllClear` means the warning is LIFTED; every warning carries its
  content twice (local language + English) and must yield ONE event. It
  publishes **no geometry**: 28896 of 28898 area blocks carry an `EMMA_ID`
  (`NUTS3` for France, `WARNCELLID` for Germany) and no shape, and no public
  endpoint turns those codes into one. The UK Met Office is the single
  exception -- it fills `area[].polygon`, a LIST of CAP rings. **The feed keeps
  listing a warning for days after its `expires`**: on 2026-10-26, 241 of the
  276 orange-and-red warnings served were already over (median 30 h, worst
  127 h; German storm gusts of the 21st). `expires` is the warning's end
  (verified on those DWD gusts), so it is mapped and an expired warning is
  withdrawn. Greece escapes `&amp;` inside the JSON (`"East Sterea &amp;
  Evvoia"`, `headline` + `areaDesc` only): decoded there and nowhere else.
  **A warning is replaced under a NEW identifier**: the Update (or the lift,
  an AllClear at level 1 green) names the old one in CAP `references`
  (`sender,identifier,sent` triples) and the old one is never listed again
  (0 of 1009 on 2026-10-27). Read `references` BEFORE the level filter and
  withdraw what they name, or a lifted orange stays on every client.
  **Greece issues two warnings per region** (thunderstorm AND rain, different
  ids, no reference between them): two hazards, two events -- never merge
  them. Its rain headline names only the colour ("Orange warning for
  Thessalia"): the awareness type is put in the title. **An official colour
  is never contradicted on screen**: the CAP rule demotes a forecast one rung
  and `certainty: Possible` one more, which published Croatia's ORANGE Velebit
  warning as MINOR (2026-10-27). For Meteoalarm only, the colour is a floor:
  orange >= MODERATE, red >= SEVERE (the NWS/WMO Severe-Possible demotion is
  unchanged).
- **WMO**: `s`/`u`/`c` are CAP ranks that grow **WITH** the severity, not
  against it: `s` 0=Unknown 1=Minor 2=Moderate 3=Severe 4=Extreme, `u` 1=Past
  2=Future 3=Expected 4=Immediate, `c` 2=Possible 3=Likely 4=Observed. Read
  once as "1 = most severe", which made the source keep the Minor alerts,
  publish them as EXTREME and drop every Extreme one. Verified against the
  `<severity>` element of 100 CAP documents. The scale is still **not**
  homogeneous between countries. The aggregate carries **no coordinates at
  all**; the CAP document it links does, for about half the Severe/Extreme
  alerts, and it is named `url` on some items and **`capURL` on the other
  977** -- read both. The aggregate's `expires` is the CAP document's, in UTC
  (ten issuers from UTC-5 to UTC+8, 10/10 to the second): it is the alert's
  end. Colombia (IDEAM) sets `headline` to the literal
  **"CAP Alert"** on every message; the real name is in `event`. The `event`
  values are in Spanish, French, Indonesian too ("Inundación urbana",
  "ORAGE", "Hujan Lebat dan Petir"), and Arabic ("فيضان مفاجئ"): the patterns
  carry those words. **The whole second tier is noise** (rank 2: 223
  published vs 59 -- NWS relays, Meteoalarm/EA copies, "Forestfire", "Low
  water"); only a TARGETED second tier is kept: issuers we do not read
  directly, kinds we lack (heat, landslide, drought, avalanche, flood), never
  minor, never BOM in that second tier (its CAP has no polygon: 0 of 5
  placeable). BOM STAYS in the top tier: those are public-safety warnings WMO
  relays for redistribution, which is not the reuse of BOM's own API that was
  refused in round 1 -- the refusal was about that API's payload terms, not
  about a warning the issuing service hands to WMO to spread. The issuer is
  the CAP source directory (`co-ideam-es/...`). **The aggregate's OID ids
  carry no country** (0 of 1319 by id prefix): the flag comes from the
  issuer's ISO prefix (`uk` = GB), for land hazards only, never for a marine
  product -- read from the area and headline, because "Gale" is a sea-area
  warning for MET Norway and SMHI but CMA's land strong-wind warning, and
  DWD's "gale-force gusts" are over towns, and "wave" is a WORD not after
  "heat"/"cold" (a substring, it made "Severe heatwave warning" a sea
  product; whole word alone still sinks "Heat Wave") -- and never when a
  TOP-LEVEL area name is another country (Colombia has towns called Armenia,
  Montenegro, Albania, Florida, California: municipality lists in
  parentheses are not read). NWS territories are postal suffixes
  (`Saipan, MP`): their own code.
  The CAP id is a WMO OID naming the issuer's country too (`2.49.0.0.170` =
  ISO numeric 170, Colombia; table in `sources/iso3166.py`): it must agree
  with the directory prefix (882 of 882 did on 2026-10-27). **IDEAM re-sends an unchanged warning under a
  new identifier every few hours, without `references`**: identical content
  from one issuer is collapsed to the newest copy.
- **SWPC alerts**: `alerts.json` keeps a bulletin listed for weeks after its
  "Valid To". A G3 warning valid until 2026-10-10 was still published, SEVERE,
  on the 26th: severe and not a quake, it escaped the horizon and nothing ever
  removed it. The validity is now `expires`, and an expired bulletin is
  withdrawn.
- **IMO Iceland** (`api.vedur.is/quakes/events`, CC BY 4.0): the old
  `POST /skjalftalisa/v1/quake/array` that most clients still call is **404**.
  Magnitudes go **negative** and carry six decimals. `type` is not the closed
  list its OpenAPI claims: `null` on fresh automatic events, and
  `"anthropogenic event"` appears though the enum omits it. **"not existing"
  is a DELETED detection and the default listing still returns it** -- the
  GeoNet trap: withdraw it. "not locatable" ships coordinates that mean
  nothing: skip it. A third of the M1+ events are offshore ("Reykjaneshryggur"
  is the Reykjanes RIDGE): never stamp Iceland from the source. Slow (1.5 to
  10 s) and flaky. Volcano levels (`/volcanoes/volcanoes`): ground level in
  `vals.current.alert_level_info.code_en`, aviation colour in `vona`; it also
  watches Beerenberg, which is Norwegian and says so in `country`.
- **JMA volcanoes** (`bosai/volcano/data/warning.json`): the list is the
  volcanoes under a warning PLUS the ones just lowered (lowered = lifted, not
  published). The level is read from the **wording**, never the bare `code`
  (lesson 27), and its digits are **full-width** (`レベル２`): NFKC first.
  Volcanoes without levels say 「火口周辺危険」 and submarine ones
  「周辺海域警戒」. `reportDatetime` is when the level was last changed or
  re-issued -- Iwo Jima's is from 2007 and still in force. No coordinates:
  join `bosai/volcano/const/volcano_list.json` on `eventId == code`
  (`latlon` = two STRINGS, latitude first).
- **NCS India** (`riseq.seismo.gov.in`): **no API at all**; each event is a
  JSON string in the `data-json` attribute of an `<li class="... event_list">`
  of the home page (630 KB). Times end in **`IST`** (+05:30, proven by the
  EMSC/USGS offset histogram peaking at exactly 0 h after conversion); refuse
  any other suffix. The web server's `Date` header runs ~48 min slow: never
  date anything from it. Magnitude and depth share one string (`"M: 4.0 , D:
  10km"`). Mostly FOREIGN events (Tajikistan, Tibet, Afghanistan): no country
  stamped at the source.
- **Environment Agency (England floods)** (`/flood-monitoring/id/floods`, OGL v3):
  `severityLevel` runs DOWN (1 danger to life ... 3 flood alert), and **4 =
  "no longer in force" stays listed** (58 of 98): lift it. No position in the
  list: the static `/id/floodAreas/{id}` has WGS84 `lat`/`long` and an
  `envelope` in **British National Grid metres** (EPSG:27700, converted in
  `ukea.py`). Times have NO offset and the reference names no zone: read as
  Europe/London, exact in winter -- re-check on 2027-03-28 (BST).
  **Meteoalarm UK already relays the EA's Flood Warnings word for word** (5 of
  5, "Flooding is expected: <area>"): the source is off by default; enabled at
  level 2 it doubles every warning. Measure a new source against the
  aggregators BEFORE counting a gap as missing coverage.
- **US Drought Monitor**: a weekly MAP (5 MultiPolygons, `DM` 0-4), no date
  but `Last-Modified`, 17 MB uncompressed: `If-Modified-Since`, and never a
  6 h wait to retry a failure. Region ids are category + rounded centre and
  change every week (7 of 9 D4 ids between the maps valid 2026-10-13 and
  -20): last week's regions are withdrawn against the map last PUBLISHED,
  kept in the source memory, or a restart between two releases leaves both
  weeks on screen. Past maps: `data/json/usdm_YYYYMMDD.json` (the Tuesday).
- **PHIVOLCS** revises a quake under a NEW bulletin name and drops the old
  row (`2026_1026_204121_B1` -> `2026_1026_2041_B2`); same-minute rows are
  mostly DIFFERENT quakes (500 km apart). Only the page says what is current:
  a bulletin that leaves it inside the window is withdrawn (memory kept next
  to the journal, so a restart does not forget).
- **USGS** merges network ids: `properties.ids` lists the aliases of the
  preferred `id`; an alias stored as its own quake is withdrawn (the day feed
  is read for `ids`, a merge lands after the hour feed has dropped it).
- **INGV** re-identifies events (`eventid=46940061` -> 302 -> `47630362`, same
  origin): same origin + position withdraws the old id, ONLY once the page
  stops listing it. The catalogue also lists TWO solutions of one quake at
  once (47459342/47459372, 0.2 s and 0.12 km apart; four such pairs in
  October 2026): withdrawn on the match alone, each removed the other every
  cycle and the quake re-arrived as new every minute. Italian places end in
  a province code `(PZ)`; seas and abroad use brackets (`[Mare]`, `[Sea:
  Vanuatu]`): Italy only on a province code.
- **Area centres** (NWS zones, CAP polygons, UK Meteoalarm, SIGMETs) go
  through `sources/geometry.py`: averaged naively, PKZ767 (Bering Sea) landed
  south-east of Greenland. The zone and CAP caches are versioned: bump the
  version whenever a stored centre can no longer be trusted.
- **Source health freshness**: `last_event_at` = first-seen time of an event
  never handed over before (re-listing is not news), `newest_event_time` =
  the newest event's own time, `last_delivery_at` = a poll returned anything.
- **Rejected after checking** (2026-10-26): Chile CSN (HTML only, fully relayed
  by EMSC, licence limited to academic use), KOERI (non-commercial clause, AFAD
  already covers Turkey), IGEPN (page frozen since June, Cloudflare), Geoscience
  Australia (clean CC BY, but every event already in EMSC; its
  `epicentral_time` is LOCAL time wrongly suffixed `Z` -- read `origin_time`),
  MAGMA Indonesia (API returns 500, no reuse grant), ReliefWeb (approved
  `appname` now mandatory), Taiwan CWA / Korea KMA (key mandatory), Colombia
  SGC (403 from every egress tried -- retest from the hub).
- **Measured and left out** (2026-10-27): DWD warnings (at the product's
  orange threshold a 1:1 duplicate of Meteoalarm Germany; below it, yellow
  wind), ECCC Canada and INMET Brazil (alive, but every live warning is the
  yellow tier: 0 events at the threshold today; INMET also keeps 61 of 67
  ENDED warnings listed), Copernicus EMS (post-event mapping, days late),
  LHASA (raster), GDO/EDO (raster, relayed by GDACS), NASA FIRMS API (key;
  the keyless 24 h CSV is 48k raw hotspots), EAWS/avalanche.org (licence of
  the ratings not found; out of season), SACHET India (no licence text,
  polygons behind a WAF), NINA/BBK (no licence text).
- **Climate datasets** (`app/climate.py`): Copernicus Climate Pulse marks
  the last days `PRELIMINARY`; NOAA GML omits missing days (look up "a year
  earlier" within +-3 days); NSIDC v4 has two header lines, every-other-day
  early records and a `Missing` column (an extent with missing coverage is a
  lower bound), and its daily files stopped at 2026-10-17; CPC's weekly Nino
  file glues a negative anomaly to the SST (`20.6-0.1`); the ENSO status is
  only in the discussion HTML, with entities.
- **INFORM** (`drmkc.jrc.ec.europa.eu`, place report `risk.country`): the F5
  in front closes the connection WITHOUT a response for a request that
  follows the previous one too closely (2026-10-28). From the Mac: the same
  session (kept-alive connection, or its cookies) 0 or 1.0 s after the
  previous answer is dropped, 2 s answered; from the VPS even new
  connections back to back are dropped, 9 of 10 answered 10 s apart. An
  immediate retry is dropped too. So its requests are spaced (`fetch.Host`
  `spacing` 3 s, `retry_pause` 5 s, one at a time) and the release
  (WorkflowGroups + the published group) is read in the background, so a
  report for a new country pays ONE request. Answers take 0.4 to 1.5 s, and
  once 8.3 s.

## Pattern matching on natural language

Matching uses a **length floor**: substring for patterns of 4 characters or
more, whole word below. Measured on 2525 real alerts, and the measurement
decided:

- substring everywhere: "Flash Flood" became a VOLCANIC alert ("Flash" contains "ash");
- whole words everywhere: the false positive disappeared but **621 real alerts
  were lost** -- "Forestfire", "Thunderstorms", "Rainstorm" are compound or
  inflected forms no whole word finds;
- length floor: zero loss, false positive closed.

Never fix a classification problem by narrowing detection without measuring what
the narrowing costs.

## Frontend

- **Zustand + React 19**: never pass `useStore` a selector that builds a new
  object or array. `useSyncExternalStore` loops forever and the component never
  mounts (blank page, no visible error). Derivations go through `useMemo` on
  stable slices.
- **Severity colour**: reserved "status" palette (`info`, `minor`, `moderate`,
  `severe`, `extreme`). It never distinguishes a series, and it never carries
  meaning alone: a glyph and a label go with it everywhere.
- **MapLibre**: initialization is inside a `try/catch`. Without WebGL the map
  shows a fallback and the feed keeps running.
- **MapLibre CSS lands AFTER ours**: the map is lazy loaded, so
  `maplibregl-map { position: relative }` is injected at runtime on the same
  element as `.map` and wins at equal specificity. The container went to 0 px
  high, the canvas fell back to 400x300, and production showed a black
  rectangle for a month while every test stayed green. The rule is
  `.map-wrap > .map` for that reason: never lower it back to one class. The
  popup had the same defect (`.maplibregl-popup-content { background: #fff }`
  won, white text on white): every `.maplibregl-*` override in `styles.css`
  is scoped under `.map`, and any new one must be too.
- **The map's credit** sits BESIDE the zoom column on the phone layout
  (`margin-right: 64px`): MapLibre stacks it right under the column, and on
  the 100 px landscape map it lay on the zoom-out button. Keep it one tap
  away (the OpenFreeMap / OpenStreetMap licence); `tools/responsive.py`
  fails a viewport where it covers a zoom button or the place report's
  button, closed or open.
- **Forced colours remove every box-shadow**, and THE focus ring is one:
  in Windows High Contrast no focused control showed anything. Its twin is
  an important `outline` in the forced-colors block at the END of
  `styles.css`; a new box-shadow ring needs its outline there too (an inset
  one a negative `outline-offset`), which `forcedFocus.test.ts` enforces.
- **Basemap**: OpenFreeMap (`tiles.openfreemap.org`, no key). CARTO's free
  raster tiles started answering "API KEY REQUIRED" in 2026-09. The host
  `connect-src` in BOTH `deploy/nginx.conf` and the VPS host nginx must name
  the tile host, or the browser blocks every tile. If the style cannot be
  fetched the map falls back to a plain background and still draws the events.
- **Media queries** go at the END of the stylesheet. At equal specificity the
  last rule wins, and a media block placed before the rules it overrides does
  nothing (this broke the whole mobile layout once).

## Operational trap shared with the other SuiteForge products

**Never stop this API by process pattern.** Every product in the suite literally
runs `uvicorn app.main:app`: a `pkill -f "uvicorn app.main:app"` also kills
ScanGithub (`:8894`) and the others on the same machine. That mistake has
already cost a neighbouring session three in-flight GitHub sweeps, and sent it
hunting for the failure inside its own code. Use `make stop-api` (which targets
port 8300) or `lsof -ti tcp:8300 -sTCP:LISTEN | xargs kill` (`-sTCP:LISTEN` is
load-bearing: without it lsof also returns the CLIENT sockets connected to the
port, so the kill hits the browser tab or the Vite proxy instead of the API).

**One `Task.cancel()` does not always arrive.** anyio 4.15.1, under httpx,
swallows a native cancel that lands in the loop step where `connect_tcp`'s
winning attempt cancels its own task group; the source then polls on with
`cancelling()` at 1. A shutdown that cancelled once and gathered with no
timeout held CI for 5 h 25 min (2026-10-28) and kept uvicorn running after
SIGTERM. The lifespan re-cancels what still runs, 10 x 0.5 s: never go back
to a single cancel plus an unbounded gather, here or in a new background
task's owner. Tests must not enter the real lifespan (`TestClient(app)`
without `with`), and `tools/drill.py serve` without `--with-sources` sends
nothing upstream: the suite runs with the network blocked (0 attempts).

## Commands

```bash
make install      # backend venv + npm install
make dev-api      # API on :8300
make dev-web      # UI on :5380
make test         # backend + frontend tests
make lint typecheck
make stop-api     # stop by PORT, never by process pattern
make smoke        # live state of the sources
make up           # docker, UI on :8380
```

## Verification: what counts as "done"

A source is not "implemented" until it has run **against the real API** and
`make smoke` shows it `up` with events seen. A unit test on an invented payload
proves nothing here: the fixtures in `backend/tests/` are verbatim excerpts of
real responses, and that is the only accepted form of fixture in this product.

For a classification or filtering change, "done" also means **measuring the
before/after on real data**: green tests on hand-picked cases say nothing about
recall.
