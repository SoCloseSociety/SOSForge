"""`history.earthquakes`, `.significant_earthquakes`, `.tsunamis`,
`.eruptions`, `.volcanoes`: what the catalogues hold around the place.

A catalogue's `as_of` is "records through": the newest entry the WHOLE
catalogue held WHEN THIS CELL WAS FETCHED, stored with the cell. A fresher
probe never relabels an older cell as "records through today".

Cells are 0.1 deg, queried around the cell centre with a radius (or a box)
widened by the cell's half-diagonal, so every requester in the cell finds
its own events; distances and bearings are then measured from the
requester's own point. Rows are kept packed (Tokyo alone has 6247 USGS rows
within 310 km) and the caches are bounded in bytes.

Traps from the captures:

- USGS: `eventtype=earthquake` is asked AND rows of another type are
  dropped (the Kathmandu capture holds a "landslide" row). `place` text is
  mojibake for some rows: distance and bearing come from the coordinates.
  Rows of net `iscgem`/`iscgemsup` are ISC-GEM's (CC BY-SA 3.0): the block
  then carries that attribution and is shared alike (owner decision).
- NCEI tsunamis: `deaths` is the tsunami's own, `deathsTotal` includes the
  linked earthquake (Kanto 1923: 325 vs 105385). `eventValidity` -1..2 is
  excluded and counted. Runups are scoped to min(radius_km, 100) km.
- NCEI volcanoes: `eruption: false` rows are dropped; `publish` is never a
  filter (false on every volcano row captured).
- GVP: `Last_Eruption_Year` is null for unknown and negative for BCE.
"""

from __future__ import annotations

import csv
import io
import math
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np

from app.place import block as B
from app.place import fetch, geo
from app.place.context import Context
from app.place.weather import serve

CELL = 0.1
HALF_DIAGONAL_KM = 8.0  # of a 0.1 deg cell, at the equator (7.9 km)
USGS_RADIUS_KM = 310.0
RUNUP_KM = 100.0
NCEI_MAX_PAGES = 25

SRC_USGS = B.Source(
    "USGS ANSS Comprehensive Catalog (ComCat)",
    "https://earthquake.usgs.gov/fdsnws/event/1/",
    "U.S. Geological Survey, ANSS Comprehensive Earthquake Catalog; distances, bearings "
    "and decade counts computed by SOSForge",
    "US Government work, public domain",
)
SRC_ISCGEM = B.Source(
    "ISC-GEM Global Instrumental Earthquake Catalogue",
    "http://www.isc.ac.uk/iscgem/",
    "International Seismological Centre, ISC-GEM Global Instrumental Earthquake Catalogue, "
    "rows relayed by USGS ComCat (net iscgem)",
    "CC BY-SA 3.0",
)
SRC_NCEI_EQ = B.Source(
    "NOAA NCEI Global Significant Earthquake Database",
    "https://www.ngdc.noaa.gov/hazel/view/hazards/earthquake/search",
    "National Geophysical Data Center / World Data Service: NCEI/WDS Global Significant "
    "Earthquake Database. NOAA National Centers for Environmental Information. "
    "doi:10.7289/V5TD9V7K",
    "US Government work, public domain",
)
SRC_NCEI_TS = B.Source(
    "NOAA NCEI Global Historical Tsunami Database",
    "https://www.ngdc.noaa.gov/hazel/view/hazards/tsunami/event-search",
    "National Geophysical Data Center / World Data Service: NCEI/WDS Global Historical "
    "Tsunami Database. NOAA National Centers for Environmental Information. doi:10.7289/V5PN93H7",
    "US Government work, public domain",
)
SRC_NCEI_VO = B.Source(
    "NOAA NCEI Significant Volcanic Eruptions Database",
    "https://www.ngdc.noaa.gov/hazel/view/hazards/volcano/event-search",
    "National Geophysical Data Center / World Data Service: NCEI/WDS Global Significant "
    "Volcanic Eruptions Database. NOAA National Centers for Environmental Information. "
    "doi:10.7289/V5JW8BSH",
    "US Government work, public domain",
)
SRC_GVP = B.Source(
    "Smithsonian Global Volcanism Program, Volcanoes of the World",
    "https://volcano.si.edu/",
    "Global Volcanism Program, Volcanoes of the World. Distributed by Smithsonian "
    "Institution, compiled by Venzke, E.",
    "Smithsonian GVP terms of use, citation required",
)

