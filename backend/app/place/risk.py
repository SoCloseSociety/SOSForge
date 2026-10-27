"""`drought`, `fire.hotspots`, `fire.danger`, `risk.water`, `risk.country`.

- `drought`: Copernicus GDO point time series (SPI-3, soil moisture
  anomaly, risk of drought impact for agriculture), each indicator the LAST
  element of its own series. A null there stays null: an older dekad is
  never passed off as the current one. `at_sea` only when the soil series is
  empty AND every rdria is null AND our table says sea (Reykjavik's rdria is
  null on a dekad where its SPI is perfectly valid).
- `fire.hotspots`: NASA FIRMS VIIRS (NOAA-20 and NOAA-21) global 24 h files.
  The "24h" files measured a 27 h spread, so only rows acquired within the
  24 h before the newest acquisition count, and the window is exposed. 0
  means "none detected in that window": clouds hide fires.
- `fire.danger`: GWIS ECMWF Fire Weather Index, read as the colour of the
  centre pixel of a 9x9 WMS tile CENTRED ON THE REQUESTER'S POINT (0.01 deg
  per pixel) against the layer's legend. WMS samples a pixel at its centre:
  the 2 deg tile of a 0.25 deg cell this used to read put that centre up to
  16.7 km away -- open sea for Ko Samui (called `masked` while GWIS renders
  "low" on the island), the wrong class for Lima. The layer is
  `queryable=0`: GetFeatureInfo is not offered. GWIS states no run time: a
  stalled pipeline still renders "today", so the horizon is checked too
  (today+7 must render; the measured horizon is +9 days).
- `risk.water`: WRI Aqueduct 4.0 annual indicators of the river sub-basin,
  WRI's own labels kept verbatim (the scale differs by indicator: drr cat 3
  is "Medium - High (0.6-0.8)", rfr cat 3 is "High (6 in 1,000 to 1 in
  100)"). `as_of` is the response's Last-Modified, read at run time.
- `risk.country`: the INFORM Risk index of the country, always labelled as a
  national score. The release is the latest global workflow group of the
  INFORM listing, never a name written here.
"""

from __future__ import annotations

import csv
import io
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import numpy as np

from app.place import block as B
from app.place import fetch, geo, png
from app.place.context import Context
from app.place.weather import serve

# ------------------------------------------------------------------ sources

SRC_GDO = B.Source(
    "Copernicus Global Drought Observatory",
    "https://drought.emergency.copernicus.eu/",
    "Copernicus Emergency Management Service, Global Drought Observatory (GDO)",
    "CC BY 4.0",
)
SRC_FIRMS = B.Source(
    "NASA FIRMS, VIIRS NOAA-20 and NOAA-21",
    "https://firms.modaps.eosdis.nasa.gov/",
    "NASA FIRMS (LANCE), VIIRS 375 m active fire product; rings and window computed by SOSForge",
    "NASA open data, no restriction (attribution requested)",
)
SRC_GWIS = B.Source(
    "GWIS Fire Weather Index (ECMWF)",
    "https://gwis.jrc.ec.europa.eu/",
    "Copernicus Emergency Management Service, Global Wildfire Information System (GWIS), "
    "ECMWF fire danger forecast",
    "CC BY 4.0",
)
SRC_AQUEDUCT = B.Source(
    "WRI Aqueduct 4.0",
    "https://www.wri.org/aqueduct",
    "World Resources Institute, Aqueduct 4.0 water risk indicators",
    "CC BY 4.0",
)
SRC_INFORM = B.Source(
    "INFORM Risk Index",
    "https://drmkc.jrc.ec.europa.eu/inform-index",
    "INFORM Risk Index, European Commission Joint Research Centre (DRMKC)",
    "CC BY 4.0",
)

SPEC_DROUGHT = B.Spec("drought", "monitoring", "period_start", 35 * 86400.0, SRC_GDO)
SPEC_HOTSPOTS = B.Spec("fire.hotspots", "observation", "through", 12 * 3600.0, SRC_FIRMS)
SPEC_DANGER = B.Spec("fire.danger", "forecast", "valid_for", 86400.0, SRC_GWIS)
SPEC_WATER = B.Spec("risk.water", "static", "published", None, SRC_AQUEDUCT)
SPEC_COUNTRY = B.Spec("risk.country", "static", "published", 400 * 86400.0, SRC_INFORM)

