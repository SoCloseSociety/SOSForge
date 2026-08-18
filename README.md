# SOSForge

[![CI](https://github.com/SoCloseSociety/SOSForge/actions/workflows/ci.yml/badge.svg)](https://github.com/SoCloseSociety/SOSForge/actions/workflows/ci.yml)
[![Live](https://img.shields.io/badge/live-sosforge.soclose.co-0ca30c)](https://sosforge.soclose.co)
[![License MIT](https://img.shields.io/badge/license-MIT-3987e5)](LICENSE)

**Live at <https://sosforge.soclose.co>**

Real-time tracker for earthquakes, tsunamis, volcanoes, cyclones and disaster
alerts. **Nineteen official sources** merged into a single normalized feed and
pushed to the browser over a websocket with a one-second heartbeat. Interface in
five languages.

No API key is required: all nineteen sources are public and open.

![screenshot](docs/screenshot.png)

<sub>On a phone the feed comes first and the map sits below: you read first, you
explore second. [Mobile screenshot](docs/screenshot-mobile.png).</sub>

## The idea

"Live to the second" does not come from hammering one API. It comes from a
**fan-in** of heterogeneous sources, normalized into one event model, and a
**fan-out** over websocket to every client.

```
EMSC websocket (push)      \
USGS GeoJSON (5 s)          \
JMA early warning (5 s)      \
JMA / BMKG / GeoNet           \
INGV / AFAD / GEOFON / CENC    >-- normalize -- horizon -- dedupe -- ring -- hub -- /ws -- UI
NOAA tsunami.gov (30 s)       /
NWS + Meteoalarm + WMO CAP   /
NHC cyclones (300 s)        /
Volcanic-ash SIGMETs       /
GDACS / NASA EONET        /

                                              + one server tick every second
```

EMSC is the only **push** source: it emits an earthquake the moment it is
located, without waiting for a cycle. The others are polled at a rate matched to
how often they actually publish -- polling USGS faster than it regenerates would
return nothing new.

## The nineteen sources (no API key required)

**Worldwide**

| Source | Endpoint | Mode | What it adds |
|---|---|---|---|
| EMSC seismicportal | `wss://www.seismicportal.eu/standing_order/websocket` | push | worldwide earthquakes, within seconds |
| USGS | `earthquake.usgs.gov/.../all_hour.geojson` | poll 5 s | earthquakes + tsunami flag + PAGER alert level |
| GEOFON (GFZ) | `geofon.gfz.de/eqinfo/list.php?fmt=geojson` | poll 60 s | third global catalogue: dedup becomes a **three-way vote** |
| NOAA NTWC / PTWC | `tsunami.gov/events/xml/PAAQAtom.xml`, `PHEBAtom.xml` | poll 30 s | tsunami bulletins (Information / Watch / Advisory / Warning) |
| GDACS | `gdacs.org/xml/rss.xml` | poll 120 s | cyclones, floods, fires, volcanoes, droughts, with a green/orange/red level |
| Volcanic-ash SIGMETs | `aviationweather.gov/api/data/isigmet?hazard=VA` | poll 180 s | **structured volcanic ash, worldwide** -- the only machine-readable equivalent to the VAACs |
| NHC | `nhc.noaa.gov/CurrentStorms.json` | poll 300 s | Atlantic and Pacific tropical cyclones: position, winds, category, advisory |
| NASA EONET | `eonet.gsfc.nasa.gov/api/v3/events` | poll 600 s | ongoing natural events seen from space: **wildfires tracked as events** |
| WMO CAP aggregate | `severeweather.wmo.int/json/wmo_all.json` | poll 300 s | official alerts for the rest of the world in a single call |

**National and regional** -- they are not here for redundancy. Each one adds
something EMSC does not have.

| Source | Endpoint | Mode | What it adds |
|---|---|---|---|
| JMA early warning | `api.wolfx.jp/jma_eew.json` | poll 5 s | **the only feed issued WHILE the waves are still travelling** |
| JMA (Japan) | `jma.go.jp/bosai/quake/data/list.json` | poll 45 s | **shindo**, the intensity felt at ground level -- what matters in Japan, and it exists nowhere else |
| BMKG (Indonesia) | `data.bmkg.go.id/DataMKG/TEWS/gempaterkini.json` | poll 60 s | the **official Indonesian tsunami-potential flag**, published before the PTWC bulletins |
| CENC (China) | `api.wolfx.jp/cenc_eqlist.json` | poll 120 s | mainland China, with no other coverage here |
| GeoNet (New Zealand) | `api.geonet.org.nz/quake?MMI=3` | poll 60 s | very low local detection threshold on a very active zone |
| INGV (Italy) | `webservices.ingv.it/fdsnws/event/1/query` | poll 60 s | same, in the standard FDSN format (the same contract as USGS) |
| AFAD (Turkey) | `deprem.afad.gov.tr/apiv2/event/filter` | poll 60 s | fine coverage of the North Anatolian fault |
| NWS (USA) | `api.weather.gov/alerts/active` | poll 20 s | floods, tornadoes, heat, tsunami on the American side |
| USGS HANS + Smithsonian | `volcanoes.usgs.gov/hans-public/api/...` | poll 300 s | US volcanoes on alert (aviation colour code), located via the GVP catalogue |
| Meteoalarm (Europe) | `feeds.meteoalarm.org/api/v1/warnings/feeds-{country}` | poll 300 s | national weather warnings across ten European countries |

### One source is of a different nature: early warning

The other eighteen publish **after** the fact: an earthquake happened, an agency
located it, we display it. Japan's earthquake early warning is emitted **while
the waves are propagating**, seconds after the nearest stations detect them. It
is the only information in this product that can still be used to take cover.

**Stated reserve.** JMA and CENC do not expose an open API, so we go through the
**unofficial third-party relay** Wolfx. It is a best-effort source: it enriches,
it is authoritative over nothing, and its failure breaks nothing. Its websockets
reject non-browser clients (403 Cloudflare), hence the polling. Turn it off with
`SOS_ENABLE_JMA_EEW=false`.

## Getting started

```bash
cp .env.example .env      # nothing to fill in: everything is public
make install              # backend venv + npm install
make dev-api              # terminal 1 -- API on :8300
make dev-web              # terminal 2 -- UI on :5273
```

With Docker: `make up`, then <http://localhost:8380>.

## Deployment

The service runs on the SoClose helper VPS behind the host nginx, which carries
TLS and the domain name; the container itself only listens on the loopback.

```bash
ssh helper-vps
cd /root/SAAS/SuiteForge/SOSForge && git pull && docker compose up -d --build
```

| | |
|---|---|
| Path on the server | `/root/SAAS/SuiteForge/SOSForge` |
| Internal port | `127.0.0.1:8380` (host nginx proxies to it) |
| Vhost | `/etc/nginx/sites-available/sosforge.soclose.co` |
| Certificate | `certbot certonly --webroot -w /var/www/html -d sosforge.soclose.co` |
| Data | docker volume `sos-data` (JSONL journal, purged after 7 days) |

Security headers live on the host: a restrictive CSP (the site loads only its own
code, CARTO tiles and its websocket), `nosniff`, HSTS, and a permanent redirect
from HTTP to HTTPS.

## API

| Route | What it returns |
|---|---|
| `GET /healthz` | service state, connected clients, ingested events |
| `GET /api/events` | recent feed. Filters: `limit`, `kind`, `min_magnitude`, `hours`, `primary_only` |
| `GET /api/events/{id}` | one event with its raw source payload |
| `GET /api/events/{id}/nearby` | live views of the area: deep links + webcams |
| `GET /api/geocode?q=` | area search (Nominatim proxy, rate-limited and cached) |
| `GET /api/stats` | counters for the last hour |
| `GET /api/sources` | health of every source (connected, events seen, last error) |
| `WS /ws` | snapshot on connect, then `event` / `update` / `tick` (1/s) |

Websocket messages:

```jsonc
{"type": "snapshot", "events": [...], "stats": {...}, "sources": [...]}
{"type": "event",    "event": {...}, "primary": true, "breaking": true}   // new event
{"type": "update",   "event": {...}, "primary": true, "breaking": false}  // revision of a known event
{"type": "tick",     "server_time": "...", "stats": {...}}                // heartbeat, every second
```

## The interface

- **Time window**: Live (15 min), 1 h, 6 h, 24 h, All. It is the first question
  anyone asks in front of a tracker, so it is the first control on the page.
- **Five languages** (English, French, Spanish, Japanese, Indonesian), detected
  from the browser. The last two are not decorative: they are the populations
  most exposed to earthquakes and tsunamis, and exactly the ones the JMA and
  BMKG sources serve.
- **Every hazard type is always listed**, even at zero. Showing only the types
  with current events made it look like the product did not cover tsunamis on
  the days there were none -- on an emergency tracker, "0 tsunami alerts" is
  information, not an absence of information.
- **Country flag** on every event, resolved server-side. No identifiable country
  (open sea)? A globe, never an approximate flag.
- **Area search**: typing a name filters the feed instantly (place, title,
  country) *and* offers to fly the map to that area even if nothing is happening
  there -- the most useful case in practice. Geocoding goes through the backend,
  which honours the one-request-per-second rate Nominatim requires.
- **Shareable link**: every event has its own URL (`#e/<id>`). Without it you
  could not say "look at THIS earthquake" -- the recipient landed on a feed that
  had already moved on.
- **Filters are remembered**: window, types and magnitude survive a reload. The
  text search is not: finding an invisible filter hiding the whole feed would be
  disorienting.
- **Click an event**: the map dives close to the area, and a card opens **live
  views** -- Windy webcams, YouTube live search, today's NASA Worldview imagery,
  satellite view. Those links work with no key at all; with a Windy key
  (`SOS_WINDY_API_KEY`), the real list of nearby public webcams appears with
  thumbnails.

## What the system handles explicitly

- **Revisions.** EMSC and USGS revise their solutions within minutes. A known
  event whose fingerprint changes becomes an `update`, not a duplicate. The UI
  marks the row "revised".
- **Cross-source dedup.** The same earthquake arrives under several identifiers
  (EMSC, USGS, GEOFON...). They are grouped into a cluster: 90 s, 250 km and 1.2
  magnitude points apart at most. Nothing is deleted, one representative is shown.
- **Wave propagation.** For a magnitude 4+ earthquake less than six minutes old,
  the map draws both wave fronts live: **P** at 6 km/s (first shaking) and **S**
  at 3.5 km/s (the damaging one). This is the only thing in the interface that
  shows **where the shaking is arriving right now**. Average crustal speeds, so
  accurate near the epicenter and approximate far away -- the circles stop at
  1200 km, before they would start lying.
- **GDACS noise.** The full feed is ~400 entries, of which ~344 are green
  wildfires and droughts open for a year. Filter: orange and red always, green
  only if freshly published (`SOS_GDACS_MAX_AGE_DAYS`).
- **Alert volume.** Meteoalarm and the WMO aggregate together emit ~4300
  bulletins per cycle, mostly routine rain and heat. Severity thresholds keep
  orange-and-above for Europe and the top tier for the WMO. Measured reserve: the
  WMO scale is **not** consistent between countries.
- **"No danger" bulletins.** A tsunami bulletin of category Information usually
  says "there is NO tsunami danger": it is displayed, but it does not raise the
  tsunami flag and does not trigger the sound.
- **A warning's advance notice.** A weather warning is published BEFORE it
  starts -- that is its whole point. Its timestamp is legitimately in the future,
  so the future-timestamp filter exempts it when the source declares it ongoing.
  An earthquake cannot be dated in advance.
- **Timestamps in the future.** A source with a drifting clock produced events of
  negative age: past the horizon, permanently announced as "live", and pinned at
  the top of a date-sorted feed. Beyond two minutes of lead, rejected.
- **Archives versus current events.** Several sources serve a catalogue, not a
  feed: the JMA list goes back more than nine months. An ingestion horizon
  (`SOS_MAX_EVENT_AGE_DAYS`) discards them, with one nuance that matters: a
  severe **and ongoing** alert (a red cyclone) survives, a past earthquake does
  not -- an earthquake is instantaneous, it does not "last".
- **Finished alerts.** An ongoing alert (EONET fire, NHC cyclone, current GDACS
  alert) escapes the horizon as long as its source publishes it. A sweep removes
  the ones no source has mentioned for six hours: a source going silent has
  implicitly said it is over.
- **Journal retention.** The JSONL journal grows by about 5 MB per day. A sweep
  deletes those older than `SOS_JOURNAL_KEEP_DAYS`: on a service that runs
  continuously, nobody watches a disk filling up.
- **Background tasks that die.** The one-second heartbeat and the sweep catch
  their exceptions. A single uncaught error killed the task for good: no more
  ticks, every client reconnecting, and `/healthz` answering "ok" throughout.
- **A dead connection.** A websocket can stay "open" and deliver nothing. The
  one-second server tick is the proof of life: 15 s of silence and the client
  reconnects instead of showing a frozen feed while claiming it is live. A server
  that accepts the connection and then says nothing is closed too -- otherwise
  the very first connection could hang forever, announcing "LIVE".
- **A slow client.** Its queue is bounded; if it cannot keep up it is dropped
  rather than slowing down ingestion, and its socket is closed rather than left
  silently open.
- **A failing source.** A source whose every feed fails cannot show as green. The
  footer shows the real state of all nineteen.
- **No WebGL.** The map degrades cleanly and the alert feed keeps running.

## Verification

```bash
make test        # 95 backend tests + 77 frontend tests
make lint
make typecheck   # tsc + mypy
make smoke       # live state of the nineteen sources
```

Backend fixtures are **verbatim** excerpts of real responses. A unit test on an
invented payload proves nothing here: this project has been bitten by
hand-written fixtures that made a working parser look broken.

## Design decisions

**No database in v1.** A live tracker needs the last hour, not a warehouse: an
in-memory ring buffer (5000 events) plus a daily JSONL journal for audit and
replay. `EventStore` is the single storage point -- Postgres can be plugged in
behind the same interface the day history becomes a product requirement.

**Severity is a "status" palette, not a series palette.** Five levels, reserved
colours, and **never colour alone**: every level also carries a glyph and a
label, so it stays readable with colour-vision deficiency and in black and white.

**Deliberately left out, after checking.** FIRMS and EFFIS (fires): about 3 h of
NRT latency and no event identifier -- "fire to the second" does not exist
anywhere. SeedLink and Raspberry Shake: waveforms, not events. GloFAS and
Copernicus EMS: key required, and GDACS already republishes the essentials. The
BOM (Australia) API works but its own payload forbids reuse.

## Sources and attribution

Data: EMSC/CSEM, USGS, NOAA (NWS, NTWC, PTWC, NHC, Aviation Weather Center),
GDACS (European Commission and UN), Smithsonian Institution Global Volcanism
Program, JMA (Japan), BMKG (Indonesia), CENC (China), GNS Science / GeoNet (New
Zealand), INGV (Italy), AFAD (Turkey), GFZ GEOFON, NASA EONET, Meteoalarm, WMO.
Basemap by OpenStreetMap and CARTO. These feeds are public; they belong to their
producers.

**SOSForge is not an official warning service.** In a real emergency, your local
civil protection authority is the reference.