SPEC_EQ = B.Spec("history.earthquakes", "catalogue", "through", 2 * 86400.0, SRC_USGS)
SPEC_SIG = B.Spec("history.significant_earthquakes", "catalogue", "through", None, SRC_NCEI_EQ)
SPEC_TS = B.Spec("history.tsunamis", "catalogue", "through", None, SRC_NCEI_TS)
SPEC_ERUPT = B.Spec("history.eruptions", "catalogue", "through", None, SRC_NCEI_VO)
SPEC_VOLC = B.Spec("history.volcanoes", "catalogue", "through", None, SRC_GVP)

_history_bytes = 64 * 1024 * 1024


def configure(cache_mb: float) -> None:
    global _history_bytes
    _history_bytes = int(cache_mb * 1024 * 1024)
    for cache in (_usgs_cache, _ncei_cache, _gvp_cache):
        cache.max_bytes = _history_bytes


_usgs_cache = fetch.Cache("earthquake.usgs.gov", max_bytes=_history_bytes)
_ncei_cache = fetch.Cache("www.ngdc.noaa.gov", max_bytes=_history_bytes)
_gvp_cache = fetch.Cache("webservices.volcano.si.edu", max_bytes=_history_bytes)
_probe_cache = fetch.Cache("catalogue probes", 16)


def cell_of(ctx: Context) -> tuple[float, float]:
    return geo.cell_key(ctx.lat, ctx.lon, CELL)