# ------------------------------------------------------------------ drought

GDO_URL = (
    "https://drought.emergency.copernicus.eu/services/webmap/hst?lon={lon:.2f}&lat={lat:.2f}"
    "&appCode=gdo_map&srvs={srv}&inds={inds}&date_ini={start}&date_end={end}"
)
GDO_SERIES = {
    "spi": ("spaST", "spa01,spa03", "spa03"),
    "smang": ("smang", "smang", "smang"),
    "rdria": ("rdria", "rdria", "rdria"),
}
_gdo_cache = fetch.Cache("drought.emergency.copernicus.eu")

RDRIA_LABELS = {0: "none", 1: "low", 2: "medium", 3: "high"}


def spi_class(value: float | None) -> str | None:
    """SPI classes (McKee et al. 1993, as GDO's legend uses them)."""
    if value is None:
        return None
    if value <= -2.0:
        return "extremely_dry"
    if value <= -1.5:
        return "severely_dry"
    if value <= -1.0:
        return "moderately_dry"
    if value < 1.0:
        return "normal"
    if value < 1.5:
        return "moderately_wet"
    if value < 2.0:
        return "very_wet"
    return "extremely_wet"


def last_of(payload: dict[str, Any], key: str) -> tuple[float | None, str | None, int]:
    """(the last value of the series, its period start, the series length)."""
    dates = payload.get("dates") or []
    values = payload.get(key) or []
    if not dates or len(values) != len(dates):
        return None, None, 0
    return B.number(values[-1]), str(dates[-1]), len(dates)


def drought_block(
    spi: dict[str, Any], smang: dict[str, Any], rdria: dict[str, Any], ctx: Context
) -> B.Block:
    spi_v, spi_d, _ = last_of(spi, "spa03")
    sm_v, sm_d, sm_n = last_of(smang, "smang")
    rd_v, rd_d, _ = last_of(rdria, "rdria")
    rdria_all_null = all(B.number(v) is None for v in rdria.get("rdria") or [])
    # measured at the mid-Pacific cell centre: dates with null values, where
    # the exact point answered an empty series -- both mean "no land value"
    soil_empty = sm_n == 0 or all(B.number(v) is None for v in smang.get("smang") or [])
    if soil_empty and rdria_all_null and ctx.place.at_sea:
        # all three signals: no soil series, no agricultural risk, our table says sea
        return B.not_applicable(SPEC_DROUGHT, "at_sea", "no soil or agricultural series at sea")
    periods = [datetime.fromisoformat(d).replace(tzinfo=UTC) for d in (spi_d, sm_d, rd_d) if d]
    if not periods:
        return B.unavailable(SPEC_DROUGHT, "masked", "GDO publishes no indicator at this point")
    rd_class = int(rd_v) if rd_v is not None else None
    data = {
        "cell": dict(zip(("lat", "lon"), geo.cell_key(ctx.lat, ctx.lon, 0.1), strict=True)),
        "spi_3": {"value": spi_v, "period_start": spi_d, "class": spi_class(spi_v)},
        "soil_moisture_anomaly": {"value": sm_v, "period_start": sm_d},
        "agri_risk": {
            "class": rd_class,
            "label": RDRIA_LABELS.get(rd_class) if rd_class is not None else None,
            "period_start": rd_d,
        },
    }
    oldest = min(periods)
    source = B.Source(
        SRC_GDO.name,
        SRC_GDO.url,
        f"{SRC_GDO.attribution}, {B.years_of(oldest)}",
        SRC_GDO.licence,
    )
    return B.ok(SPEC_DROUGHT, oldest, data, source=source)


