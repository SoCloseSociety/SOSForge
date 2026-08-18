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
  timeout is mandatory.
- **tsunami.gov**: the category (Information / Watch / Advisory / Warning) and
  the magnitude live in the `summary` HTML, served sometimes escaped and
  sometimes as real elements (which then come back prefixed by the Atom
  namespace). So we regex on the **stripped** text, never on the markup. PHEB's
  `link rel="self"` wrongly points at PAAQ: do not trust it.
- **NWS**: identifying User-Agent mandatory; `limit` returns 400 on
  `/alerts/active`; `geometry` is often `null` (UGC zones) -- the alert must stay
  usable without a position.
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
  cancelled EEW must disappear.
- **AFAD**: `date` is **UTC** (proven by cross-checking EMSC). `limit` truncates
  **before** sorting, so `orderby=timedesc&limit=3` returns the three OLDEST of
  the window: use a short window, a large limit, and sort client-side. Answers
  302 before its JSON.
- **Meteoalarm**: `awareness_level` is a composite string ("1; green; Minor");
  `responseType: AllClear` means the warning is LIFTED; every warning carries its
  content twice (local language + English) and must yield ONE event.
- **WMO**: `s`/`u`/`c` are CAP ranks (1 = most severe), and the scale is **not**
  homogeneous between countries.

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
- **Media queries** go at the END of the stylesheet. At equal specificity the
  last rule wins, and a media block placed before the rules it overrides does
  nothing (this broke the whole mobile layout once).

## Operational trap shared with the other SuiteForge products

**Never stop this API by process pattern.** Every product in the suite literally
runs `uvicorn app.main:app`: a `pkill -f "uvicorn app.main:app"` also kills
ScanGithub (`:8894`) and the others on the same machine. That mistake has
already cost a neighbouring session three in-flight GitHub sweeps, and sent it
hunting for the failure inside its own code. Use `make stop-api` (which targets
port 8300) or `lsof -ti tcp:8300 | xargs kill`.

## Commands

```bash
make install      # backend venv + npm install
make dev-api      # API on :8300
make dev-web      # UI on :5273
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