def decades(years: list[int], now_year: int, early_before: int) -> list[dict[str, Any]]:
    counts = Counter((y // 10) * 10 for y in years)
    current = (now_year // 10) * 10
    return [
        {
            "decade": d,
            "count": counts[d],
            "early": d < early_before,
            "partial": d == current,
        }
        for d in sorted(counts)
    ]


async def _probe(key: str, url: str, parse: Any, ctx: Context, ttl: float) -> datetime:
    async def load(previous: fetch.Entry) -> fetch.Loaded:
        response = await fetch.get(url)
        return fetch.Loaded(parse(response), ttl)

    entry = await fetch.obtain(_probe_cache, key, load, ctx.claim)
    if entry.value is None:
        assert entry.error is not None
        raise entry.error
    value: datetime = entry.value
    return value


# ---------------------------------------------------------------------- USGS

USGS_QUERY = (
    "https://earthquake.usgs.gov/fdsnws/event/1/query?format=csv&eventtype=earthquake"
    "&latitude={lat:.2f}&longitude={lon:.2f}&maxradiuskm={km:g}&minmagnitude=4.5"
    "&starttime=1900-01-01&orderby=time"
)
USGS_NEWEST = (
    "https://earthquake.usgs.gov/fdsnws/event/1/query?format=csv&eventtype=earthquake"
    "&minmagnitude=4.5&orderby=time&limit=1"
)
ISCGEM_NETS = frozenset({"iscgem", "iscgemsup"})


@dataclass
class Quakes:
    """Packed rows: one numpy array per numeric column."""

    t: np.ndarray  # epoch seconds (negative before 1970)
    lat: np.ndarray
    lon: np.ndarray
    mag: np.ndarray
    depth: np.ndarray
    ids: list[str]
    times: list[str]  # the catalogue's own ISO text, milliseconds kept
    mag_type: list[str]
    net: list[str]
    through: datetime  # the newest event ComCat held when this cell was fetched

    @property
    def nbytes(self) -> int:
        arrays = self.t.nbytes + self.lat.nbytes + self.lon.nbytes + self.mag.nbytes
        return arrays + self.depth.nbytes + 80 * len(self.ids)


def parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def parse_usgs(text: str, through: datetime) -> Quakes:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or "time" not in reader.fieldnames:
        if not text.strip():
            # an empty answer is a header-less 204 body: no event
            empty = np.zeros(0)
            return Quakes(empty, empty, empty, empty, empty, [], [], [], [], through)
        raise fetch.FetchError("upstream_parse", "earthquake.usgs.gov CSV without a header")
    cols: dict[str, list[Any]] = {k: [] for k in ("t", "lat", "lon", "mag", "depth")}
    ids, times, types, nets = [], [], [], []
    for row in reader:
        if (row.get("type") or "earthquake") != "earthquake":
            continue
        lat, lon, mag = (
            B.number(row.get("latitude")),
            B.number(row.get("longitude")),
            B.number(row.get("mag")),
        )
        if lat is None or lon is None or mag is None:
            continue
        try:
            when = parse_time(row["time"])
        except (KeyError, ValueError):
            continue
        depth = B.number(row.get("depth"))
        cols["t"].append(when.timestamp())
        cols["lat"].append(lat)
        cols["lon"].append(lon)
        cols["mag"].append(mag)
        cols["depth"].append(depth if depth is not None else math.nan)
        ids.append(row.get("id") or "")
        times.append(row["time"])
        types.append(row.get("magType") or "")
        nets.append(row.get("net") or "")
    return Quakes(
        np.asarray(cols["t"]),
        np.asarray(cols["lat"]),
        np.asarray(cols["lon"]),
        np.asarray(cols["mag"]),
        np.asarray(cols["depth"]),
        ids,
        times,
        types,
        nets,
        through,
    )


def _usgs_newest(response: Any) -> datetime:
    rows = list(csv.DictReader(io.StringIO(response.text)))
    if not rows:
        raise fetch.FetchError("upstream_parse", "earthquake.usgs.gov newest-event probe empty")
    return parse_time(rows[0]["time"])


def _distances(lat: float, lon: float, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    p1 = math.radians(lat)
    p2 = np.radians(lats)
    dp = p2 - p1
    dl = np.radians(lons - lon)
    h = np.sin(dp / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * geo.EARTH_KM * np.arcsin(np.minimum(1.0, np.sqrt(h)))


def quake_block(q: Quakes, ctx: Context) -> B.Block:
    km = _distances(ctx.lat, ctx.lon, q.lat, q.lon) if len(q.ids) else np.zeros(0)
    inside = np.nonzero(km <= ctx.radius_km)[0]

    def row(i: int) -> dict[str, Any]:
        depth = float(q.depth[i])
        return {
            "id": q.ids[i],
            "time": q.times[i],
            "mag": round(float(q.mag[i]), 2),
            "mag_type": q.mag_type[i] or None,
            "depth_km": None if math.isnan(depth) else round(depth, 1),
            "distance_km": round(float(km[i]), 1),
            "bearing_deg": round(
                geo.bearing_deg(ctx.lat, ctx.lon, float(q.lat[i]), float(q.lon[i]))
            )
            % 360,
            "url": f"https://earthquake.usgs.gov/earthquakes/eventpage/{q.ids[i]}",
        }

    by_mag = sorted(inside, key=lambda i: (-q.mag[i], -q.t[i]))
    by_time = sorted(inside, key=lambda i: -q.t[i])
    years = [datetime.fromtimestamp(float(q.t[i]), UTC).year for i in inside]
    data = {
        "radius_km": int(ctx.radius_km) if ctx.radius_km.is_integer() else ctx.radius_km,
        "min_magnitude": 4.5,
        "since_year": 1900,
        "total": len(inside),
        "by_decade": decades(years, ctx.now.year, 1980),
        "strongest": [row(int(i)) for i in by_mag[:10]],
        "latest": [row(int(i)) for i in by_time[:5]],
        "derived": ["distance_km", "bearing_deg", "url", "by_decade"],
    }
    source = SRC_USGS
    if any(q.net[i] in ISCGEM_NETS for i in inside):
        source = B.Source(
            SRC_USGS.name,
            SRC_USGS.url,
            SRC_USGS.attribution,
            "US Government work (ComCat rows); this block includes ISC-GEM rows and is "
            "shared under CC BY-SA 3.0",
            also=(SRC_ISCGEM,),
        )
    return B.ok(SPEC_EQ, q.through, data, source=source)


async def build_earthquakes(ctx: Context) -> B.Block:
    clat, clon = cell_of(ctx)
    url = USGS_QUERY.format(lat=clat, lon=clon, km=USGS_RADIUS_KM)

    async def load(previous: fetch.Entry) -> fetch.Loaded:
        through = await _probe("usgs", USGS_NEWEST, _usgs_newest, ctx, 3600.0)
        response = await fetch.get(url, accept=(200, 204))
        quakes = await fetch.in_thread(parse_usgs, response.text, through)
        return fetch.Loaded(quakes, 86400.0, size=quakes.nbytes)

    entry = await fetch.obtain(_usgs_cache, (clat, clon), load, ctx.claim)
    return serve(SPEC_EQ, entry, lambda q: quake_block(q, ctx))


# ---------------------------------------------------------------------- NCEI

NCEI = "https://www.ngdc.noaa.gov/hazel/hazard-service/api/v1"
NCEI_NEWEST = "{base}/{endpoint}?itemsPerPage=3&order=year:desc&order=month:desc&order=day:desc"
# NCEI's code lists, from its OpenAPI description (captured 2026-10-27)
CAUSES = {
    0: "unknown",
    1: "earthquake",
    2: "questionable_earthquake",
    3: "earthquake_and_landslide",
    4: "volcano_and_earthquake",
    5: "volcano_earthquake_and_landslide",
    6: "volcano",
    7: "volcano_and_landslide",
    8: "landslide",
    9: "meteorological",
    10: "explosion",
    11: "astronomical_tide",
}
VALIDITY = {
    -1: "erroneous",
    0: "none",
    1: "very_doubtful",
    2: "questionable",
    3: "probable",
    4: "definite",
}
KEEP_VALIDITY = 3


@dataclass
class Rows:
    items: list[dict[str, Any]]
    through: datetime
    total: int
    complete: bool

    @property
    def nbytes(self) -> int:
        return 400 * len(self.items)


def entry_date(item: dict[str, Any]) -> dict[str, int | None]:
    def part(key: str) -> int | None:
        v = B.number(item.get(key))
        return int(v) if v is not None else None

    return {"year": part("year"), "month": part("month"), "day": part("day")}


def _ncei_newest(response: Any) -> datetime:
    items = (response.json() or {}).get("items") or []
    if not items:
        raise fetch.FetchError("upstream_parse", "www.ngdc.noaa.gov newest-entry probe empty")
    d = items[0]
    return datetime(
        int(d["year"]),
        int(d.get("month") or 1),
        int(d.get("day") or 1),
        int(d.get("hour") or 0),
        int(d.get("minute") or 0),
        tzinfo=UTC,
    )


def ncei_boxes(lat: float, lon: float, km: float) -> list[tuple[float, float, float, float]]:
    """(minLat, maxLat, minLon, maxLon) boxes covering `km` around a point,
    clamped at the poles and split at the antimeridian."""
    south, north, west, east = geo.box(lat, lon, km)
    return [(south, north, w, e) for w, e in geo.split_lon(west, east)]


async def ncei_rows(
    endpoint: str, probe_endpoint: str, clat: float, clon: float, km: float, ctx: Context
) -> fetch.Entry:
    key = (endpoint, clat, clon, km)

    async def load(previous: fetch.Entry) -> fetch.Loaded:
        through = await _probe(
            f"ncei:{probe_endpoint}",
            NCEI_NEWEST.format(base=NCEI, endpoint=probe_endpoint),
            _ncei_newest,
            ctx,
            86400.0,
        )
        items: list[dict[str, Any]] = []
        total = 0
        complete = True
        for s, n, w, e in ncei_boxes(clat, clon, km + HALF_DIAGONAL_KM):
            page = 1
            while True:
                url = (
                    f"{NCEI}/{endpoint}?minLatitude={s:.3f}&maxLatitude={n:.3f}"
                    f"&minLongitude={w:.3f}&maxLongitude={e:.3f}&itemsPerPage=200&page={page}"
                )
                payload = (await fetch.get(url)).json()
                if not isinstance(payload, dict) or "items" not in payload:
                    raise fetch.FetchError(
                        "upstream_parse", "www.ngdc.noaa.gov answer without items"
                    )
                items.extend(payload["items"])
                pages = int(payload.get("totalPages") or 1)
                if page == 1:
                    total += int(payload.get("totalItems") or 0)
                if page >= pages:
                    break
                if page >= NCEI_MAX_PAGES:
                    complete = False
                    break
                page += 1
        rows = Rows(items, through, total, complete)
        return fetch.Loaded(rows, 7 * 86400.0, size=rows.nbytes)

    return await fetch.obtain(_ncei_cache, key, load, ctx.claim)


def _within(
    items: list[dict[str, Any]], ctx: Context, km: float
) -> list[tuple[float, dict[str, Any]]]:
    out = []
    for item in items:
        lat, lon = B.number(item.get("latitude")), B.number(item.get("longitude"))
        if lat is None or lon is None:
            continue
        d = geo.haversine_km(ctx.lat, ctx.lon, lat, lon)
        if d <= km:
            out.append((d, item))
    return out


def _n(item: dict[str, Any], key: str) -> float | None:
    return B.number(item.get(key))


def _int(item: dict[str, Any], key: str) -> int | None:
    v = B.number(item.get(key))
    return int(v) if v is not None else None


def _impact(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "deaths": _int(item, "deaths"),
        "deaths_order": _int(item, "deathsAmountOrder"),
        "deaths_total": _int(item, "deathsTotal"),
        "deaths_total_order": _int(item, "deathsAmountOrderTotal"),
        "damage_musd": _n(item, "damageMillionsDollars"),
        "damage_order": _int(item, "damageAmountOrder"),
    }


def _or_low(value: float | None) -> float:
    return -1.0 if value is None else value


def _rank(item: dict[str, Any], strength: str) -> tuple[float, float, float]:
    """Own deaths, then their order of magnitude, then strength: an event
    with no death figure sorts after every event with one."""
    return (
        _or_low(_n(item, "deaths")),
        _or_low(_n(item, "deathsAmountOrder")),
        _or_low(_n(item, strength)),
    )


def significant_block(rows: Rows, ctx: Context) -> B.Block:
    found = _within(rows.items, ctx, ctx.radius_km)
    found.sort(key=lambda p: _rank(p[1], "eqMagnitude"), reverse=True)
    events = [
        {
            "date": entry_date(item),
            "mag": _n(item, "eqMagnitude"),
            "vei": None,
            "name": item.get("locationName"),
            **_impact(item),
            "tsunami": bool(item.get("tsunamiEventId")),
            "distance_km": round(d),
        }
        for d, item in found[:10]
    ]
    missing = [] if rows.complete else [{"field": "events", "reason": "not_in_source"}]
    data = {"total": len(found), "events": events, "derived": ["distance_km"]}
    return B.ok(SPEC_SIG, rows.through, data, missing=missing)


def eruption_block(rows: Rows, ctx: Context) -> B.Block:
    kept = [item for item in rows.items if item.get("eruption") is True]
    found = _within(kept, ctx, ctx.radius_km)
    found.sort(key=lambda p: _rank(p[1], "vei"), reverse=True)
    events = [
        {
            "date": entry_date(item),
            "mag": None,
            "vei": _int(item, "vei"),
            "name": item.get("name"),
            **_impact(item),
            "tsunami": bool(item.get("tsunamiEventId")),
            "distance_km": round(d),
        }
        for d, item in found[:10]
    ]
    data = {"total": len(found), "events": events, "derived": ["distance_km"]}
    return B.ok(SPEC_ERUPT, rows.through, data)


def tsunami_block(events: Rows, runups: Rows, ctx: Context) -> B.Block:
    found = _within(events.items, ctx, ctx.radius_km)
    kept = [(d, i) for d, i in found if (_int(i, "eventValidity") or 0) >= KEEP_VALIDITY]
    kept.sort(
        key=lambda p: (
            _n(p[1], "deaths") if _n(p[1], "deaths") is not None else -1.0,
            _n(p[1], "maxWaterHeight") if _n(p[1], "maxWaterHeight") is not None else -1.0,
        ),
        reverse=True,
    )
    names = {i.get("id"): i.get("locationName") for i in events.items}
    reach = min(ctx.radius_km, RUNUP_KM)
    near = _within(runups.items, ctx, reach)
    near.sort(key=lambda p: _n(p[1], "runupHt") or -1.0, reverse=True)
    data = {
        "sources": {
            "total": len(found),
            "kept": len(kept),
            "excluded_doubtful": len(found) - len(kept),
            "events": [
                {
                    "date": entry_date(item),
                    "cause": CAUSES.get(_int(item, "causeCode") or -99),
                    "validity": VALIDITY.get(_int(item, "eventValidity") or -99),
                    "max_height_m": _n(item, "maxWaterHeight"),
                    # the tsunami's own deaths; deaths_total includes the linked earthquake
                    "deaths": _int(item, "deaths"),
                    "deaths_total": _int(item, "deathsTotal"),
                    "distance_km": round(d),
                    "name": item.get("locationName"),
                }
                for d, item in kept[:10]
            ],
        },
        "runups": {
            "within_km": int(reach) if float(reach).is_integer() else reach,
            "total": len(near),
            "top": [
                {
                    "date": entry_date(item),
                    "location": item.get("locationName"),
                    "height_m": _n(item, "runupHt"),
                    "distance_km": round(d),
                    "source_name": names.get(item.get("tsunamiEventId")),
                }
                for d, item in near[:10]
            ],
        },
        "derived": ["distance_km"],
    }
    missing = []
    if not (events.complete and runups.complete):
        missing.append({"field": "sources", "reason": "not_in_source"})
    return B.ok(SPEC_TS, min(events.through, runups.through), data, missing=missing)


async def build_significant(ctx: Context) -> B.Block:
    clat, clon = cell_of(ctx)
    entry = await ncei_rows("earthquakes", "earthquakes", clat, clon, 300.0, ctx)
    return serve(SPEC_SIG, entry, lambda rows: significant_block(rows, ctx), ttl_s=7 * 86400.0)


async def build_eruptions(ctx: Context) -> B.Block:
    clat, clon = cell_of(ctx)
    entry = await ncei_rows("volcanoes", "volcanoes", clat, clon, 300.0, ctx)
    return serve(SPEC_ERUPT, entry, lambda rows: eruption_block(rows, ctx), ttl_s=7 * 86400.0)


async def build_tsunamis(ctx: Context) -> B.Block:
    clat, clon = cell_of(ctx)
    events = await ncei_rows("tsunamis/events", "tsunamis/events", clat, clon, 300.0, ctx)
    runups = await ncei_rows("tsunamis/runups", "tsunamis/events", clat, clon, RUNUP_KM, ctx)
    if runups.value is None:
        error = runups.error or fetch.FetchError("upstream_parse", "no runups")
        return B.unavailable(SPEC_TS, error.code, error.reason)
    return serve(
        SPEC_TS, events, lambda rows: tsunami_block(rows, runups.value, ctx), ttl_s=7 * 86400.0
    )


# ----------------------------------------------------------------------- GVP

GVP = "https://webservices.volcano.si.edu/geoserver/GVP-VOTW/ows"
GVP_VOLCANOES = (
    GVP + "?service=WFS&version=1.0.0&request=GetFeature&outputFormat=application%2Fjson"
    "&typeName=GVP-VOTW%3ASmithsonian_VOTW_Holocene_Volcanoes&bbox={w:.3f}%2C{s:.3f}%2C{e:.3f}"
    "%2C{n:.3f}&propertyName=Volcano_Number%2CVolcano_Name%2CPrimary_Volcano_Type%2C"
    "Last_Eruption_Year%2CCountry%2CLatitude%2CLongitude%2CElevation%2CEvidence_Category"
)
GVP_CONTINUING = (
    GVP + "?service=WFS&version=1.0.0&request=GetFeature"
    "&typeName=GVP-VOTW:Smithsonian_VOTW_Holocene_Eruptions&outputFormat=application/json"
    "&CQL_FILTER=EndDateYearModifier%3D%27%3E%27&propertyName=Volcano_Number,Volcano_Name,"
    "StartDateYear,StartDateMonth,StartDateDay,EndDateYearModifier,EndDateYear,EndDateMonth,"
    "EndDateDay"
)


@dataclass
class Continuing:
    numbers: frozenset[int]
    through: datetime


def parse_continuing(payload: dict[str, Any]) -> Continuing:
    numbers = set()
    newest: datetime | None = None
    for feature in payload.get("features") or []:
        p = feature.get("properties") or {}
        if p.get("EndDateYearModifier") != ">":
            continue
        if p.get("Volcano_Number") is not None:
            numbers.add(int(p["Volcano_Number"]))
        year = B.number(p.get("EndDateYear"))
        if year is None:
            continue
        month = int(B.number(p.get("EndDateMonth")) or 1)
        day = int(B.number(p.get("EndDateDay")) or 1)
        try:
            when = datetime(int(year), max(1, month), max(1, day), tzinfo=UTC)
        except ValueError:
            continue
        newest = when if newest is None or when > newest else newest
    if newest is None:
        raise fetch.FetchError(
            "upstream_parse", "webservices.volcano.si.edu: no continuing eruption"
        )
    return Continuing(frozenset(numbers), newest)


@dataclass
class Volcanoes:
    rows: list[dict[str, Any]]
    continuing: Continuing

    @property
    def nbytes(self) -> int:
        return 300 * len(self.rows)


def volcano_block(v: Volcanoes, ctx: Context) -> B.Block:
    found = []
    for p in v.rows:
        lat, lon = B.number(p.get("Latitude")), B.number(p.get("Longitude"))
        if lat is None or lon is None:
            continue
        d = geo.haversine_km(ctx.lat, ctx.lon, lat, lon)
        if d <= ctx.radius_km:
            found.append((d, p))
    found.sort(key=lambda x: x[0])
    rows = []
    for d, p in found[:10]:
        number = int(p["Volcano_Number"]) if p.get("Volcano_Number") is not None else None
        year = B.number(p.get("Last_Eruption_Year"))
        elevation = B.number(p.get("Elevation"))
        rows.append(
            {
                "number": number,
                "name": p.get("Volcano_Name"),
                "type": p.get("Primary_Volcano_Type"),
                # negative = BCE; null = unknown, never 0
                "last_eruption_year": int(year) if year is not None else None,
                "continuing": number in v.continuing.numbers if number is not None else False,
                "elevation_m": int(elevation) if elevation is not None else None,
                "country": p.get("Country"),
                "distance_km": round(d),
            }
        )
    source = B.Source(
        SRC_GVP.name,
        SRC_GVP.url,
        f"{SRC_GVP.attribution} ({B.years_of(v.continuing.through)})",
        SRC_GVP.licence,
    )
    data = {"total": len(found), "volcanoes": rows, "derived": ["distance_km", "continuing"]}
    return B.ok(SPEC_VOLC, v.continuing.through, data, source=source)


async def build_volcanoes(ctx: Context) -> B.Block:
    clat, clon = cell_of(ctx)

    async def load_continuing(previous: fetch.Entry) -> fetch.Loaded:
        response = await fetch.get(GVP_CONTINUING)
        return fetch.Loaded(parse_continuing(response.json()), 86400.0)

    async def load(previous: fetch.Entry) -> fetch.Loaded:
        cont = await fetch.obtain(_probe_cache, "gvp:continuing", load_continuing, ctx.claim)
        if cont.value is None:
            assert cont.error is not None
            raise cont.error
        rows: list[dict[str, Any]] = []
        south, north, west, east = geo.box(clat, clon, 300.0 + HALF_DIAGONAL_KM)
        for w, e in geo.split_lon(west, east):
            payload = (await fetch.get(GVP_VOLCANOES.format(w=w, s=south, e=e, n=north))).json()
            if not isinstance(payload, dict) or "features" not in payload:
                raise fetch.FetchError("upstream_parse", "webservices.volcano.si.edu: no features")
            rows.extend((f.get("properties") or {}) for f in payload["features"])
        value = Volcanoes(rows, cont.value)
        return fetch.Loaded(value, 7 * 86400.0, size=value.nbytes)

    entry = await fetch.obtain(_gvp_cache, (clat, clon), load, ctx.claim)
    return serve(SPEC_VOLC, entry, lambda v: volcano_block(v, ctx), ttl_s=7 * 86400.0)