async def build_drought(ctx: Context) -> B.Block:
    clat, clon = geo.cell_key(ctx.lat, ctx.lon, 0.1)
    today = ctx.now.date()
    start = (today - timedelta(days=120)).isoformat()
    entries = {}
    for name, (srv, inds, _) in GDO_SERIES.items():
        url = GDO_URL.format(lon=clon, lat=clat, srv=srv, inds=inds, start=start, end=today)

        async def load(previous: fetch.Entry, url: str = url) -> fetch.Loaded:
            response = await fetch.get(url)
            payload = response.json()
            if not isinstance(payload, dict) or "dates" not in payload:
                raise fetch.FetchError("upstream_parse", "drought.emergency.copernicus.eu shape")
            return fetch.Loaded(payload, 6 * 3600.0)

        entries[name] = await fetch.obtain(_gdo_cache, (clat, clon, name), load, ctx.claim)
    for entry in entries.values():
        if entry.value is None:
            assert entry.error is not None
            return B.unavailable(SPEC_DROUGHT, entry.error.code, entry.error.reason)
    spi, sm, rd = (entries[k] for k in ("spi", "smang", "rdria"))
    result = serve(SPEC_DROUGHT, spi, lambda v: drought_block(v, sm.value, rd.value, ctx))
    fetched = [e.fetched_at for e in entries.values() if e.fetched_at]
    result.fetched_at = min(fetched) if fetched else None
    return result


# ------------------------------------------------------------- fire hotspots

FIRMS_FILES = {
    "N20": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/"
    "J1_VIIRS_C2_Global_24h.csv",
    "N21": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-21-viirs-c2/csv/"
    "J2_VIIRS_C2_Global_24h.csv",
}
RINGS_KM = (25, 50, 100)
_firms_cache = fetch.Cache("firms.modaps.eosdis.nasa.gov", 2)


@dataclass
class Hotspots:
    lat: np.ndarray
    lon: np.ndarray
    t: np.ndarray  # epoch seconds
    frp: np.ndarray
    confidence: list[str]
    satellite: list[str]
    cells: dict[tuple[int, int], np.ndarray]
    newest: datetime


def parse_firms(text: str) -> Hotspots:
    reader = csv.DictReader(io.StringIO(text))
    lat, lon, t, frp, conf, sat = [], [], [], [], [], []
    for row in reader:
        try:
            hhmm = row["acq_time"].zfill(4)
            when = datetime.strptime(f"{row['acq_date']} {hhmm}", "%Y-%m-%d %H%M").replace(
                tzinfo=UTC
            )
            la, lo = float(row["latitude"]), float(row["longitude"])
        except (KeyError, ValueError):
            continue
        lat.append(la)
        lon.append(lo)
        t.append(when.timestamp())
        value = B.number(row.get("frp"))
        frp.append(value if value is not None else math.nan)
        conf.append(row.get("confidence") or "")
        sat.append(row.get("satellite") or "")
    if not t:
        # no row, no as-of: the file says nothing about when it was made
        raise fetch.FetchError("upstream_parse", "firms.modaps.eosdis.nasa.gov file with no row")
    lat_a, lon_a = np.asarray(lat), np.asarray(lon)
    cells: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, (la, lo) in enumerate(zip(lat_a, lon_a, strict=True)):
        cells[(math.floor(la), math.floor(lo))].append(i)
    t_a = np.asarray(t)
    return Hotspots(
        lat_a,
        lon_a,
        t_a,
        np.asarray(frp),
        conf,
        sat,
        {k: np.asarray(v) for k, v in cells.items()},
        datetime.fromtimestamp(float(np.max(t_a)), UTC),
    )


def hotspot_block(files: dict[str, Hotspots], ctx: Context) -> B.Block:
    end = min(h.newest for h in files.values())
    start = end - timedelta(hours=24)
    counts = dict.fromkeys(RINGS_KM, 0)
    nearest: tuple[float, str, int] | None = None
    span = 1 + int(max(RINGS_KM) / (111.32 * max(0.05, math.cos(math.radians(ctx.lat)))))
    for name, h in files.items():
        idx_parts = []
        for dy in (-1, 0, 1):
            for dx in range(-span, span + 1):
                cell = (math.floor(ctx.lat) + dy, ((math.floor(ctx.lon) + dx + 180) % 360) - 180)
                if cell in h.cells:
                    idx_parts.append(h.cells[cell])
        if not idx_parts:
            continue
        idx = np.concatenate(idx_parts)
        inside = (h.t[idx] > start.timestamp()) & (h.t[idx] <= end.timestamp())
        for i in idx[inside]:
            km = geo.haversine_km(ctx.lat, ctx.lon, float(h.lat[i]), float(h.lon[i]))
            for ring in RINGS_KM:
                if km <= ring:
                    counts[ring] += 1
            if km <= max(RINGS_KM) and (nearest is None or km < nearest[0]):
                nearest = (km, name, int(i))
    near = None
    if nearest is not None:
        km, name, i = nearest
        h = files[name]
        frp = float(h.frp[i])
        near = {
            "distance_km": round(km, 1),
            "time": B.iso(datetime.fromtimestamp(float(h.t[i]), UTC)),
            "frp_mw": None if math.isnan(frp) else round(frp, 2),
            "confidence": h.confidence[i] or None,
            "satellite": h.satellite[i] or name,
        }
    data = {
        "window_start": B.iso(start),
        "window_end": B.iso(end),
        "within_km": {str(r): counts[r] for r in RINGS_KM},
        "nearest": near,
        "derived": ["within_km", "nearest"],
    }
    return B.ok(SPEC_HOTSPOTS, end, data)


async def build_hotspots(ctx: Context) -> B.Block:
    entries = {}
    for name, url in FIRMS_FILES.items():

        async def load(previous: fetch.Entry, url: str = url) -> fetch.Loaded:
            response = await fetch.get(url)
            parsed = await fetch.in_thread(parse_firms, response.text)
            return fetch.Loaded(parsed, 3600.0, size=len(response.content))

        entries[name] = await fetch.obtain(
            _firms_cache,
            name,
            load,
            ctx.claim,
            swr=True,
            on_first_wait=ctx.waiting(SPEC_HOTSPOTS.id, "warming"),
            on_stale=ctx.serving_stale(SPEC_HOTSPOTS.id),
        )
    for entry in entries.values():
        if entry.value is None:
            assert entry.error is not None
            return B.unavailable(SPEC_HOTSPOTS, entry.error.code, entry.error.reason)
    files = {name: e.value for name, e in entries.items()}
    first = next(iter(entries.values()))
    result = serve(SPEC_HOTSPOTS, first, lambda _: hotspot_block(files, ctx), ttl_s=3600.0)
    errors = [e.error for e in entries.values() if e.error is not None]
    if result.status == "ok" and errors and result.error is None:
        result.error = B.Problem(errors[0].code, errors[0].reason)
    fetched = [e.fetched_at for e in entries.values() if e.fetched_at]
    result.fetched_at = min(fetched) if fetched else None
    return result


# --------------------------------------------------------------- fire danger

GWIS_URL = (
    "https://maps.effis.emergency.copernicus.eu/gwis?service=WMS&version=1.3.0&request=GetMap"
    "&layers=ecmwf.fwi&styles=&crs=EPSG:4326&bbox={s:.3f},{w:.3f},{n:.3f},{e:.3f}"
    "&width={px}&height={py}&format=image/png&transparent=true&time={day}"
)
TILE = 9
PIXEL_DEG = 0.01  # the report's own rounding: one pixel per rounded point
# the legend of `ecmwf.fwi` (GetLegendGraphic, captured 2026-10-27): swatch
# colour -> class, FWI range
FWI_LEGEND: dict[tuple[int, int, int], tuple[str, float | None, float | None]] = {
    (156, 255, 192): ("low", None, 11.2),
    (205, 226, 78): ("moderate", 11.2, 21.3),
    (230, 172, 0): ("high", 21.3, 38.0),
    (217, 112, 16): ("very_high", 38.0, 50.0),
    (173, 6, 14): ("extreme", 50.0, 70.0),
    (58, 0, 21): ("very_extreme", 70.0, None),
}
_gwis_tile_cache = fetch.Cache("maps.effis.emergency.copernicus.eu tiles")
_gwis_probe_cache = fetch.Cache("maps.effis.emergency.copernicus.eu probes", 64)


def tile_box(lat: float, lon: float) -> tuple[float, float, float, float, tuple[float, float]]:
    """(south, north, west, east, key) of a TILE x TILE tile whose centre
    pixel is centred on the (rounded) point itself: an odd tile, PIXEL_DEG
    per pixel, so the pixel WMS samples at its centre IS the point."""
    half = TILE * PIXEL_DEG / 2
    return lat - half, lat + half, lon - half, lon + half, (lat, lon)


def pixel_at(
    rows: list[list[png.Pixel]],
    south: float,
    north: float,
    west: float,
    east: float,
    lat: float,
    lon: float,
) -> png.Pixel:
    """The pixel holding (lat, lon) inside a tile of that box."""
    h, w = len(rows), len(rows[0])
    x = min(w - 1, max(0, int((lon - west) / (east - west) * w)))
    y = min(h - 1, max(0, int((north - lat) / (north - south) * h)))
    return rows[y][x]


def fwi_class(pixel: png.Pixel) -> tuple[str, float | None, float | None] | None:
    """None for a transparent pixel (no value there)."""
    if pixel[3] == 0:
        return None
    rgb = pixel[:3]
    if rgb in FWI_LEGEND:
        return FWI_LEGEND[rgb]
    # resampled edges: the nearest legend colour
    best = min(FWI_LEGEND, key=lambda c: sum((a - b) ** 2 for a, b in zip(c, rgb, strict=True)))
    return FWI_LEGEND[best]


def has_colour(data: bytes) -> bool:
    return any(p[3] for row in png.decode(data) for p in row)


async def _gwis(cache: fetch.Cache, key: Any, url: str, ctx: Context) -> bytes:
    async def load(previous: fetch.Entry) -> fetch.Loaded:
        response = await fetch.get(url)
        if not response.content.startswith(png.SIGNATURE):
            raise fetch.FetchError(
                "upstream_parse", "maps.effis.emergency.copernicus.eu: not a PNG"
            )
        return fetch.Loaded(response.content, 3 * 3600.0, size=len(response.content))

    entry = await fetch.obtain(cache, key, load, ctx.claim)
    if entry.value is None:
        assert entry.error is not None
        raise entry.error
    value: bytes = entry.value
    return value


async def _probe(day: date, ctx: Context) -> bool:
    url = GWIS_URL.format(s=-90.0, w=-180.0, n=90.0, e=180.0, px=36, py=18, day=day.isoformat())
    data = await _gwis(_gwis_probe_cache, day, url, ctx)
    return await fetch.in_thread(has_colour, data)


async def build_danger(ctx: Context) -> B.Block:
    today = ctx.now.astimezone(UTC).date()
    if not await _probe(today, ctx):
        return B.unavailable(
            SPEC_DANGER, "stale_upstream", f"GWIS renders no fire danger anywhere for {today}"
        )
    horizon = today + timedelta(days=7)
    horizon_ok = await _probe(horizon, ctx)
    south, north, west, east, key = tile_box(ctx.lat, ctx.lon)
    days = []
    for k in range(3):
        day = today + timedelta(days=k)
        url = GWIS_URL.format(
            s=south, w=west, n=north, e=east, px=TILE, py=TILE, day=day.isoformat()
        )
        tile = await _gwis(_gwis_tile_cache, (key, day), url, ctx)
        rows = await fetch.in_thread(png.decode, tile)
        found = fwi_class(pixel_at(rows, south, north, west, east, ctx.lat, ctx.lon))
        days.append(
            {
                "date": day.isoformat(),
                "class": found[0] if found else None,
                "fwi_min": found[1] if found else None,
                "fwi_max": found[2] if found else None,
            }
        )
    if days[0]["class"] is None:
        if ctx.place.at_sea:
            return B.not_applicable(SPEC_DANGER, "at_sea", "no fire danger is computed at sea")
        return B.unavailable(SPEC_DANGER, "masked", "GWIS renders no fire danger at this point")
    as_of = datetime(today.year, today.month, today.day, tzinfo=UTC)
    data: dict[str, Any] = {
        "days": days,
        "horizon_end": horizon.isoformat() if horizon_ok else None,
        "derived": ["class"],
    }
    source = B.Source(
        SRC_GWIS.name,
        SRC_GWIS.url,
        f"{SRC_GWIS.attribution}, {B.years_of(as_of)}",
        SRC_GWIS.licence,
    )
    # a run three or more days old still renders today: the horizon says so
    return B.ok(SPEC_DANGER, as_of, data, source=source, stale_rule=not horizon_ok)


# --------------------------------------------------------------- risk.water

CARTO_URL = "https://wri-rw.carto.com/api/v2/sql"
AQUEDUCT_SQL = (
    "SELECT s.aq30_id, s.pfaf_id, s.bws_raw, s.bws_cat, s.bws_label, "
    "s.rfr_raw, s.rfr_cat, s.rfr_label, "
    "s.cfr_raw, s.cfr_cat, s.cfr_label, s.drr_raw, s.drr_cat, s.drr_label "
    "FROM water_risk_indicators_annual_aqueduct40 s "
    "JOIN y2018m12d06_rh_master_shape_v01 r ON s.aq30_id = r.aq30_id "
    "WHERE ST_Intersects(r.the_geom, ST_SetSRID(ST_Point({lon}, {lat}), 4326))"
)
WATER_INDICATORS = {
    "riverine_flood": "rfr",
    "coastal_flood": "cfr",
    "drought_risk": "drr",
    "water_stress": "bws",
}
SCALE_KEYS = frozenset(
    {"low", "low_medium", "medium", "medium_high", "high", "extremely_high", "arid_low_water_use"}
)
_water_cache = fetch.Cache("wri-rw.carto.com")


def water_scale(label: str | None) -> str | None:
    """WRI's label -> a scale key: "Medium - High (0.6-0.8)" -> medium_high.
    "No Data" is no value."""
    if not label or label.strip() in B.SENTINEL_TEXT:
        return None
    head = re.sub(r"\s*\(.*\)\s*$", "", label).strip().lower()
    if head == "arid and low water use":
        return "arid_low_water_use"
    key = re.sub(r"\s*-\s*", "_", head).replace(" ", "_")
    return key if key in SCALE_KEYS else None


def water_block(rows: list[dict[str, Any]], modified: datetime, ctx: Context) -> B.Block:
    if not rows:
        if ctx.place.at_sea:
            return B.not_applicable(SPEC_WATER, "at_sea", "no Aqueduct sub-basin here")
        return B.unavailable(SPEC_WATER, "masked", "no Aqueduct sub-basin contains this point")
    rows = sorted(rows, key=lambda r: r.get("aq30_id") or 0)
    row = rows[0]
    data: dict[str, Any] = {"basin_pfaf_id": row.get("pfaf_id")}
    for name, prefix in WATER_INDICATORS.items():
        label = row.get(f"{prefix}_label")
        label = None if label is None or label.strip() in B.SENTINEL_TEXT else label
        cat = B.number(row.get(f"{prefix}_cat"))
        data[name] = {
            "raw": B.number(row.get(f"{prefix}_raw")),
            "cat": int(cat) if cat is not None else None,
            "label": label,
            "scale": water_scale(label),
        }
    missing = []
    if len(rows) > 1:
        missing.append({"field": "basin_pfaf_id", "reason": "ambiguous"})
    return B.ok(SPEC_WATER, modified, data, missing=missing)


async def build_water(ctx: Context) -> B.Block:
    # two floats formatted here and nowhere else go into the SQL
    sql = AQUEDUCT_SQL.format(lon=f"{ctx.lon:.2f}", lat=f"{ctx.lat:.2f}")

    async def load(previous: fetch.Entry) -> fetch.Loaded:
        from urllib.parse import quote

        response = await fetch.get(f"{CARTO_URL}?q={quote(sql)}")
        payload = response.json()
        if not isinstance(payload, dict) or "rows" not in payload:
            raise fetch.FetchError("upstream_parse", "wri-rw.carto.com answer without rows")
        try:
            modified = parsedate_to_datetime(response.headers["last-modified"]).astimezone(UTC)
        except (KeyError, TypeError, ValueError):
            raise fetch.FetchError(
                "upstream_parse", "wri-rw.carto.com answer without Last-Modified"
            ) from None
        return fetch.Loaded((payload["rows"], modified), 30 * 86400.0)

    entry = await fetch.obtain(_water_cache, (ctx.lat, ctx.lon), load, ctx.claim)
    return serve(SPEC_WATER, entry, lambda v: water_block(v[0], v[1], ctx), ttl_s=30 * 86400.0)


# ------------------------------------------------------------- risk.country

INFORM = "https://drmkc.jrc.ec.europa.eu/inform-index/API/InformAPI"
INFORM_GROUPS = f"{INFORM}/workflows/WorkflowGroups"
INFORM_GROUP = f"{INFORM}/workflows/GetByWorkflowGroup/{{name}}"
INFORM_SCORES = (
    f"{INFORM}/Countries/Scores/?WorkflowId={{wf}}&Iso3={{iso3}}"
    "&IndicatorId=INFORM,HA,HA.NAT,HA.NAT.EQ,HA.NAT.FL,HA.NAT.CFL,HA.NAT.TC,HA.NAT.DR,"
    "HA.NAT.TS,HA.NAT.EPI,VU,CC"
)
INFORM_HAZARDS = {
    "earthquake": "HA.NAT.EQ",
    "river_flood": "HA.NAT.FL",
    "coastal_flood": "HA.NAT.CFL",
    "tropical_cyclone": "HA.NAT.TC",
    "drought": "HA.NAT.DR",
    "tsunami": "HA.NAT.TS",
    "epidemic": "HA.NAT.EPI",
}
_GLOBAL_GROUP = re.compile(r"^INFORM(\d{4})(Mid|_Mid|_2nd)?$")
_inform_cache = fetch.Cache("drmkc.jrc.ec.europa.eu")


def latest_groups(names: list[str]) -> list[str]:
    """The global INFORM Risk releases, newest first (regional ones such as
    INFORM_SAHEL are left out): year, then January < second < mid-year."""
    rank = {None: 0, "_2nd": 1, "Mid": 2, "_Mid": 2}
    found = []
    for name in names:
        m = _GLOBAL_GROUP.match(name)
        if m:
            found.append(((int(m.group(1)), rank[m.group(2)]), name))
    return [name for _, name in sorted(found, reverse=True)]


def country_block(
    scores: list[dict[str, Any]], workflow: dict[str, Any], iso3: str, ctx: Context
) -> B.Block:
    if not scores:
        return B.unavailable(SPEC_COUNTRY, "no_coverage", f"INFORM has no score for {iso3}")
    by_id = {s.get("IndicatorId"): B.number(s.get("IndicatorScore")) for s in scores}
    published = datetime.fromisoformat(str(workflow["FlagGnaPublished"])).replace(tzinfo=UTC)
    data = {
        "iso3": iso3,
        "release": workflow.get("Name"),
        "inform": by_id.get("INFORM"),
        "hazard_exposure": by_id.get("HA"),
        "natural_hazard": by_id.get("HA.NAT"),
        "vulnerability": by_id.get("VU"),
        "lack_of_coping_capacity": by_id.get("CC"),
        "hazards": {name: by_id.get(code) for name, code in INFORM_HAZARDS.items()},
        "country_basis": ctx.place.country_basis,
    }
    return B.ok(SPEC_COUNTRY, published, data)


async def _inform_entry(key: Any, url: str, ttl: float, ctx: Context) -> fetch.Entry:
    async def load(previous: fetch.Entry) -> fetch.Loaded:
        response = await fetch.get(url)
        return fetch.Loaded(response.json(), ttl)

    entry = await fetch.obtain(_inform_cache, key, load, ctx.claim)
    if entry.value is None:
        assert entry.error is not None
        raise entry.error
    return entry


async def _inform_json(key: Any, url: str, ttl: float, ctx: Context) -> Any:
    return (await _inform_entry(key, url, ttl, ctx)).value


async def build_country(ctx: Context) -> B.Block:
    code = ctx.place.country_code
    if not code:
        return B.not_applicable(SPEC_COUNTRY, "no_country", "the point is in no country")
    iso3 = geo.iso3(code)
    if not iso3:
        return B.unavailable(SPEC_COUNTRY, "no_coverage", f"no ISO alpha-3 code for {code}")
    groups = await _inform_json("groups", INFORM_GROUPS, 86400.0, ctx)
    workflow = None
    for name in latest_groups(list(groups) if isinstance(groups, list) else []):
        found = await _inform_json(("group", name), INFORM_GROUP.format(name=name), 86400.0, ctx)
        if isinstance(found, list) and found and found[0].get("FlagGnaPublished"):
            workflow = found[0]
            break
    if workflow is None:
        return B.unavailable(SPEC_COUNTRY, "upstream_parse", "no published INFORM Risk release")
    entry = await _inform_entry(
        ("scores", workflow["WorkflowId"], iso3),
        INFORM_SCORES.format(wf=workflow["WorkflowId"], iso3=iso3),
        86400.0,
        ctx,
    )
    if not isinstance(entry.value, list):
        raise fetch.FetchError("upstream_parse", "drmkc.jrc.ec.europa.eu scores not a list")
    block = country_block(entry.value, workflow, iso3, ctx)
    block.fetched_at = entry.fetched_at
    return block
